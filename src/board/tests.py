import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.services import GuestService, IdentityService, PreferenceService
from calendars.models import CalendarConnection
from calendars.services import RecallService
from comments.models import Comment
from todos.models import Todo
from todos.schemas import TodoTitleSchema
from todos.services import TodoService


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class BoardTests(TestCase):
    def setUp(self):
        self.user = IdentityService.create_user("a@x.com", name="Ada")
        self.client.force_login(self.user)
        self.todos = [TodoService.create(self.user, TodoTitleSchema(title=f"todo {i}")) for i in (1, 2, 3)]

    def url(self, name, todo=None):
        return reverse(f"board:{name}", args=[todo.id] if todo else None)

    def at(self, todo):
        return f"{reverse('board:index')}?t={todo.id}"

    def test_the_thread_shows_the_first_todo_by_default(self):
        page = self.client.get(self.url("index"))
        self.assertContains(page, "#1 today")
        self.assertContains(page, "todo-row--current")

    def test_the_thread_spells_out_its_age_from_yesterday_on(self):
        page = self.client.get(self.url("index"))
        self.assertContains(page, "#1 today</b></span>")                # added today: nothing more to say
        Todo.objects.filter(pk=self.todos[0].pk).update(created_at=timezone.now() - timedelta(days=1))
        self.assertContains(self.client.get(self.url("index")), "#1 today</b> · added yesterday")
        Todo.objects.filter(pk=self.todos[0].pk).update(created_at=timezone.now() - timedelta(days=3))
        self.assertContains(self.client.get(self.url("index")), "#1 today</b> · added 3 days ago")

    def test_updates_not_comments(self):
        page = self.client.get(self.url("index"))
        self.assertContains(page, 'placeholder="Add an update"')
        self.assertContains(page, "No updates yet.")

    def test_t_selects_a_todo(self):
        self.assertContains(self.client.get(self.at(self.todos[1])), "#2 today")

    def test_selecting_a_todo_returns_its_thread_and_pushes_the_url(self):
        response = self.client.get(self.url("todo-detail", self.todos[1]))
        self.assertContains(response, 'id="thread"')
        self.assertEqual(response["HX-Push-Url"], self.at(self.todos[1]))
        self.assertContains(response, 'id="comment-input"')
        self.assertRegex(response.content.decode(), r'id="comment-input"[^>]*autofocus')

    def test_create_swaps_the_workspace_and_keeps_the_selection(self):
        response = self.client.post(self.url("todo-create"), {"title": "fresh", "t": self.todos[0].id})
        self.assertContains(response, "fresh")
        self.assertEqual((response["HX-Retarget"], response["HX-Reswap"]), ("#workspace", "outerHTML"))
        self.assertEqual(response["HX-Push-Url"], self.at(self.todos[0]))

    def test_an_invalid_todo_rerenders_the_form_with_a_message(self):
        response = self.client.post(self.url("todo-create"), {"title": "   "})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="todo-form"')
        self.assertContains(response, "form-message--error")
        self.assertNotIn("HX-Retarget", response)

    def test_closing_another_todo_keeps_the_thread(self):
        response = self.client.post(self.url("todo-close", self.todos[0]), {"t": self.todos[1].id})
        self.assertEqual(response["HX-Push-Url"], self.at(self.todos[1]))

    def test_a_todo_done_today_stays_in_its_place_with_a_tick(self):
        response = self.client.post(self.url("todo-close", self.todos[0]), {"t": self.todos[0].id})
        self.assertNotContains(response, 'class="toast__item"')
        self.assertEqual(response["HX-Push-Url"], self.at(self.todos[0]))        # still the one being read
        html = response.content.decode()
        self.assertIn("todo-row--closed", html)
        self.assertLess(html.index("todo 1"), html.index("todo 2"))               # nothing moved
        self.assertIn('class="todo-row__done" aria-label="todo 1: done — reopen"', html)   # its tick, in its number's place
        self.assertIn('<span class="todo-row__pos">02</span>', html)        # closing one changes no one's number
        self.assertIn('<span class="todo-row__pos"></span>', html)          # the done one shows its tick instead
        self.assertIn('aria-label="Mark todo 2 done"', html)
        thread = self.client.get(self.url("todo-detail", self.todos[0])).content.decode()
        self.assertIn("Done today</b>", thread)                                        # done, not a number or its age
        self.assertIn('thread__done--on', thread)
        self.client.post(self.url("todo-reopen", self.todos[0]), {"index": 99})
        self.assertEqual(list(TodoService.open_for(self.user).values_list("title", flat=True)), ["todo 1", "todo 2", "todo 3"])

    def test_done_yesterday_leaves_the_list_for_the_done_rows(self):
        TodoService.close(self.todos[0])
        Todo.objects.filter(pk=self.todos[0].pk).update(closed_at=timezone.now() - timedelta(days=1))
        html = self.client.get(self.url("index")).content.decode()
        self.assertNotIn("todo-row--closed", html)
        self.assertIn("todo-row--done", html)

    def test_deleting_shows_a_quiet_undo_notice_and_undo_clears_it(self):
        response = self.client.post(self.url("todo-delete", self.todos[0]))
        self.assertContains(response, 'class="toast__item"')
        self.assertContains(response, "Deleted “todo 1”")
        response = self.client.post(self.url("todo-reopen", self.todos[0]), {"index": 1})
        self.assertNotContains(response, 'class="toast__item"')

    def test_the_done_stack_shows_the_last_ten_closed_newest_first(self):
        extra = [TodoService.create(self.user, TodoTitleSchema(title=f"extra {i}")) for i in range(10)]
        for n, todo in enumerate(self.todos + extra):
            TodoService.close(todo)
            Todo.objects.filter(pk=todo.pk).update(closed_at=timezone.now() - timedelta(days=1, minutes=60 - n))  # done yesterday
        TodoService.delete(TodoService.create(self.user, TodoTitleSchema(title="binned")))
        page = self.client.get(self.url("index")).content.decode()
        self.assertEqual(page.count("todo-row--done"), 10)
        self.assertLess(page.index("extra 9"), page.index("extra 0"))
        self.assertNotIn("binned", page)
        self.assertNotIn("Recently done", page)  # no separate section: they end the list

    def test_search_shows_only_active_matches_and_clearing_brings_done_back(self):
        TodoService.close(self.todos[0])
        Todo.objects.filter(pk=self.todos[0].pk).update(closed_at=timezone.now() - timedelta(days=1))
        self.assertNotContains(self.client.get(self.url("todo-list"), {"q": "todo"}), "todo-row--done")
        self.assertContains(self.client.get(self.url("todo-list"), {"q": ""}), "todo-row--done", count=1)

    def test_reopening_from_the_done_stack_puts_it_at_the_end(self):
        TodoService.close(self.todos[0])
        Todo.objects.filter(pk=self.todos[0].pk).update(closed_at=timezone.now() - timedelta(days=1))
        TodoService._renumber(self.user)
        page = self.client.get(self.url("index"))
        self.assertContains(page, '"index": 3')
        html = page.content.decode()
        self.assertLess(html.index("todo 3"), html.index("todo-row--done"))  # done rows come after the active ones
        self.client.post(self.url("todo-reopen", self.todos[0]), {"index": 3})
        self.assertEqual(list(TodoService.open_for(self.user).values_list("title", flat=True)), ["todo 2", "todo 3", "todo 1"])

    def test_deleting_offers_undo_and_undo_restores(self):
        self.assertContains(self.client.post(self.url("todo-delete", self.todos[0])), "Deleted")
        self.client.post(self.url("todo-reopen", self.todos[0]), {"index": 1})
        self.assertEqual(list(TodoService.open_for(self.user).values_list("title", flat=True)), ["todo 1", "todo 2", "todo 3"])

    def test_renaming_updates_the_title_in_the_thread_and_the_list(self):
        response = self.client.post(self.url("todo-rename", self.todos[0]), {"title": "  sharper title "})
        self.assertContains(response, 'value="sharper title"')
        self.assertContains(response, f'id="todo-title-{self.todos[0].id}" class="todo-row__title" hx-swap-oob="true"')
        self.todos[0].refresh_from_db()
        self.assertEqual(self.todos[0].title, "sharper title")

    def test_a_blank_rename_keeps_the_title_and_says_why(self):
        response = self.client.post(self.url("todo-rename", self.todos[0]), {"title": "   "})
        self.assertContains(response, 'value="todo 1"')
        self.assertContains(response, "Write something first")
        self.assertNotContains(response, "hx-swap-oob")

    def test_the_thread_marks_done_with_a_tick_and_deletes_from_the_title_line(self):
        page = self.client.get(self.url("index"))
        self.assertContains(page, 'aria-label="Mark done"')
        self.assertContains(page, 'class="icon-btn icon-btn--small icon-btn--quiet" aria-label="Delete todo"')
        self.assertNotContains(page, ">Mark done<")

    def test_reorder_returns_the_selected_thread_with_its_new_position(self):
        ids = ",".join(str(t.id) for t in reversed(self.todos))
        response = self.client.post(self.url("todo-reorder"), {"ids": ids, "t": self.todos[2].id})
        self.assertEqual(list(TodoService.open_for(self.user).values_list("title", flat=True)), ["todo 3", "todo 2", "todo 1"])
        self.assertContains(response, "#1 today")
        self.assertEqual(response["HX-Push-Url"], self.at(self.todos[2]))

    def test_search_keeps_full_list_positions(self):
        response = self.client.get(self.url("todo-list"), {"q": "todo 3"})
        self.assertContains(response, "03")
        self.assertNotContains(response, "todo 1")
        self.assertContains(self.client.get(self.url("todo-list"), {"q": "zzz"}), "No matches.")

    def test_a_comment_with_a_file_lands_in_the_thread(self):
        upload = SimpleUploadedFile("shot.png", b"\x89PNG....", content_type="image/png")
        response = self.client.post(self.url("comment-create", self.todos[0]), {"body": "hello", "files": upload})
        self.assertContains(response, "hello")
        self.assertContains(response, "shot.png")
        self.assertEqual(response["HX-Retarget"], "#thread")

    def test_an_empty_comment_rerenders_the_form_with_a_message(self):
        response = self.client.post(self.url("comment-create", self.todos[0]), {"body": "  "})
        self.assertContains(response, 'id="comment-form"')
        self.assertContains(response, "Write something or attach a file")
        self.assertEqual(Comment.objects.count(), 0)

    @override_settings(MAX_UPLOAD_BYTES=4)
    def test_an_oversized_file_is_refused_with_a_message(self):
        upload = SimpleUploadedFile("big.bin", b"12345", content_type="application/octet-stream")
        response = self.client.post(self.url("comment-create", self.todos[0]), {"body": "x", "files": upload})
        self.assertContains(response, "big.bin")
        self.assertContains(response, "form-message--error")
        self.assertEqual(Comment.objects.count(), 0)

    def test_all_todos_lists_only_closed_todos_most_recent_first(self):
        TodoService.close(self.todos[0])
        TodoService.close(self.todos[1])
        Todo.objects.filter(pk=self.todos[0].pk).update(closed_at=timezone.now() - timedelta(days=2))
        response = self.client.get(self.url("review"))
        titles = [row.title for row in response.context["rows"]]
        self.assertEqual(titles, ["todo 2", "todo 1"])  # todo 3 is still open: not listed
        self.assertContains(response, 'id="timeline-title">All todos</span>')
        self.assertContains(response, 'aria-label="Reopen"', count=2)
        self.assertNotContains(response, "review__filter")

    def test_deleted_and_open_todos_never_show_in_all_todos(self):
        TodoService.close(self.todos[0])
        TodoService.delete(self.todos[0])
        response = self.client.get(self.url("review"))
        self.assertNotContains(response, "todo 1")
        self.assertEqual(self.client.get(self.url("review-thread", self.todos[0])).status_code, 404)
        self.assertEqual(self.client.get(self.url("review-thread", self.todos[1])).status_code, 404)  # still open

    def test_a_closed_todos_thread_is_readable_in_the_review(self):
        from comments.schemas import CommentCreateSchema
        from comments.service import CommentService
        CommentService.add(self.todos[0], CommentCreateSchema(body="what I learned"))
        TodoService.close(self.todos[0])
        response = self.client.get(self.url("review-thread", self.todos[0]))
        self.assertContains(response, "what I learned")
        self.assertContains(response, "Closed ")
        self.assertContains(response, 'aria-label="Reopen"')
        self.assertNotContains(response, 'id="comment-form"')
        self.assertNotContains(response, 'class="overlay"')  # only the table area is replaced

    def test_deleting_from_all_todos_soft_deletes_and_refreshes_the_board(self):
        TodoService.close(self.todos[0])
        response = self.client.post(self.url("review-delete", self.todos[0]), {"t": self.todos[1].id})
        self.todos[0].refresh_from_db()
        self.assertIsNotNone(self.todos[0].deleted_at)  # kept in the database
        self.assertContains(response, 'class="review timeline"')
        self.assertNotContains(response, "todo 1")
        self.assertContains(response, 'id="workspace" hx-swap-oob="true"')
        self.assertContains(response, "#1 today")  # the board's thread moved to the next todo

    def test_review_rows_open_their_thread(self):
        TodoService.close(self.todos[0])
        response = self.client.get(self.url("review"))
        self.assertContains(response, reverse("board:review-thread", args=[self.todos[0].id]))

    def test_reopening_from_review_puts_it_on_top_selects_it_and_closes_the_modal(self):
        TodoService.close(self.todos[2])
        Todo.objects.filter(pk=self.todos[2].pk).update(closed_at=timezone.now() - timedelta(days=1))
        TodoService._renumber(self.user)
        response = self.client.post(self.url("todo-reopen", self.todos[2]), {"index": 1, "select": "1"})
        self.assertEqual(response["HX-Trigger"], "overlay-close")
        self.assertEqual(response["HX-Push-Url"], self.at(self.todos[2]))
        self.assertEqual(list(TodoService.open_for(self.user).values_list("title", flat=True))[0], "todo 3")

    def test_another_users_todo_is_forbidden(self):
        self.client.force_login(IdentityService.create_user("b@x.com"))
        self.assertEqual(self.client.get(self.url("todo-detail", self.todos[0])).status_code, 403)
        self.assertEqual(self.client.post(self.url("todo-close", self.todos[0])).status_code, 403)
        self.assertEqual(self.client.post(self.url("comment-create", self.todos[0]), {"body": "x"}).status_code, 403)
        self.assertEqual(self.client.post(self.url("todo-rename", self.todos[0]), {"title": "mine now"}).status_code, 403)
        TodoService.close(self.todos[0])  # All todos only reaches closed ones
        self.assertEqual(self.client.get(self.url("review-thread", self.todos[0])).status_code, 403)
        self.assertEqual(self.client.post(self.url("review-delete", self.todos[0])).status_code, 403)
        self.assertNotContains(self.client.get(self.url("review")), "todo 1")




