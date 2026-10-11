from django.views import View

from accounts.mixins import SignedInRequiredMixin
from board.mixins import BoardResponseMixin
from board.services import ReviewService
from todos.mixins import TodoPermissionRequiredMixin
from todos.services import TodoService
from utils.view_helper import render_fragment, render_fragments


class ReviewView(SignedInRequiredMixin, View):
    def get(self, request):
        return render_fragment(request, "board.review", ReviewService.build(request.user))


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
        TodoService.delete(self.todo)
        return render_fragments(request, ("board.review", ReviewService.build(request.user)), self.workspace_oob())
