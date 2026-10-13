from datetime import timedelta

from django.test import RequestFactory, TestCase
from django.utils import timezone

from accounts.services import IdentityService
from todos.models import Todo
from todos.schemas import TodoReopenSchema, TodoReorderSchema, TodoTitleSchema
from todos.services import TodoService


class TodoServiceTests(TestCase):
    def setUp(self):
        self.user = IdentityService.create_user("a@x.com")
        self.todos = [TodoService.create(self.user, TodoTitleSchema(title=f"todo {i}")) for i in (1, 2, 3)]

    def positions(self):
        return list(TodoService.open_for(self.user).values_list("title", "position"))

    def test_close_keeps_its_place_for_the_day(self):
        removed = TodoService.close(self.todos[0])
        self.assertEqual((removed.index, removed.removal), (1, Todo.Removal.CLOSED))
        self.assertEqual(list(TodoService.ordered_for(self.user).values_list("title", "position")), [("todo 1", 1), ("todo 2", 2), ("todo 3", 3)])

    def test_a_new_day_closes_the_gap(self):
        TodoService.close(self.todos[0])
        Todo.objects.filter(pk=self.todos[0].pk).update(closed_at=timezone.now() - timedelta(days=1))
        TodoService.create(self.user, TodoTitleSchema(title="todo 4"))
        self.assertEqual(self.positions(), [("todo 2", 1), ("todo 3", 2), ("todo 4", 3)])

    def test_reopen_puts_it_back_in_place(self):
        TodoService.close(self.todos[0])
        TodoService.reopen(Todo.objects.get(pk=self.todos[0].pk), TodoReopenSchema(index=1, select=False))
        self.assertEqual(self.positions(), [("todo 1", 1), ("todo 2", 2), ("todo 3", 3)])

    def test_delete_is_undoable_and_never_counts_as_done(self):
        TodoService.delete(self.todos[0])
        self.assertNotIn(self.todos[0], TodoService.reviewed(self.user))
        TodoService.reopen(Todo.objects.get(pk=self.todos[0].pk), TodoReopenSchema(index=1, select=False))
        self.assertEqual([title for title, _ in self.positions()], ["todo 1", "todo 2", "todo 3"])

    def test_reorder_applies_the_order_and_keeps_unmentioned_todos_last(self):
        TodoService.reorder(self.user, TodoReorderSchema(ids=[self.todos[2].id, self.todos[0].id]))
        self.assertEqual([title for title, _ in self.positions()], ["todo 3", "todo 1", "todo 2"])

    def test_transfer_appends_to_the_recipients_list(self):
        other = IdentityService.create_user("b@x.com")
        TodoService.create(other, TodoTitleSchema(title="theirs"))
        TodoService.transfer(self.user, other)
        self.assertEqual([title for title, _ in TodoService.open_for(other).values_list("title", "position")],
                         ["theirs", "todo 1", "todo 2", "todo 3"])


class ReviewQueryTests(TestCase):
    def test_reviewed_is_only_closed_todos(self):
        user = IdentityService.create_user("a@x.com")
        done, gone, _ = (TodoService.create(user, TodoTitleSchema(title=t)) for t in ("done", "gone", "open"))
        TodoService.close(done)
        TodoService.delete(gone)
        self.assertEqual([t.title for t in TodoService.reviewed(user)], ["done"])


class TodoSchemaTests(TestCase):
    def schema(self, title):
        request = RequestFactory().post("/", {"title": title})
        request._messages = _Messages()
        return TodoTitleSchema.from_post(request), request._messages

    def test_blank_and_overlong_titles_are_rejected_with_a_message(self):
        for title in ("   ", "x" * 301):
            data, messages = self.schema(title)
            self.assertFalse(data.is_valid())
            self.assertEqual(len(messages.added), 1)

    def test_title_is_stripped(self):
        data, _ = self.schema("  ship it  ")
        self.assertTrue(data.is_valid())
        self.assertEqual(data.title, "ship it")


class TodoPermissionTests(TestCase):
    def test_only_the_owner_has_todo_permissions(self):
        owner = IdentityService.create_user("a@x.com")
        stranger = IdentityService.create_user("b@x.com")
        todo = TodoService.create(owner, TodoTitleSchema(title="mine"))
        for perm in ("todos.view_todo", "todos.change_todo", "todos.delete_todo"):
            self.assertTrue(owner.has_perm(perm, todo))
            self.assertFalse(stranger.has_perm(perm, todo))


class _Messages:
    """Minimal message storage so schemas can be exercised without the middleware."""

    def __init__(self):
        self.added = []

    def add(self, level, message, extra_tags=""):
        self.added.append(message)




