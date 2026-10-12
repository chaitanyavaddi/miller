from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.urls import reverse
from django.views import View

from recordings.mixins import RecordingPermissionRequiredMixin
from recordings.models import RecordingPart
from recordings.services import GroqService, NotNow, RecordingService, TranscriptionError
from utils.view_helper import render_fragment
from recordings.services.recording_service import MAX_CHUNK

MAX_VOICE = 1024 * 1024  # a voice note is a minute at most (~250 KB): this leaves room, and stops anything longer
from todos.mixins import LiveTodoMixin, TodoPermissionRequiredMixin

CHUNK_STATUS = {"ok": 200, "duplicate": 200, "gap": 409, "closed": 410}


class RecordingStartView(LiveTodoMixin, TodoPermissionRequiredMixin, View):
    """Start recording a meeting's call (or resume the one still going), only while it's on. Answers with
    where to send chunks, which part each track starts at, and where to ask how it's getting on."""
    permission_required = "todos.comment_todo"

    def post(self, request, pk):
        if request.user.is_guest:
            raise PermissionDenied("Sign in to record")
        try:
            recording, next_parts = RecordingService.start(request.user, self.todo)
        except NotNow as error:
            return JsonResponse({"error": str(error)}, status=409)
        return JsonResponse({
            "id":         recording.id,
            "chunk_url":  reverse("recordings:chunk", args=[recording.id]),
            "stop_url":   reverse("recordings:stop", args=[recording.id]),
            "status_url": reverse("recordings:status", args=[recording.id]),
            "next_parts": next_parts,
        })


class RecordingChunkView(RecordingPermissionRequiredMixin, View):
    """One chunk of one track's part, the audio as the request body: ?track=mic|call&part=0&seq=12&started=<ms>.
    Answers with the chunk it expects next, so the recorder can resend from there after a drop."""
    permission_required = "recordings.change_recording"

    def post(self, request, pk):
        track, query = request.GET.get("track", ""), request.GET
        if track not in RecordingPart.Track.values or not all(query.get(k, "").isdigit() for k in ("part", "seq", "started")):
            return JsonResponse({"error": "track, part, seq and started are required"}, status=400)
        if not request.body or len(request.body) > MAX_CHUNK:
            return JsonResponse({"error": "empty or too large"}, status=400)
        result, expected = RecordingService.append(
            self.recording, track, int(query["part"]), int(query["seq"]), int(query["started"]), request.body,
        )
        return JsonResponse({"result": result, "next": expected}, status=CHUNK_STATUS[result])


class RecordingStopView(RecordingPermissionRequiredMixin, View):
    permission_required = "recordings.change_recording"

    def post(self, request, pk):
        RecordingService.stop(self.recording)
        return JsonResponse({"status": self.recording.status})


class RecordingStatusView(RecordingPermissionRequiredMixin, View):
    """How a stopped recording is getting on, for the page to refresh the thread once its words are in."""
    permission_required = "recordings.change_recording"

    def get(self, request, pk):
        return JsonResponse({"status": self.recording.status})


class VoiceView(LiveTodoMixin, TodoPermissionRequiredMixin, View):
    """A voice note said into a todo's composer, the audio as the request body: its words come straight
    back, to land in the composer for the user to edit and send. Nothing is kept."""
    permission_required = "todos.comment_todo"

    def post(self, request, pk):
        if request.user.is_guest or not GroqService.is_configured():
            raise PermissionDenied("Voice isn't available")
        if not request.body or len(request.body) > MAX_VOICE:
            return JsonResponse({"error": "Keep a voice note to a minute."}, status=400)
        try:
            return JsonResponse({"text": RecordingService.words(request.body)})
        except TranscriptionError:
            return JsonResponse({"error": "Couldn't make out the words. Try again."}, status=502)


class TranscriptView(LiveTodoMixin, TodoPermissionRequiredMixin, View):
    """A meeting's transcripts, in a modal opened from its menu: never shown in the thread itself."""
    permission_required = "todos.view_todo"

    def get(self, request, pk):
        return render_fragment(request, "recordings.transcript", {"todo": self.todo, "recordings": RecordingService.transcripts(self.todo.id)})
