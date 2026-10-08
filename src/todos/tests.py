from datetime import timedelta

from django.test import RequestFactory, TestCase
from django.utils import timezone

from accounts.services import IdentityService
from todos.models import PositionDay, Todo
from todos.schemas import ReviewPeriod, ReviewStatus, TodoTitleSchema, TodoReopenSchema, TodoReorderSchema
from todos.services import InsightsService, PositionHistoryService, TodoService


class TodoServiceTests(TestCase):
    def setUp(self):
        self.user = IdentityService.create_user("a@x.com")
        self.todos = [TodoService.create(self.user, TodoTitleSchema(title=f"todo {i}")) for i in (1, 2, 3)]

    def positions(self):
        return list(TodoService.open_for(self.user).values_list("title", "position"))

    def test_close_renumbers_and_returns_where_it_was(self):
        removed = TodoService.close(self.todos[0])
        self.assertEqual((removed.index, removed.removal), (1, Todo.Removal.CLOSED))
        self.assertEqual(self.positions(), [("todo 2", 1), ("todo 3", 2)])

    def test_reopen_puts_it_back_in_place(self):
        TodoService.close(self.todos[0])
        TodoService.reopen(Todo.objects.get(pk=self.todos[0].pk), TodoReopenSchema(index=1, select=False))
        self.assertEqual(self.positions(), [("todo 1", 1), ("todo 2", 2), ("todo 3", 3)])

    def test_delete_is_undoable_and_never_counts_as_done(self):
        TodoService.delete(self.todos[0])
        self.assertEqual(InsightsService.for_period(self.user, ReviewPeriod.ALL).closed, 0)
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


class PositionHistoryTests(TestCase):
    def setUp(self):
        self.user = IdentityService.create_user("a@x.com")
        self.todos = [TodoService.create(self.user, TodoTitleSchema(title=f"todo {i}")) for i in (1, 2, 3)]

    def test_reorder_records_every_todos_position_for_today(self):
        TodoService.reorder(self.user, TodoReorderSchema(ids=[t.id for t in reversed(self.todos)]))
        recorded = {d.todo.title: d.position for d in PositionDay.objects.all()}
        self.assertEqual(recorded, {"todo 3": 1, "todo 2": 2, "todo 1": 3})

    def test_an_undo_overwrites_the_days_record(self):
        TodoService.close(self.todos[0])
        self.assertEqual(PositionDay.objects.get(todo=self.todos[1]).position, 1)
        TodoService.reopen(Todo.objects.get(pk=self.todos[0].pk), TodoReopenSchema(index=1, select=False))
        self.assertEqual(PositionDay.objects.get(todo=self.todos[1]).position, 2)
        self.assertEqual(PositionDay.objects.count(), 3)

    def test_journey_carries_a_position_across_quiet_days(self):
        todo = self.todos[0]
        today = timezone.localdate()
        Todo.objects.filter(pk=todo.pk).update(created_at=timezone.now() - timedelta(days=3))
        todo.days.all().delete()
        PositionDay.objects.create(todo=todo, day=today - timedelta(days=3), position=5)
        PositionDay.objects.create(todo=todo, day=today - timedelta(days=1), position=2)
        todo.refresh_from_db()
        self.assertEqual([p for _, p in PositionHistoryService.journey(todo)], [5, 5, 2, 2])


class InsightsTests(TestCase):
    def setUp(self):
        self.user = IdentityService.create_user("a@x.com")

    def closed(self, title, closed_ago, created_ago=None):
        todo = TodoService.create(self.user, TodoTitleSchema(title=title))
        TodoService.close(todo)
        now = timezone.now()
        Todo.objects.filter(pk=todo.pk).update(
            closed_at=now - closed_ago, created_at=now - (created_ago or closed_ago),
        )

    def test_counts_this_week_and_compares_with_the_same_stretch_last_week(self):
        self.closed("now", timedelta(minutes=1))
        self.closed("a week ago", timedelta(days=7, minutes=1))
        self.closed("too old", timedelta(days=30))
        insights = InsightsService.for_period(self.user, ReviewPeriod.WEEK)
        self.assertEqual((insights.closed, insights.previous, insights.delta), (1, 1, 0))

    def test_streak_counts_consecutive_days_with_a_close(self):
        self.closed("today", timedelta(minutes=1))
        self.closed("yesterday", timedelta(days=1))
        self.closed("gap", timedelta(days=3))
        self.assertEqual(InsightsService.for_period(self.user, ReviewPeriod.WEEK).streak, 2)

    def test_longest_wait_is_the_oldest_todo_cleared_in_the_period(self):
        self.closed("quick", timedelta(minutes=1))
        self.closed("slow", timedelta(minutes=2), created_ago=timedelta(days=12))
        wait = InsightsService.for_period(self.user, ReviewPeriod.ALL).longest_wait
        self.assertEqual((wait.title, wait.days), ("slow", 12))

    def test_week_has_seven_buckets_and_future_days_are_empty(self):
        buckets = InsightsService.for_period(self.user, ReviewPeriod.WEEK).buckets
        self.assertEqual(len(buckets), 7)
        today = timezone.localdate()
        self.assertTrue(all(b.count is None for b in buckets if b.start > today))

    def test_all_time_reports_the_best_week(self):
        for minutes in (1, 2, 3):
            self.closed(f"t{minutes}", timedelta(minutes=minutes))
        self.assertEqual(InsightsService.for_period(self.user, ReviewPeriod.ALL).best_week, 3)

    def test_deleted_todos_never_count(self):
        TodoService.delete(TodoService.create(self.user, TodoTitleSchema(title="gone")))
        self.assertEqual(InsightsService.for_period(self.user, ReviewPeriod.ALL).closed, 0)


class ReviewQueryTests(TestCase):
    def test_reviewed_filters_by_state(self):
        user = IdentityService.create_user("a@x.com")
        done, gone, _ = (TodoService.create(user, TodoTitleSchema(title=t)) for t in ("done", "gone", "open"))
        TodoService.close(done)
        TodoService.delete(gone)
        titles = {status: [t.title for t in TodoService.reviewed(user, status, None)] for status in ReviewStatus.values}
        self.assertEqual(titles, {"done": ["done"], "open": ["open"], "deleted": ["gone"]})


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
