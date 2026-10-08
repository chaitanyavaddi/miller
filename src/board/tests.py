import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.services import IdentityService
from comments.models import Comment
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
