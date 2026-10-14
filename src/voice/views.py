import re
import time

from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views import View

from accounts.mixins import SignedInRequiredMixin
from live.bus import publish
from recordings.services import RecordingService, TranscriptionError
from voice.models import VoiceCommand
from voice.services.command_service import CommandService
from voice.services.decision_service import DecisionError, DecisionService
from voice.services.voice_service import VoiceService

MAX_AUDIO = 1024 * 1024  # a spoken command is seconds long; this stops anything much longer
# What Whisper "hears" in a room's noise or a silence. Never a command, so never shown or acted on.
# ("Thank you" alone is not here: it is how someone says they're done.)
NOT_SPEECH = {"thank you for watching", "thanks for watching", "you", "okay", "ok", "uh", "um", "hmm"}
# The recording formats a browser makes: webm and ogg (Chrome, Firefox, Android), mp4 (Safari, iPhones).
SUFFIXES = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".mp4", "audio/x-m4a": ".m4a", "audio/wav": ".wav"}


def is_noise(transcript: str) -> bool:
    words = re.sub(r"[^a-z ]", "", transcript.lower()).strip()
    return not words or words in NOT_SPEECH


def whole(raw: str) -> int | None:
    return int(raw) if raw.isdigit() else None


class VoiceCommandView(SignedInRequiredMixin, View):
    """A spoken command, the audio as the request body. A member's page hears the answer as it forms, over
    its live stream (voice-heard, then todos-changed and voice-result); a guest's has no stream, so it takes
    the result from this response, which carries the same thing."""

    def post(self, request):
        user = request.user
        if not VoiceService.is_available(user):
            raise PermissionDenied("Voice isn't available")
        if not request.body or len(request.body) > MAX_AUDIO:
            return JsonResponse({"error": "Keep it to a few seconds."}, status=400)
        turn = request.headers.get("X-Voice-Turn", "")[:40]
        started = time.monotonic()
        if VoiceService.over_budget(user):
            return JsonResponse({"turn": turn, "reply": VoiceService.limit_message(user), "outcome": "limit", "undo": None,
                                 "usage": VoiceService.usage(user)})
        live = not user.is_guest   # only a member's page holds a live stream
        command = VoiceCommand(user=user, session=request.headers.get("X-Voice-Session", "")[:40])

        def tell(name, data=None):
            if live:
                publish(user.pk, name, data)

        def finish(reply: str, outcome: str, applied=None):
            command.reply, command.outcome = reply, outcome
            command.total_ms = int((time.monotonic() - started) * 1000)
            command.save()
            changed = bool(applied and applied.undo)   # a change to take back; opening a panel isn't one
            # What the page needs to show the change as it lands: which thread to open, which rows to light.
            hint = {"show": applied.show, "touched": applied.touched, "read": applied.read} if changed else None
            if changed:
                tell("todos-changed", hint)
            result = {"turn": turn, "reply": reply, "outcome": outcome, "undo": command.pk if changed else None,
                      "heard": command.transcript, "usage": VoiceService.usage(user), **(hint or {}),
                      **({"go": applied.go} if applied and applied.go else {})}
            tell("voice-result", result)
            return JsonResponse(result)

        todos = CommandService.todos_for(user)
        suffix = SUFFIXES.get(request.headers.get("X-Voice-Mime", "").split(";")[0].strip(), ".webm")
        try:
            command.transcript, command.audio_seconds = RecordingService.listen(request.body, CommandService.vocabulary(todos), suffix)
        except TranscriptionError:
            return finish("Couldn't make out the words. Try again.", VoiceCommand.Outcome.FAILED)
        command.stt_cost = VoiceService.speech_cost(command.audio_seconds)
        command.heard_ms = int((time.monotonic() - started) * 1000)
        if is_noise(command.transcript):
            return finish("", VoiceCommand.Outcome.NOTHING)  # silence says nothing: the page just keeps listening
        tell("voice-heard", {"turn": turn, "text": command.transcript})

        history = CommandService.history(user, command.session)
        done_earlier = CommandService.done_earlier(user)
        open_id = whole(request.headers.get("X-Voice-Open", ""))   # the thread on screen: what "this one" means
        try:
            decision, command.decide_ms = DecisionService.decide(
                command.transcript, todos, history, notes=CommandService.notes_for(todos), open_id=open_id, done_earlier=done_earlier, today=timezone.localdate())
        except DecisionError:
            return finish("Couldn't work that out. Try again.", VoiceCommand.Outcome.FAILED)
        command.model, command.llm_cost = decision.model, decision.cost
        command.tokens_in, command.tokens_out = decision.tokens_in, decision.tokens_out
        command.operations = [o.as_dict() for o in decision.operations]

        valid, problems = CommandService.validate(todos, decision.operations, done_earlier)
        if not valid:
            if decision.reply:
                return finish(decision.reply, VoiceCommand.Outcome.ASKED)
            note = f"Couldn't do that: {problems[0]}." if problems else "Nothing to change."
            return finish(note, VoiceCommand.Outcome.NOTHING)
        applied = CommandService.apply(user, valid)
        command.applied, command.undo = applied.done, applied.undo
        reply = " · ".join(applied.lines) + (f" (skipped: {problems[0]})" if problems else "")
        return finish(reply, VoiceCommand.Outcome.APPLIED, applied)


class VoiceUndoView(SignedInRequiredMixin, View):
    """Take back a voice command: its changes reversed, newest first."""

    def post(self, request, pk):
        command = get_object_or_404(VoiceCommand, pk=pk, user=request.user)
        if not CommandService.undo(command):
            return JsonResponse({"reply": "Already undone."})
        touched = [o["todo_id"] for o in command.applied if o.get("todo_id")]   # the rows light up as they go back
        if not request.user.is_guest:
            publish(request.user.pk, "todos-changed", {"touched": touched})
        return JsonResponse({"reply": "Undone.", "touched": touched})
