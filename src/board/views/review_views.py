from django.views import View

from accounts.mixins import SignedInRequiredMixin
from board.mixins import BoardResponseMixin
from board.services import ReviewService
from todos.mixins import TodoPermissionRequiredMixin
from todos.services import TodoService
from utils.view_helper import render_fragment, render_fragments


class ReviewView(SignedInRequiredMixin, View):
    """Past: the rail of days and what was closed on the one picked (?day=2026-10-14)."""

    def get(self, request):
        day = ReviewService.parse_day(request.GET.get("day"))
        return render_fragment(request, "board.review", ReviewService.build(request.user, day))


class ReviewTodoMixin(TodoPermissionRequiredMixin):
    """All todos works on the user's closed todos; open and deleted ones never show."""

    def get_todo_queryset(self):
        return TodoService.closed_queryset()


class ReviewThreadView(ReviewTodoMixin, View):
    """One todo's thread, read-only, in place of the review table."""
    permission_required = "todos.view_todo"

    def get(self, request, pk):
        return render_fragment(request, "board.review-thread", ReviewService.thread(self.todo))


class ReviewDeleteView(ReviewTodoMixin, BoardResponseMixin, View):
    """Delete from All todos: it leaves the list (kept in the database, soft-deleted)."""
    permission_required = "todos.delete_todo"

    def post(self, request, pk):
        day = ReviewService.row(self.todo).day   # back to the day it was on
        TodoService.delete(self.todo)
        built = ReviewService.build(request.user, ReviewService.parse_day(day))
        return render_fragments(request, ("board.review", built), self.workspace_oob())
