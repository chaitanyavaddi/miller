import tempfile
from datetime import datetime, time, timedelta
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

    def test_closing_the_todo_being_read_moves_the_thread_on(self):
        response = self.client.post(self.url("todo-close", self.todos[0]), {"t": self.todos[0].id})
        self.assertEqual(response["HX-Push-Url"], self.at(self.todos[1]))

    def test_closing_needs_no_notice_the_done_rows_show_it(self):
        response = self.client.post(self.url("todo-close", self.todos[0]))
        self.assertNotContains(response, 'class="toast__item"')
        self.assertContains(response, "todo-row--done")

    def test_deleting_shows_a_quiet_undo_notice_and_undo_clears_it(self):
        response = self.client.post(self.url("todo-delete", self.todos[0]))
        self.assertContains(response, 'class="toast__item"')
        self.assertContains(response, "Deleted “todo 1”")
        response = self.client.post(self.url("todo-reopen", self.todos[0]), {"index": 1})
        self.assertNotContains(response, 'class="toast__item"')

    def test_the_done_stack_shows_the_last_ten_closed_newest_first(self):
        extra = [TodoService.create(self.user, TodoTitleSchema(title=f"extra {i}")) for i in range(10)]
        for todo in self.todos + extra:
            TodoService.close(todo)
        TodoService.delete(TodoService.create(self.user, TodoTitleSchema(title="binned")))
        page = self.client.get(self.url("index")).content.decode()
        self.assertEqual(page.count("todo-row--done"), 10)
        self.assertLess(page.index("extra 9"), page.index("extra 0"))
        self.assertNotIn("binned", page)
        self.assertNotIn("Recently done", page)  # no separate section: they end the list

    def test_search_shows_only_active_matches_and_clearing_brings_done_back(self):
        TodoService.close(self.todos[0])
        self.assertNotContains(self.client.get(self.url("todo-list"), {"q": "todo"}), "todo-row--done")
        self.assertContains(self.client.get(self.url("todo-list"), {"q": ""}), "todo-row--done", count=1)

    def test_reopening_from_the_done_stack_puts_it_at_the_end(self):
        TodoService.close(self.todos[0])
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

    def test_review_shows_what_the_week_adds_up_to(self):
        TodoService.close(self.todos[0])
        response = self.client.get(self.url("review"))
        self.assertContains(response, 'You closed <span class="review__number">1</span> todo this week.')
        self.assertContains(response, "Reopen")
        self.assertContains(response, "review-chart__bar")

    def test_review_filters_fall_back_to_done_this_week(self):
        response = self.client.get(self.url("review"), {"status": "bogus", "period": "bogus"})
        self.assertContains(response, 'review__filter review__filter--active" aria-pressed="true"', count=2)

    def test_review_lists_open_todos_with_a_show_action(self):
        response = self.client.get(self.url("review"), {"status": "open", "period": "all"})
        self.assertContains(response, 'data-action="select-todo"', count=3)

    def test_a_closed_todos_thread_is_readable_in_the_review(self):
        from comments.schemas import CommentCreateSchema
        from comments.service import CommentService
        CommentService.add(self.todos[0], CommentCreateSchema(body="what I learned"))
        TodoService.close(self.todos[0])
        response = self.client.get(self.url("review-thread", self.todos[0]), {"status": "done", "period": "all"})
        self.assertContains(response, "what I learned")
        self.assertContains(response, "Closed ")
        self.assertContains(response, "Reopen")
        self.assertContains(response, "?status=done&period=all")  # back keeps the filters
        self.assertNotContains(response, 'id="comment-form"')
        self.assertNotContains(response, 'class="overlay"')  # only the table area is replaced

    def test_deleting_from_review_moves_a_todo_to_deleted_and_refreshes_the_board(self):
        response = self.client.post(self.url("review-delete", self.todos[0]), {"status": "open", "period": "all", "t": self.todos[1].id})
        self.todos[0].refresh_from_db()
        self.assertIsNotNone(self.todos[0].deleted_at)
        self.assertContains(response, 'class="review"')
        self.assertContains(response, 'id="workspace" hx-swap-oob="true"')
        self.assertContains(response, "#1 today")  # the board's thread moved to the next todo

    def test_deleting_a_deleted_todo_removes_it_and_its_files_for_good(self):
        import os
        from comments.schemas import CommentCreateSchema
        from comments.service import CommentService
        upload = SimpleUploadedFile("note.txt", b"hi", content_type="text/plain")
        path = CommentService.add(self.todos[0], CommentCreateSchema(body="", files=[upload])).attachments.get().file.path
        TodoService.delete(self.todos[0])
        response = self.client.post(self.url("review-delete", self.todos[0]), {"status": "deleted", "period": "all"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TodoService.any_queryset().filter(pk=self.todos[0].pk).exists())
        self.assertFalse(Comment.objects.exists())
        self.assertFalse(os.path.exists(path))

    def test_a_deleted_todo_asks_before_deleting_for_good(self):
        TodoService.delete(self.todos[0])
        response = self.client.get(self.url("review"), {"status": "deleted", "period": "all"})
        self.assertContains(response, "for good? This can’t be undone.")

    def test_review_rows_open_their_thread(self):
        response = self.client.get(self.url("review"), {"status": "open", "period": "all"})
        self.assertContains(response, reverse("board:review-thread", args=[self.todos[0].id]))

    def test_reopening_from_review_puts_it_on_top_selects_it_and_closes_the_modal(self):
        TodoService.close(self.todos[2])
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
        self.assertEqual(self.client.get(self.url("review-thread", self.todos[0])).status_code, 403)
        self.assertEqual(self.client.post(self.url("review-delete", self.todos[0])).status_code, 403)
        self.assertNotContains(self.client.get(self.url("review"), {"period": "all", "status": "open"}), "todo 1")


class DayBoardTests(TestCase):
    def setUp(self):
        self.user = IdentityService.create_user("d@x.com", name="Dee", show_meetings=True, timezone="UTC")
        self.client.force_login(self.user)
        self.today = timezone.localdate()
        self.todo = TodoService.create(self.user, TodoTitleSchema(title="write the plan"))

    def meeting(self, day, title="Hiring panel"):
        start = timezone.make_aware(datetime.combine(day, time(10)))
        meeting = Todo.objects.create(user=self.user, kind=Todo.Kind.MEETING, title=title, day=day, starts_at=start,
                                      ends_at=start + timedelta(hours=1), meeting_url="https://zoom.us/j/1")
        TodoService.slot_meeting(meeting)
        return meeting

    def test_the_list_heads_with_today_and_labels_the_coming_days(self):
        TodoService.move_to_day(self.todo, self.today + timedelta(days=2))
        page = self.client.get(reverse("board:index"))
        self.assertContains(page, f"Todos · {self.today:%a}, {self.today:%b} {self.today.day}")
        day = self.today + timedelta(days=2)
        self.assertContains(page, f'<span class="day__label">{f"{day:%a}, {day:%b} {day.day}".lower()}</span>')

    def test_todos_past_the_week_fold_into_later_with_a_count(self):
        TodoService.move_to_day(self.todo, self.today + timedelta(days=30))
        page = self.client.get(reverse("board:index"))
        self.assertContains(page, "<span data-later-count>1 todo</span>")
        self.assertContains(page, 'data-action="later-toggle"')

    def test_a_meeting_shows_its_time_and_a_read_only_thread(self):
        meeting = self.meeting(self.today)
        page = self.client.get(f"{reverse('board:index')}?t={meeting.id}")
        self.assertContains(page, "10:00a")
        self.assertContains(page, "Meeting</b> · today · 10:00a – 11:00a")
        self.assertContains(page, 'href="https://zoom.us/j/1"')
        self.assertNotContains(page, 'aria-label="Todo title"')   # no rename
        self.assertNotContains(page, 'aria-label="Delete todo"')
        self.assertContains(page, 'id="comment-input"')            # notes still welcome

    def test_a_meeting_cannot_be_closed_or_renamed(self):
        meeting = self.meeting(self.today)
        self.assertEqual(self.client.post(reverse("board:todo-close", args=[meeting.id])).status_code, 403)
        self.assertEqual(self.client.post(reverse("board:todo-rename", args=[meeting.id]), {"title": "x"}).status_code, 403)

    def test_setting_a_reminder_shows_it_on_the_row_and_files_it_under_its_day(self):
        tomorrow = self.today + timedelta(days=1)
        response = self.client.post(reverse("board:todo-remind", args=[self.todo.id]), {"remind_at": f"{tomorrow.isoformat()}T09:30", "t": self.todo.id})
        self.assertContains(response, "9:30a")
        self.todo.refresh_from_db()
        self.assertEqual(self.todo.day, tomorrow)
        response = self.client.post(reverse("board:todo-remind", args=[self.todo.id]), {"remind_at": ""})
        self.todo.refresh_from_db()
        self.assertIsNone(self.todo.remind_at)

    def test_a_garbled_reminder_is_refused_with_a_message(self):
        response = self.client.post(reverse("board:todo-remind", args=[self.todo.id]), {"remind_at": "soon"})
        self.assertContains(response, "isn&#x27;t a date and time")
        self.assertEqual(response["HX-Retarget"], "#reminder")


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


class ReminderAndReorderTests(TestCase):
    def setUp(self):
        self.user = IdentityService.create_user("r@x.com", timezone="UTC")
        self.client.force_login(self.user)
        self.todo = TodoService.create(self.user, TodoTitleSchema(title="call the bank"))

    def test_the_composer_offers_the_presets_with_pick_a_time_last(self):
        page = self.client.get(reverse("board:index")).content.decode()
        self.assertIn('data-remind-preset="hour"', page)
        self.assertIn('data-remind-preset="tomorrow"', page)
        self.assertLess(page.index('data-remind-preset="tomorrow"'), page.index("Pick a time…"))

    def test_adding_a_todo_with_a_preset_sets_its_reminder(self):
        self.client.post(reverse("board:todo-create"), {"title": "pay rent", "remind_preset": "tomorrow"})
        todo = Todo.objects.get(title="pay rent")
        tomorrow = timezone.localdate() + timedelta(days=1)
        self.assertEqual((todo.day, timezone.localtime(todo.remind_at).time()), (tomorrow, time(9)))

    def test_a_reminder_in_the_past_is_refused_and_the_todo_is_not_added(self):
        response = self.client.post(reverse("board:todo-create"), {"title": "too late", "remind_at": "2020-01-01T09:00"})
        self.assertContains(response, "Pick a time that hasn")
        self.assertFalse(Todo.objects.filter(title="too late").exists())

    def test_the_thread_shows_a_reminder_line_only_once_there_is_one(self):
        page = self.client.get(reverse("board:index"))
        self.assertNotContains(page, 'type="datetime-local" class="reminder')
        self.assertNotContains(page, "reminder__when")
        self.assertContains(page, 'data-remind-for="remind-form"')       # the bell by the title
        self.client.post(reverse("board:todo-remind", args=[self.todo.id]), {"remind_preset": "tomorrow"})
        page = self.client.get(reverse("board:index"))
        self.assertContains(page, "Tomorrow · 9:00a")
        self.assertNotContains(page, 'data-remind-for="remind-form"')

    def test_the_next_five_days_and_later_are_there_to_drop_onto(self):
        page = self.client.get(reverse("board:index")).content.decode()
        for offset in range(1, 6):
            self.assertIn(f'data-day="{(timezone.localdate() + timedelta(days=offset)).isoformat()}"', page)
        self.assertIn('data-day="later"', page)
        self.assertIn('data-action="reorder-start"', page)


class RowAgeTests(TestCase):
    def setUp(self):
        self.user = IdentityService.create_user("age@x.com")
        self.client.force_login(self.user)

    def test_a_row_shows_its_age_only_from_two_days_on(self):
        fresh = TodoService.create(self.user, TodoTitleSchema(title="fresh"))
        old = TodoService.create(self.user, TodoTitleSchema(title="old"))
        Todo.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=3))
        Todo.objects.filter(pk=fresh.pk).update(created_at=timezone.now() - timedelta(days=1))
        page = self.client.get(reverse("board:index")).content.decode()
        self.assertIn('title="3 days on list">3d</span>', page)
        self.assertNotIn(">1d</span>", page)
        self.assertNotIn(">0d</span>", page)
