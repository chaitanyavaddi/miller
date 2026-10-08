from django.urls import reverse

from board.services import BoardService
from todos.models import Todo
from utils.view_helper import push_url, render_fragment


class BoardResponseMixin:
    """Renders board fragments. The page sends the selected todo (`t`) with every list action, so
    adding, closing or reordering never changes what is being read."""

    def selected_id(self) -> int | None:
        source = self.request.POST if self.request.method == "POST" else self.request.GET
        raw = source.get("t", "")
        return int(raw) if raw.isdigit() else None

    def render_workspace(self, selected_id, undo=None):
        context = BoardService.workspace(self.request.user, selected_id, undo)
        response = render_fragment(self.request, "board.workspace", context)
        return push_url(response, self.board_url(context["current"]))

    def workspace_oob(self):
        """The board behind a modal, refreshed in the same response as the modal itself."""
        context = BoardService.workspace(self.request.user, self.selected_id())
        context["oob"] = True
        return "board.workspace", context

    def render_thread(self, selected_id, focus=False, push=False):
        context = BoardService.workspace(self.request.user, selected_id)
        context["focus"] = focus
        response = render_fragment(self.request, "board.thread", context)
        return push_url(response, self.board_url(context["current"])) if push else response

    def render_after_removal(self, removed):
        """Only a delete offers Undo: a closed todo is right there in the done rows, with its own reopen."""
        selected_id = BoardService.selection_after_removal(self.request.user, removed, self.selected_id())
        undo = removed if removed.removal == Todo.Removal.DELETED else None
        return self.render_workspace(selected_id, undo)

    @staticmethod
    def board_url(current) -> str:
        url = reverse("board:index")
        return f"{url}?t={current.id}" if current else url