class MeetingBoardTests(TestCase):
    def setUp(self):
        self.user = IdentityService.create_user("d@x.com", name="Dee", show_meetings=True, timezone="UTC")
        self.client.force_login(self.user)
        TodoService.create(self.user, TodoTitleSchema(title="write the plan"))
        start = (timezone.now() + timedelta(minutes=30)).replace(second=0, microsecond=0)
        self.meeting = Todo.objects.create(user=self.user, kind=Todo.Kind.MEETING, title="Hiring panel", starts_at=start,
                                           ends_at=start + timedelta(hours=1), meeting_url="https://zoom.us/j/1")

    def test_the_list_is_one_list_dated_today(self):
        page = self.client.get(reverse("board:index"))
        today = timezone.localdate()
        self.assertContains(page, f'Todos · {today:%a}, {today:%b} {today.day}</span>')   # today, beside the header
        self.assertNotContains(page, 'remind')

    def test_a_meeting_shows_its_time_and_a_read_only_thread(self):
        page = self.client.get(f"{reverse('board:index')}?t={self.meeting.id}")
        self.assertContains(page, "Meeting</b> · ")
        self.assertContains(page, 'href="https://zoom.us/j/1"')
        self.assertNotContains(page, 'aria-label="Todo title"')
        self.assertNotContains(page, 'aria-label="Delete todo"')
        self.assertContains(page, 'id="comment-input"')

    def test_a_meeting_cannot_be_closed_or_renamed(self):
        self.assertEqual(self.client.post(reverse("board:todo-close", args=[self.meeting.id])).status_code, 403)
        self.assertEqual(self.client.post(reverse("board:todo-rename", args=[self.meeting.id]), {"title": "x"}).status_code, 403)


