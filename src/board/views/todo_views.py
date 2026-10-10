from django.views import View

from accounts.mixins import SignedInRequiredMixin
from board.mixins import BoardResponseMixin
from board.services import BoardService
from todos import reminders
from todos.mixins import TodoPermissionRequiredMixin
from todos.schemas import TodoReminderSchema, TodoReopenSchema, TodoReorderSchema, TodoTitleSchema
from todos.services import TodoService
from utils.view_helper import close_overlay, render_fragment, retarget


class TodoListView(SignedInRequiredMixin, BoardResponseMixin, View):
    def get(self, request):
        query = request.GET.get("q", "").strip()
        if query:
            context = {"rows": BoardService.search(request.user, query), "searching": True}
        else:
            context = BoardService.listing(request.user)
        context["current"] = self.selected_id()
        return render_fragment(request, "todos.rows", context)


class TodoCreateView(SignedInRequiredMixin, BoardResponseMixin, View):
    def post(self, request):
        data = TodoTitleSchema.from_post(request)
        reminder = TodoReminderSchema.from_post(request)
        if not all([data.is_valid(), reminder.is_valid()]):
            return render_fragment(request, "todos.form", {"data": data, "reminder": reminder, "presets": reminders.presets()})
        todo = TodoService.create(request.user, data)
        if reminder.remind_at is not None:
            TodoService.remind(todo, reminder)
        return retarget(self.render_workspace(self.selected_id()), "#workspace")


class TodoDetailView(TodoPermissionRequiredMixin, BoardResponseMixin, View):
    permission_required = "todos.view_todo"

    def get(self, request, pk):
        return self.render_thread(self.todo.id, focus=True, push=True)


class TodoRenameView(TodoPermissionRequiredMixin, View):
    permission_required = "todos.change_todo"

    def post(self, request, pk):
        data = TodoTitleSchema.from_post(request)
        renamed = data.is_valid()
        if renamed:
            TodoService.rename(self.todo, data)
        return render_fragment(request, "todos.title-form", {"todo": self.todo, "renamed": renamed})


class TodoRemindView(TodoPermissionRequiredMixin, BoardResponseMixin, View):
    """Set or clear a reminder. The board comes back whole: the todo may now sit under another day."""
    permission_required = "todos.change_todo"

    def post(self, request, pk):
        data = TodoReminderSchema.from_post(request)
        if not data.is_valid():
            row = BoardService.select(BoardService.rows(request.user), self.todo.id)
            return retarget(render_fragment(request, "todos.reminder-form", {"row": row, "presets": reminders.presets()}), "#reminder")
        TodoService.remind(self.todo, data)
        return self.render_workspace(self.selected_id())


class TodoCloseView(TodoPermissionRequiredMixin, BoardResponseMixin, View):
    permission_required = "todos.change_todo"

    def post(self, request, pk):
        return self.render_after_removal(TodoService.close(self.todo))


class TodoDeleteView(TodoPermissionRequiredMixin, BoardResponseMixin, View):
    permission_required = "todos.delete_todo"

    def post(self, request, pk):
        return self.render_after_removal(TodoService.delete(self.todo))


class TodoReopenView(TodoPermissionRequiredMixin, BoardResponseMixin, View):
    permission_required = "todos.change_todo"

    def get_todo_queryset(self):
        return TodoService.removed_queryset()

    def post(self, request, pk):
        data = TodoReopenSchema.from_post(request)
        TodoService.reopen(self.todo, data)
        if data.select:
            return close_overlay(self.render_workspace(self.todo.id))
        return self.render_workspace(self.selected_id())


class TodoReorderView(SignedInRequiredMixin, BoardResponseMixin, View):
    def post(self, request):
        """Saves the order, then returns the selected todo's thread: its "#n today" depends on that order."""
        TodoService.reorder(request.user, TodoReorderSchema.from_post(request))
        return self.render_thread(self.selected_id(), focus=True, push=True)
