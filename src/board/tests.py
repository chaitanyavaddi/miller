import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.services import GuestService, IdentityService, PreferenceService
from calendars.models import CalendarConnection
from calendars.services import CalendarApiError, CalendarService, TokenVault
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

    def test_the_thread_menu_offers_move_to_and_delete(self):
        page = self.client.get(self.at(self.todos[1]))
        self.assertContains(page, 'data-action="thread-menu"')
        self.assertContains(page, f'hx-post="{self.url("todo-close", self.todos[1])}"', count=2)  # its row's tick and the thread's Mark done
        self.assertContains(page, f'hx-get="{reverse("scheduled:move", args=[self.todos[1].id])}"')
        self.assertContains(page, f'hx-post="{self.url("todo-delete", self.todos[1])}"')
        self.assertNotContains(page, 'aria-label="Delete todo"')  # no longer beside the title

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
        self.assertIn('class="todo-row__tick todo-row__tick--done todo-row__tick--reopen" aria-label="todo 1: done — reopen"', html)   # its tick, on the right
        self.assertIn('<span class="todo-row__pos">01</span>', html)        # the done one keeps its number…
        self.assertIn('<span class="todo-row__pos">02</span>', html)        # …and closing it changes no one's
        self.assertIn('aria-label="Mark todo 2 done"', html)
        thread = self.client.get(self.url("todo-detail", self.todos[0])).content.decode()
        self.assertIn("Done today</b>", thread)                                        # done, not a number or its age
        self.assertIn('>Mark not done</button>', thread)
        self.client.post(self.url("todo-reopen", self.todos[0]), {"index": 99})
        self.assertEqual(list(TodoService.open_for(self.user).values_list("title", flat=True)), ["todo 1", "todo 2", "todo 3"])

    def test_done_before_today_is_not_on_todays_list_at_all(self):
        TodoService.close(self.todos[0])
        Todo.objects.filter(pk=self.todos[0].pk).update(closed_at=timezone.now() - timedelta(days=1))
        html = self.client.get(self.url("index")).content.decode()
        self.assertNotIn("todo 1", html)
        self.assertNotIn("todo-row--closed", html)

    def test_deleting_shows_a_quiet_undo_notice_and_undo_clears_it(self):
        response = self.client.post(self.url("todo-delete", self.todos[0]))
        self.assertContains(response, 'class="toast__item"')
        self.assertContains(response, "Deleted “todo 1”")
        response = self.client.post(self.url("todo-reopen", self.todos[0]), {"index": 1})
        self.assertNotContains(response, 'class="toast__item"')

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

    def test_a_todo_done_today_can_still_be_deleted(self):
        self.client.post(self.url("todo-close", self.todos[0]))
        response = self.client.post(self.url("todo-delete", self.todos[0]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "toast__item")
        self.todos[0].refresh_from_db()
        self.assertIsNotNone(self.todos[0].deleted_at)

    def test_the_thread_marks_done_with_a_button(self):
        page = self.client.get(self.url("index"))
        self.assertContains(page, '>Mark done</button>')
        self.assertContains(page, 'class="account-menu thread-menu" id="thread-menu"')  # the menu is drawn as the account menu

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

    def test_all_todos_opens_on_today_with_what_was_closed_today(self):
        TodoService.close(self.todos[0])
        TodoService.close(self.todos[1])
        Todo.objects.filter(pk=self.todos[0].pk).update(closed_at=timezone.now() - timedelta(days=2))
        response = self.client.get(self.url("review"))
        self.assertEqual([row.title for row in response.context["rows"]], ["todo 2"])  # todo 1 is on its own day; todo 3 is open
        self.assertContains(response, 'id="timeline-title">All todos</span>')
        self.assertContains(response, 'aria-label="Reopen"', count=1)
        day = (timezone.localdate() - timedelta(days=2)).isoformat()
        older = self.client.get(self.url("review"), {"day": day})
        self.assertEqual([row.title for row in older.context["rows"]], ["todo 1"])

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
        self.assertContains(response, f'?day={timezone.localdate().isoformat()}"')   # back to its day
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




class ReviewRailTests(TestCase):
    """All todos → Past: a rail of days, each with how many were done, and the chosen day's todos in the order they were done."""

    def setUp(self):
        from board.services import ReviewService
        self.review = ReviewService
        self.user = IdentityService.create_user("rail@x.com")
        self.client.force_login(self.user)
        self.today = timezone.localdate()

    def done(self, title, days_ago=0, minutes=0, kind=Todo.Kind.TASK):
        todo = TodoService.create(self.user, TodoTitleSchema(title=title))
        when = timezone.now().replace(hour=12, minute=0) - timedelta(days=days_ago) + timedelta(minutes=minutes)
        Todo.objects.filter(pk=todo.pk).update(closed_at=when, kind=kind)
        return todo

    def test_the_rail_runs_a_month_back_to_today_with_a_count_on_each_day(self):
        self.done("a", 3), self.done("b", 3), self.done("c", 1)
        built = self.review.build(self.user)
        rail = {d.value: d for d in built["rail"]}
        self.assertEqual(len(built["rail"]), 30)
        self.assertEqual(built["rail"][-1].value, self.today.isoformat())
        self.assertTrue(built["rail"][-1].is_today)
        self.assertEqual(rail[(self.today - timedelta(days=3)).isoformat()].count, 2)
        self.assertEqual(rail[(self.today - timedelta(days=2)).isoformat()].count, 0)
        self.assertTrue(built["rail"][0].month)   # its first day says the month

    def test_it_reaches_back_to_the_first_day_something_was_done_but_no_more_than_a_year(self):
        self.done("old", 90)
        self.assertEqual(self.review.build(self.user)["rail"][0].value, (self.today - timedelta(days=90)).isoformat())
        self.done("ancient", 800)
        self.assertEqual(len(self.review.build(self.user)["rail"]), 365)

    def test_with_nothing_done_today_it_opens_on_the_last_day_something_was(self):
        self.done("c", 4)
        built = self.review.build(self.user)
        self.assertEqual(built["day"], (self.today - timedelta(days=4)).isoformat())
        self.assertEqual([r.title for r in built["rows"]], ["c"])
        self.assertEqual(sum(d.is_on for d in built["rail"]), 1)

    def test_a_days_todos_come_in_the_order_they_were_done_with_their_notes(self):
        second, first = self.done("second", 1, minutes=30), self.done("first", 1)
        Comment.objects.create(todo=second, body="a note")
        Comment.objects.create(todo=second, body="Moved to Fri", automatic=True)   # Miller's own notes don't count
        rows = self.review.build(self.user, self.today - timedelta(days=1))["rows"]
        self.assertEqual([(r.title, r.notes) for r in rows], [("first", 0), ("second", 1)])

    def test_meetings_are_in_the_days_list_with_the_tasks(self):
        self.done("task", 0, minutes=-30)
        self.done("Standup", 0, kind=Todo.Kind.MEETING)
        built = self.review.build(self.user)
        self.assertEqual([r.title for r in built["rows"]], ["task", "Standup"])
        self.assertEqual(built["rail"][-1].count, 2)

    def test_a_day_off_the_rail_or_unreadable_falls_back(self):
        self.done("x", 0)
        for raw in ("2099-01-01", "nonsense", ""):
            response = self.client.get(reverse("board:review"), {"day": raw})
            self.assertEqual(response.context["day"], self.today.isoformat(), raw)

    def test_the_page_draws_the_rail_and_says_the_day(self):
        self.done("x", 1)
        response = self.client.get(reverse("board:review"), {"day": (self.today - timedelta(days=1)).isoformat()})
        self.assertContains(response, "data-rail-days")
        self.assertContains(response, "Yesterday · 1 done")
        self.assertContains(response, 'aria-current="date"', count=1)
        self.assertContains(self.client.get(reverse("board:review"), {"day": (self.today - timedelta(days=2)).isoformat()}), "Nothing done on this day.")


class MeetingBoardTests(TestCase):
    def setUp(self):
        self.user = IdentityService.create_user("d@x.com", name="Dee", show_meetings=True, timezone="UTC")
        self.client.force_login(self.user)
        TodoService.create(self.user, TodoTitleSchema(title="write the plan"))
        start = (timezone.now() + timedelta(minutes=30)).replace(second=0, microsecond=0)
        self.meeting = Todo.objects.create(user=self.user, kind=Todo.Kind.MEETING, title="Hiring panel", starts_at=start,
                                           ends_at=start + timedelta(hours=1), meeting_url="https://zoom.us/j/1",
                                           event_url="https://www.google.com/calendar/event?eid=abc")

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

    def test_a_meeting_row_links_out_to_its_calendar_event(self):
        page = self.client.get(reverse("board:index"))
        self.assertContains(page, 'class="todo-row__tick todo-row__out" href="https://www.google.com/calendar/event?eid=abc" target="_blank"')

    def test_the_meeting_stamp_moves_when_a_meeting_ends(self):
        before = self.client.get(reverse("board:stamp")).json()["stamp"]
        Todo.objects.filter(pk=self.meeting.pk).update(starts_at=timezone.now() - timedelta(hours=2), ends_at=timezone.now() - timedelta(minutes=1))
        after = self.client.get(reverse("board:stamp")).json()["stamp"]
        self.assertNotEqual(after, before)
        self.meeting.refresh_from_db()
        self.assertIsNotNone(self.meeting.closed_at)  # closed by the poll itself, so the reload shows it done

    def test_the_meeting_stamp_moves_when_a_meeting_changes(self):
        before = self.client.get(reverse("board:stamp")).json()["stamp"]
        self.meeting.delete()
        self.assertNotEqual(self.client.get(reverse("board:stamp")).json()["stamp"], before)

    def test_an_ended_meeting_shows_the_done_tick_with_nothing_to_reopen(self):
        midnight = TodoService.start_of_today()
        ended = Todo.objects.create(user=self.user, kind=Todo.Kind.MEETING, title="Earlier sync", starts_at=midnight, ends_at=midnight + timedelta(seconds=1))
        page = self.client.get(reverse("board:index")).content.decode()
        self.assertIn('class="todo-row__tick todo-row__tick--done" title="Ended"', page)
        self.assertNotIn(f'hx-post="{reverse("board:todo-reopen", args=[ended.id])}"', page)

    def test_a_meeting_cannot_be_closed_or_renamed(self):
        self.assertEqual(self.client.post(reverse("board:todo-close", args=[self.meeting.id])).status_code, 403)
        self.assertEqual(self.client.post(reverse("board:todo-rename", args=[self.meeting.id]), {"title": "x"}).status_code, 403)


@override_settings(CALENDAR_TOKEN_KEY="aZ6JYqxxDyvXKY1bDSL0iZ3cN3g9cRQX9hX0_9gXQGQ=", GOOGLE_CLIENT_ID="id", GOOGLE_CLIENT_SECRET="secret")
class SettingsTests(TestCase):
    def setUp(self):
        self.user = IdentityService.create_user("s@x.com", name="Sam")
        self.client.force_login(self.user)

    def test_the_account_menu_opens_settings(self):
        self.assertContains(self.client.get(reverse("board:index")), f'hx-get="{reverse("board:settings")}"')

    @override_settings(MICROSOFT_CLIENT_ID="id", MICROSOFT_CLIENT_SECRET="secret")
    def test_only_google_is_offered_for_now(self):
        response = self.client.get(reverse("board:settings"))
        self.assertContains(response, reverse("calendars:connect", args=["google"]))
        self.assertNotContains(response, reverse("calendars:connect", args=["microsoft"]))

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
        self.assertContains(response, "Sign in to connect")
        self.assertNotContains(response, 'name="show_meetings"')

    @patch.object(CalendarService, "access_token", side_effect=CalendarApiError("down"))
    def test_disconnecting_a_calendar_says_so(self, access_token):
        PreferenceService.set_show_meetings(self.user, True)
        connection = CalendarConnection.objects.create(user=self.user, provider="google", email="s@x.com", refresh_token=TokenVault.seal("r"))
        response = self.client.post(reverse("board:settings-disconnect", args=[connection.id]))
        self.assertContains(response, "Disconnected s@x.com.")
        self.assertFalse(CalendarConnection.objects.exists())

    def test_each_connected_calendar_shows_as_a_pill_with_a_link_out(self):
        CalendarConnection.objects.create(user=self.user, provider="google", email="work@x.com", refresh_token="x")
        response = self.client.get(reverse("board:settings"))
        self.assertContains(response, 'class="calendar-pill__email" title="work@x.com"')
        self.assertContains(response, "https://calendar.google.com/calendar/u/0/r?authuser=work%40x.com")
        self.assertContains(response, "+ Add calendar")
        self.assertNotContains(response, 'name="show_meetings"')  # connecting is what brings meetings in

    def test_an_account_that_lost_access_offers_a_reconnect(self):
        PreferenceService.set_show_meetings(self.user, True)
        CalendarConnection.objects.create(user=self.user, provider="google", email="s@x.com", refresh_token="x", status="disconnected")
        response = self.client.get(reverse("board:settings"))
        self.assertContains(response, "Lost access")
        self.assertContains(response, reverse("calendars:connect", args=["google"]) + "?hint=s%40x.com")

    def test_someone_elses_calendar_cant_be_disconnected(self):
        other = IdentityService.create_user("o@x.com")
        connection = CalendarConnection.objects.create(user=other, provider="google", email="o@x.com", refresh_token="x")
        self.assertEqual(self.client.post(reverse("board:settings-disconnect", args=[connection.id])).status_code, 403)
        self.assertTrue(CalendarConnection.objects.filter(pk=connection.pk).exists())


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


class HomeTests(TestCase):
    def test_visitors_get_the_home_page_with_a_preview_and_a_way_in(self):
        response = self.client.get("/")
        self.assertContains(response, "Today, in order.")
        self.assertContains(response, f'href="{reverse("board:index")}"')
        self.assertContains(response, reverse("accounts:login"))

    def test_members_go_straight_to_their_list(self):
        self.client.force_login(IdentityService.create_user("m@x.com"))
        self.assertRedirects(self.client.get("/"), reverse("board:index"), fetch_redirect_response=False)

    def test_the_old_home_address_moves_to_the_root(self):
        self.assertRedirects(self.client.get("/home/"), "/", status_code=301, fetch_redirect_response=False)


class LegalPageTests(TestCase):
    def test_the_home_page_links_to_privacy_and_terms(self):
        response = self.client.get("/")
        self.assertContains(response, f'href="{reverse("privacy")}"')
        self.assertContains(response, f'href="{reverse("terms")}"')

    def test_privacy_states_the_google_limited_use_and_ai_commitments(self):
        page = self.client.get(reverse("privacy"))
        self.assertContains(page, "including the Limited Use requirements")
        self.assertContains(page, "Google Workspace APIs are not used to develop, improve, or train non-personalized AI and/or ML models.")
        self.assertContains(page, "calendar.events.readonly")

    def test_the_app_links_to_privacy_and_terms_from_the_account_menu(self):
        page = self.client.get(reverse("board:index"))
        self.assertContains(page, f'href="{reverse("privacy")}" target="_blank"')
        self.assertContains(page, f'href="{reverse("terms")}" target="_blank"')

    def test_terms_are_public(self):
        self.assertEqual(self.client.get(reverse("terms")).status_code, 200)


class SignInPageTests(TestCase):
    @override_settings(GOOGLE_CLIENT_ID="id", GOOGLE_CLIENT_SECRET="secret")
    def test_the_logo_leads_home(self):
        self.assertContains(self.client.get(reverse("accounts:login")), f'class="signin__home" href="{reverse("board:home")}"')


class NotFoundPageTests(TestCase):
    def test_a_missing_page_looks_like_the_home_page(self):
        response = self.client.get("/no-such-page/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Nothing here.", status_code=404)
        self.assertContains(response, f'href="{reverse("board:home")}"', status_code=404)
