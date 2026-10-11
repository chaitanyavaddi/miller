from django.http import HttpResponse
from django.views import View

from accounts.mixins import SignedInRequiredMixin
from scheduled.schemas import ScheduledSaveSchema
from scheduled.services import ScheduleService
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
