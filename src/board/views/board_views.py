from django.db.models import Count, Max
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie

from accounts.mixins import GuestSessionMixin, SignedInRequiredMixin
from board.mixins import BoardResponseMixin
from board.services import BoardService
from todos.models import Todo
from utils.view_helper import redirect_to


class HomeView(View):
    """The front door: what Miller is, with the real board below to try. Members go straight to their list."""

    def get(self, request):
        if request.user.is_authenticated and not request.user.is_guest:
            return redirect_to(request, "board:index")
        return render(request, "home.html")


@method_decorator(ensure_csrf_cookie, name="dispatch")
class BoardView(GuestSessionMixin, BoardResponseMixin, View):
    def get(self, request):
        return render(request, "board/index.html", BoardService.workspace(request.user, self.selected_id()))


class BoardWorkspaceView(SignedInRequiredMixin, BoardResponseMixin, View):
    """The whole board as a fragment: reloaded when something outside it (settings, a calendar) changed it."""

    def get(self, request):
        return self.render_workspace(self.selected_id())


class BoardStampView(SignedInRequiredMixin, View):
    """A fingerprint of the user's meetings, polled by the open board: when a calendar push changes
    one (moved, renamed, cancelled), the stamp changes and the board reloads itself."""

    def get(self, request):
        meetings = Todo.objects.filter(user=request.user, kind=Todo.Kind.MEETING).aggregate(count=Count("id"), changed=Max("updated_at"))
        changed = meetings["changed"].isoformat() if meetings["changed"] else ""
        return JsonResponse({"stamp": f"{meetings['count']}:{changed}"})
