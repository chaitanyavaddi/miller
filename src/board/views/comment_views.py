from django.views import View

from board.mixins import BoardResponseMixin
from comments.schemas import CommentCreateSchema
from comments.service import CommentService
from recordings.services import RecordingService
from todos.mixins import LiveTodoMixin, TodoPermissionRequiredMixin
from utils.view_helper import render_fragment, retarget


class CommentCreateView(LiveTodoMixin, TodoPermissionRequiredMixin, BoardResponseMixin, View):
    permission_required = "todos.comment_todo"

    def post(self, request, pk):
        data = CommentCreateSchema.from_post(request)
        if not data.is_valid():
            return render_fragment(request, "comments.form", {"todo_id": self.todo.id, "data": data,
                                                              "recorder": RecordingService.composer(request.user, self.todo)})
        CommentService.add(self.todo, data)
        return retarget(self.render_thread(self.todo.id, focus=True), "#thread")