@override_settings(RECALL_API_KEY="key", GOOGLE_CLIENT_ID="id", GOOGLE_CLIENT_SECRET="secret")
class SettingsTests(TestCase):
    def setUp(self):
        self.user = IdentityService.create_user("s@x.com", name="Sam")
        self.client.force_login(self.user)

    def test_the_account_menu_opens_settings(self):
        self.assertContains(self.client.get(reverse("board:index")), f'hx-get="{reverse("board:settings")}"')

    def test_turning_meetings_on_suggests_the_signed_in_calendar(self):
        self.assertNotContains(self.client.get(reverse("board:settings")), "Connect <b>s@x.com</b>")
        response = self.client.post(reverse("board:settings-meetings"), {"show_meetings": "1"})
        self.assertContains(response, "Connect <b>s@x.com</b>")
        self.assertContains(response, "hint=s%40x.com")
        self.assertIn("board-changed", response["HX-Trigger"])

    def test_choosing_a_time_zone_saves_it_and_refreshes_the_board(self):
        response = self.client.post(reverse("board:settings-timezone"), {"timezone": "Asia/Kolkata"})
        self.assertContains(response, '<option value="Asia/Kolkata" selected>')
        self.assertIn("board-changed", response["HX-Trigger"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.timezone, "Asia/Kolkata")

    def test_the_browsers_zone_fills_in_but_never_overrides_a_choice(self):
        self.client.post(reverse("board:settings-timezone"), {"timezone": "Europe/Berlin", "detected": "1"})
        self.client.post(reverse("board:settings-timezone"), {"timezone": "Asia/Tokyo", "detected": "1"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.timezone, "Europe/Berlin")

    def test_an_unknown_browser_zone_is_skipped_without_a_message(self):
        response = self.client.post(reverse("board:settings-timezone"), {"timezone": "Etc/Unknown", "detected": "1"})
        self.assertEqual(response.status_code, 204)
        self.assertNotContains(self.client.get(reverse("board:index")), "Pick a time zone")

    def test_an_unknown_zone_is_refused(self):
        self.assertContains(self.client.post(reverse("board:settings-timezone"), {"timezone": "Mars/Base"}), "Pick a time zone from the list")

    def test_guests_are_asked_to_sign_in_for_calendars(self):
        self.client.force_login(GuestService.create_guest())
        response = self.client.get(reverse("board:settings"))
        self.assertContains(response, "Sign in to bring meetings")
        self.assertNotContains(response, 'name="show_meetings"')

    @patch.object(RecallService, "delete_calendar")
    def test_disconnecting_a_calendar_says_so(self, delete_calendar):
        PreferenceService.set_show_meetings(self.user, True)
        connection = CalendarConnection.objects.create(user=self.user, provider="google", email="s@x.com", recall_id="cal-1")
        response = self.client.post(reverse("board:settings-disconnect", args=[connection.id]))
        self.assertContains(response, "Disconnected s@x.com.")
        self.assertFalse(CalendarConnection.objects.exists())


class RowAgeTests(TestCase):
    def setUp(self):
        self.user = IdentityService.create_user("age@x.com")
        self.client.force_login(self.user)

    def test_a_row_shows_its_age_from_one_day_on(self):
        fresh = TodoService.create(self.user, TodoTitleSchema(title="fresh"))
        old = TodoService.create(self.user, TodoTitleSchema(title="old"))
        Todo.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=3))
        Todo.objects.filter(pk=fresh.pk).update(created_at=timezone.now() - timedelta(days=1))
        page = self.client.get(reverse("board:index")).content.decode()
        self.assertIn('title="3 days on list">3d</span>', page)
        self.assertIn('title="1 day on list">1d</span>', page)
        self.assertNotIn(">0d</span>", page)
