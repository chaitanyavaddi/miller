from django.http import HttpResponse
from django.views import View

from django.urls import reverse
from django.utils import timezone

from accounts.mixins import SignedInRequiredMixin
from scheduled.schemas import MoveSchema, ScheduledSaveSchema
from scheduled.services import ScheduleService
from todos.mixins import TodoPermissionRequiredMixin
from utils.view_helper import render_fragment, retarget

# Close the modal, and have the board behind it reload: anything due today lands on it.
SAVED = '{"overlay-close": {"target": "body"}, "board-changed": {"target": "body"}}'


class ScheduledView(SignedInRequiredMixin, View):
    def get(self, request):
        return render_fragment(request, "scheduled.modal", ScheduleService.modal(request.user))


class ScheduledSaveView(SignedInRequiredMixin, View):
    """Save in the modal: every change at once, or none. Refused, the modal comes back saying why."""

    def post(self, request):
        data = ScheduledSaveSchema.from_post(request)
        if not data.is_valid():
            return retarget(render_fragment(request, "scheduled.modal", ScheduleService.modal(request.user)), "#overlay", "innerHTML")
        ScheduleService.save(request.user, data.changes)
        response = HttpResponse()
        response["HX-Trigger"] = SAVED
        return response


class MoveView(TodoPermissionRequiredMixin, View):
    """Move to…: one Scheduled row (title, date, Save) that takes one of the user's open todos off today
    until a date. The same todo, thread and all, comes back that day."""
    permission_required = "todos.change_todo"

    def get(self, request, pk):
        return render_fragment(request, "scheduled.move", self.context())

    def post(self, request, pk):
        data = MoveSchema.from_post(request)
        if not data.is_valid():
            return retarget(render_fragment(request, "scheduled.move", self.context()), "#overlay", "innerHTML")
        ScheduleService.move(self.todo, data.on_date)
        response = HttpResponse()
        response["HX-Trigger"] = SAVED
        return response

    def context(self) -> dict:
        today = timezone.localdate()
        return {
            "row":      {"title": self.todo.title, "rhythm": "once", "moved": True},
            "months":   ScheduleService.move_months(today),
            "weekdays": [],
            "action":   reverse("scheduled:move", args=[self.todo.id]),
        }
