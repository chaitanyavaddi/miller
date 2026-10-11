from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from accounts.mixins import SignedInRequiredMixin
from todos.services import TodoService


class TodoPermissionRequiredMixin(SignedInRequiredMixin):
    """Resolves the `pk` URL kwarg to `self.todo` and requires a `todos.*` permission on it."""
    permission_required = None

    def get_todo_queryset(self):
        return TodoService.open_queryset()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.todo = get_object_or_404(self.get_todo_queryset(), pk=kwargs.get("pk"))
        if self.permission_required and not request.user.has_perm(self.permission_required, self.todo):
            raise PermissionDenied(f"Missing permission: {self.permission_required}")
        return super().dispatch(request, *args, **kwargs)


class LiveTodoMixin:
    """For what still works on a todo once it's done: reading its thread, renaming, commenting."""

    def get_todo_queryset(self):
        return TodoService.live_queryset()