class TodayListTests(TestCase):
    """Today's list: the user's tasks in their order, then today's meetings by time."""

    def setUp(self):
        self.user = IdentityService.create_user("t@x.com", show_meetings=True)
        self.todos = [TodoService.create(self.user, TodoTitleSchema(title=f"todo {i}")) for i in (1, 2)]

    def meeting(self, title, hours):
        start = (timezone.now() + timedelta(hours=hours)).replace(second=0, microsecond=0)
        return Todo.objects.create(user=self.user, kind=Todo.Kind.MEETING, title=title, starts_at=start, ends_at=start + timedelta(minutes=30))

    def listed(self):
        return list(TodoService.listed_for(self.user).values_list("title", flat=True))

    def test_every_meeting_today_is_listed_on_top_and_an_ended_one_is_done(self):
        today_noon = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
        midnight = TodoService.start_of_today()
        Todo.objects.create(user=self.user, kind=Todo.Kind.MEETING, title="tomorrow", starts_at=today_noon + timedelta(days=1), ends_at=today_noon + timedelta(days=1, hours=1))
        Todo.objects.create(user=self.user, kind=Todo.Kind.MEETING, title="yesterday", starts_at=midnight - timedelta(hours=3), ends_at=midnight - timedelta(hours=2))
        over = Todo.objects.create(user=self.user, kind=Todo.Kind.MEETING, title="over", starts_at=midnight, ends_at=midnight + timedelta(seconds=1))
        TodoService.place_todays_meetings(self.user)
        TodoService.close_ended_meetings(self.user)
        listed = self.listed()
        self.assertEqual(listed, ["over", "todo 1", "todo 2"])  # ended today: still on the list (on top), done
        self.assertNotIn("tomorrow", listed)
        self.assertNotIn("yesterday", listed)  # gone to Past
        over.refresh_from_db()
        self.assertEqual(over.closed_at, over.ends_at)
        self.assertIn("yesterday", list(TodoService.reviewed(self.user).values_list("title", flat=True)))

    def test_a_new_day_opens_with_its_meetings_then_scheduled_then_carry_overs(self):
        start = TodoService.start_of_today() + timedelta(hours=23)
        later = Todo.objects.create(user=self.user, kind=Todo.Kind.MEETING, title="late call", starts_at=start, ends_at=start + timedelta(minutes=30))
        early = Todo.objects.create(user=self.user, kind=Todo.Kind.MEETING, title="standup", starts_at=start - timedelta(hours=1), ends_at=start - timedelta(minutes=30))
        TodoService.produce(self.user, "water plants", None, timezone.localdate())  # Scheduled lands first…
        TodoService.place_todays_meetings(self.user)                                # …then the meetings go above it
        self.assertEqual(self.listed(), ["standup", "late call", "water plants", "todo 1", "todo 2"])
        self.assertEqual([t.position for t in TodoService.listed_for(self.user)], [1, 2, 3, 4, 5])
        self.assertEqual({early.pk, later.pk}, set(Todo.objects.filter(listed_on=timezone.localdate()).values_list("pk", flat=True)))

    def test_a_placed_meeting_moves_like_a_todo_and_stays_where_it_was_put(self):
        start = TodoService.start_of_today() + timedelta(hours=23)
        meeting = Todo.objects.create(user=self.user, kind=Todo.Kind.MEETING, title="standup", starts_at=start, ends_at=start + timedelta(minutes=30))
        TodoService.place_todays_meetings(self.user)
        todo_1, todo_2 = Todo.objects.get(title="todo 1"), Todo.objects.get(title="todo 2")
        TodoService.reorder(self.user, TodoReorderSchema(ids=[todo_1.id, meeting.id, todo_2.id]))
        TodoService.place_todays_meetings(self.user)  # already placed today: it isn't pulled back to the top
        self.assertEqual(self.listed(), ["todo 1", "standup", "todo 2"])

    def test_a_produced_todo_goes_to_the_top(self):
        TodoService.produce(self.user, "water plants", None, timezone.localdate())
        self.assertEqual(list(TodoService.ordered_for(self.user).values_list("title", "position")),
                         [("water plants", 1), ("todo 1", 2), ("todo 2", 3)])

    def test_meetings_are_read_only_but_can_be_commented_on(self):
        meeting = self.meeting("standup", 1)
        self.assertTrue(self.user.has_perm("todos.comment_todo", meeting))
        self.assertFalse(self.user.has_perm("todos.change_todo", meeting))
        self.assertFalse(self.user.has_perm("todos.delete_todo", meeting))
