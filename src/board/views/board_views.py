from django.db.models import Count, Max, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie

from accounts.mixins import GuestSessionMixin, SignedInRequiredMixin
from board.mixins import BoardResponseMixin
from board.services import BoardService
from todos.models import Todo
from todos.services import TodoService
from voice.services.voice_service import VoiceService
from utils.view_helper import redirect_to


class HomeView(View):
    """The front door: what Miller is, with the real board below to try. Members go straight to their list, unless
    they came by the app's logo (?stay=1)."""

    def get(self, request):
        if request.user.is_authenticated and not request.user.is_guest and request.GET.get("stay") != "1":
            return redirect_to(request, "board:index")
        return render(request, "home.html")


@method_decorator(ensure_csrf_cookie, name="dispatch")
class BoardView(GuestSessionMixin, BoardResponseMixin, View):
    def get(self, request):
        context = BoardService.workspace(request.user, self.selected_id())
        context["voice"] = VoiceService.is_available(request.user)
        if context["voice"]:
            context["voice_usage"] = VoiceService.usage(request.user)
            context["voice_limited"] = context["voice_usage"]["full"]
            context["voice_limit_message"] = VoiceService.limit_message(request.user)
        return render(request, "board/index.html", context)


class BoardWorkspaceView(SignedInRequiredMixin, BoardResponseMixin, View):
    """The whole board as a fragment: reloaded when something outside it (settings, a calendar) changed it."""

    def get(self, request):
        return self.render_workspace(self.selected_id())


class BoardStampView(SignedInRequiredMixin, View):
    """A fingerprint of the user's meetings, polled by the open board: when a calendar push changes
    one (moved, renamed, cancelled), or one's time runs out, the stamp changes and the board reloads
    itself. Ending meetings are closed here too, so one turns done within a poll of its end."""

    def get(self, request):
        TodoService.close_ended_meetings(request.user)
        meetings = Todo.objects.filter(user=request.user, kind=Todo.Kind.MEETING).aggregate(
            count=Count("id"), changed=Max("updated_at"), closed=Count("id", filter=Q(closed_at__isnull=False)),
        )
        changed = meetings["changed"].isoformat() if meetings["changed"] else ""
        return JsonResponse({"stamp": f"{meetings['count']}:{meetings['closed']}:{changed}"})
