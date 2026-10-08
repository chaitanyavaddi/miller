from django.views import View

from accounts.mixins import SignedInRequiredMixin
from board.mixins import BoardResponseMixin
from board.services import ReviewService
from todos.mixins import TodoPermissionRequiredMixin
from todos.schemas import ReviewFilterSchema
from todos.services import TodoService
from utils.view_helper import render_fragment, render_fragments


class ReviewView(SignedInRequiredMixin, View):
    def get(self, request):
        filters = ReviewFilterSchema.from_request(request)
        return render_fragment(request, "board.review", ReviewService.build(request.user, filters))


class ReviewTodoMixin(TodoPermissionRequiredMixin):
    """Review works on any of the user's todos: open, closed or deleted."""

    def get_todo_queryset(self):
        return TodoService.any_queryset()


class ReviewThreadView(ReviewTodoMixin, View):
    """One todo's thread, read-only, in place of the review table."""
    permission_required = "todos.view_todo"

    def get(self, request, pk):
        filters = ReviewFilterSchema.from_request(request)
        return render_fragment(request, "board.review-thread", ReviewService.thread(self.todo, filters))


class ReviewDeleteView(ReviewTodoMixin, BoardResponseMixin, View):
    """Delete from the review: an open or closed todo moves to Deleted; a deleted one goes for good."""
    permission_required = "todos.delete_todo"

    def post(self, request, pk):
        if self.todo.deleted_at:
            TodoService.purge(self.todo)
        else:
            TodoService.delete(self.todo)
        filters = ReviewFilterSchema.from_request(request)
        return render_fragments(request, ("board.review", ReviewService.build(request.user, filters)), self.workspace_oob())
