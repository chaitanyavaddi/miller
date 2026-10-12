import logging
import shutil
from datetime import UTC, datetime, timedelta

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from comments.service import CommentService
from recordings.models import Recording, RecordingPart
from recordings.services.groq_service import GroqService, TranscriptionError

logger = logging.getLogger(__name__)

QUIET_FOR = timedelta(minutes=30)   # no chunk for this long: the tab closed (or went offline for good); wrap up
MAX_CHUNK = 4 * 1024 * 1024         # a 10-second chunk is ~40 KB; anything this big isn't one
CLAIM_FOR = timedelta(minutes=20)   # a run that died mid-transcription lets go after this
MAX_ATTEMPTS = 3                    # transcription failures (other than Groq's rate limit) before giving up
SAME_SPEAKER_GAP = 2.0              # seconds: closer than this, one speaker's segments read as one line
CALL_OPENS = timedelta(minutes=5)   # a meeting's call can be recorded from this long before it starts, until it ends


class NotNow(Exception):
    """A call can only be recorded while its meeting is on."""


class RecordingService:
    @staticmethod
    def call_open(todo) -> bool:
        """A meeting's call can be recorded from just before it starts until it ends: never after."""
        if not todo.is_meeting or not (todo.starts_at and todo.ends_at):
            return False
        return todo.starts_at - CALL_OPENS <= timezone.now() <= todo.ends_at

    @staticmethod
    def composer(user, todo) -> dict:
        """What a thread offers: on a todo, its composer's mic (say it, then edit the words before sending);
        on a meeting that's on, recording its call, from the thread itself. `todo` may be the board's row
        for it: the todo itself is read for its times."""
        from django.urls import reverse
        from recordings.services.groq_service import GroqService
        from todos.models import Todo
        if todo is None or user.is_guest or not GroqService.is_configured():
            return {"voice_url": "", "call_url": "", "call_resumable": False}
        if not isinstance(todo, Todo):
            todo = Todo.objects.get(pk=todo.id)
        call = RecordingService.call_open(todo)
        return {
            "voice_url":      "" if todo.is_meeting else reverse("recordings:voice", args=[todo.id]),
            "call_url":       reverse("recordings:start", args=[todo.id]) if call else "",
            "call_resumable": call and Recording.objects.filter(todo_id=todo.id, kind=Recording.Kind.CALL, status=Recording.Status.RECORDING).exists(),
        }

    @staticmethod
    def transcripts(todo_id: int) -> list[Recording]:
        """A meeting's transcribed calls, oldest first: kept out of the thread, opened from its menu."""
        return list(RecordingService._transcribed_calls(todo_id).order_by("created_at"))

    @staticmethod
    def has_transcript(todo_id: int) -> bool:
        return RecordingService._transcribed_calls(todo_id).exists()

    @staticmethod
    def _transcribed_calls(todo_id: int):
        return Recording.objects.filter(todo_id=todo_id, kind=Recording.Kind.CALL, status=Recording.Status.READY).exclude(transcript=[])

    @staticmethod
    def words(audio: bytes) -> str:
        """A short voice note, said into a composer: its words, now. Nothing is kept."""
        import tempfile
        from pathlib import Path
        from recordings.services.groq_service import GroqService
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "voice.webm"
            path.write_bytes(audio)
            return " ".join(segment["text"] for segment in GroqService.transcribe(path)).strip()

    @staticmethod
    def start(user, todo) -> tuple[Recording, dict]:
        """A meeting's call recording: the one still going (a resume), or a new one. Also where each track's
        next part begins, so a resumed recording adds parts instead of overwriting them."""
        if not RecordingService.call_open(todo):
            raise NotNow("A call can be recorded only while its meeting is on.")
        recording = Recording.objects.filter(user=user, todo=todo, kind=Recording.Kind.CALL, status=Recording.Status.RECORDING).first()
        if recording is None:
            recording = Recording.objects.create(user=user, todo=todo, kind=Recording.Kind.CALL, last_chunk_at=timezone.now())
        next_parts = {
            track: (recording.parts.filter(track=track).aggregate(last=Max("index"))["last"] + 1
                    if recording.parts.filter(track=track).exists() else 0)
            for track in RecordingPart.Track.values
        }
        return recording, next_parts

    @staticmethod
    @transaction.atomic
    def append(recording: Recording, track: str, index: int, seq: int, started_ms: int, data: bytes) -> tuple[str, int]:
        """Write one chunk at the end of its part. Returns ("ok" | "duplicate" | "gap" | "closed", the chunk
        it expects next). Chunks arrive in order, retried after a drop: a repeat is ignored, a gap refused."""
        if recording.status != Recording.Status.RECORDING:
            return "closed", 0
        started_at = datetime.fromtimestamp(started_ms / 1000, tz=UTC) if started_ms else timezone.now()
        part, _ = RecordingPart.objects.select_for_update().get_or_create(
            recording=recording, track=track, index=index, defaults={"started_at": started_at},
        )
        if seq < part.next_seq:
            return "duplicate", part.next_seq
        if seq > part.next_seq:
            return "gap", part.next_seq
        part.path.parent.mkdir(parents=True, exist_ok=True)
        with open(part.path, "ab") as audio:
            audio.write(data)
        part.next_seq += 1
        part.size += len(data)
        part.save(update_fields=["next_seq", "size", "updated_at"])
        Recording.objects.filter(pk=recording.pk).update(last_chunk_at=timezone.now())
        return "ok", part.next_seq

    @staticmethod
    def stop(recording: Recording) -> None:
        """Stopped: its transcription is queued straight away (once this is saved), not left for a sweep."""
        if recording.status == Recording.Status.RECORDING:
            recording.status, recording.ended_at = Recording.Status.PROCESSING, timezone.now()
            recording.save(update_fields=["status", "ended_at", "updated_at"])
            transaction.on_commit(lambda: RecordingService.enqueue(recording))

    @staticmethod
    def enqueue(recording: Recording) -> None:
        from django_q.tasks import async_task
        async_task("recordings.tasks.process_recording", recording.id, task_name=f"Transcribe recording {recording.id}")

    @staticmethod
    def wrap_up_quiet() -> int:
        """Recordings whose chunks stopped coming (the tab closed, the laptop slept): transcribe what came."""
        quiet = Recording.objects.filter(status=Recording.Status.RECORDING, last_chunk_at__lt=timezone.now() - QUIET_FOR)
        return quiet.update(status=Recording.Status.PROCESSING, interrupted=True, ended_at=timezone.now())

    @staticmethod
    def process_due() -> int:
        """The safety net: recordings gone quiet, and any still waiting (a job cut off by a restart).
        Returns how many were finished."""
        RecordingService.wrap_up_quiet()
        return sum(RecordingService.process_claimed(recording)
                   for recording in Recording.objects.filter(status=Recording.Status.PROCESSING).select_related("todo"))

    @staticmethod
    def process_claimed(recording: Recording) -> bool:
        """Transcribe it if no one else is: claimed first, so the queued job and the sweep never transcribe
        the same audio twice. A claim left by a job that died lapses after a while."""
        now = timezone.now()
        claimed = (Recording.objects.filter(pk=recording.pk, status=Recording.Status.PROCESSING)
                   .filter(Q(claimed_at__isnull=True) | Q(claimed_at__lt=now - CLAIM_FOR)).update(claimed_at=now))
        if not claimed:
            return False
        try:
            return RecordingService.process(recording)
        finally:
            Recording.objects.filter(pk=recording.pk).update(claimed_at=None)

    @staticmethod
    def process(recording: Recording) -> bool:
        """Transcribe its parts (each saved as it's done, so a failure resumes where it stopped), then
        merge them into one transcript. False if it has to wait for a later run (Groq's rate limit)."""
        for part in recording.parts.filter(transcribed=False, size__gt=0):
            try:
                part.segments = GroqService.transcribe(part.path)
            except TranscriptionError as error:
                if error.retry_later:
                    logger.warning("Transcription of %s postponed: %s", part, error)
                    return False
                recording.attempts += 1
                recording.error = str(error)
                if recording.attempts >= MAX_ATTEMPTS:
                    recording.status = Recording.Status.FAILED
                    RecordingService._note(recording, "Recording couldn't be transcribed")
                recording.save(update_fields=["attempts", "error", "status", "updated_at"])
                return False
            part.transcribed = True
            part.save(update_fields=["segments", "transcribed", "updated_at"])
        RecordingService.finish(recording)
        return True

    @staticmethod
    @transaction.atomic
    def finish(recording: Recording) -> None:
        recording.transcript, recording.duration = RecordingService.merge(list(recording.parts.all()))
        recording.status, recording.error = Recording.Status.READY, ""
        recording.save(update_fields=["transcript", "duration", "status", "error", "updated_at"])
        transaction.on_commit(lambda: RecordingService.discard_audio(recording))
        minutes = max(1, round(recording.duration / 60)) if recording.duration else 0
        note = f"Recording · {minutes} min" if minutes else "Recording · nothing was said"
        if recording.interrupted:
            note += " · stopped when the tab closed"
        RecordingService._note(recording, note, attach=True)

    @staticmethod
    def merge(parts: list[RecordingPart]) -> tuple[list[dict], int]:
        """Every part's segments on one clock, the mic as You and the call as Others, in time order;
        one speaker's consecutive segments read as one line. Times are seconds from the first part."""
        if not parts:
            return [], 0
        origin = min(part.started_at for part in parts)
        lines = []
        for part in parts:
            offset = (part.started_at - origin).total_seconds()
            speaker = RecordingPart.Track(part.track).label
            lines += [{"at": offset + s["start"], "end": offset + s["end"], "speaker": speaker, "text": s["text"]} for s in part.segments]
        lines.sort(key=lambda line: line["at"])
        merged = []
        for line in lines:
            last = merged[-1] if merged else None
            if last and last["speaker"] == line["speaker"] and line["at"] - last["end"] < SAME_SPEAKER_GAP:
                last["text"] += " " + line["text"]
                last["end"] = max(last["end"], line["end"])
            else:
                merged.append(dict(line))
        duration = round(max(line["end"] for line in merged) - merged[0]["at"]) if merged else 0
        return [{"at": round(line["at"]), "speaker": line["speaker"], "text": line["text"]} for line in merged], duration

    @staticmethod
    def discard_audio(recording: Recording) -> None:
        """Only the words are kept: the audio goes once transcribed."""
        folder = RecordingPart(recording=recording, track="mic", index=0).path.parent
        shutil.rmtree(folder, ignore_errors=True)

    @staticmethod
    def _note(recording: Recording, text: str, attach: bool = False) -> None:
        if recording.todo_id:
            CommentService.note(recording.todo, text, recording=recording if attach else None)
