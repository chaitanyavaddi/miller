from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from recordings.models import Recording, RecordingPart
from recordings.services import GroqService, RecordingService
from todos.models import Todo


class Command(BaseCommand):
    help = ("Transcribe audio files into a todo's thread, as if recorded there: --mic as You, --call as Others. "
            "For trying transcription before (or without) the browser recorder.")

    def add_arguments(self, parser):
        parser.add_argument("todo_id", type=int)
        parser.add_argument("--mic", type=Path, help="Your side (any format Groq takes: webm, m4a, mp3, wav…)")
        parser.add_argument("--call", type=Path, help="The other side")

    def handle(self, *args, todo_id, mic=None, call=None, **options):
        if not GroqService.is_configured():
            raise CommandError("GROQ_API_KEY isn't set.")
        if not (mic or call):
            raise CommandError("Give --mic, --call or both.")
        todo = Todo.objects.filter(pk=todo_id).select_related("user").first()
        if todo is None:
            raise CommandError(f"No todo {todo_id}.")
        recording = Recording.objects.create(user=todo.user, todo=todo, status=Recording.Status.PROCESSING, ended_at=timezone.now())
        for track, source in ((RecordingPart.Track.MIC, mic), (RecordingPart.Track.CALL, call)):
            if source is None:
                continue
            if not source.is_file():
                raise CommandError(f"No file at {source}.")
            part = RecordingPart.objects.create(recording=recording, track=track, index=0, started_at=timezone.now(), size=source.stat().st_size)
            part.path.parent.mkdir(parents=True, exist_ok=True)
            part.path.write_bytes(source.read_bytes())
        if RecordingService.process(recording):
            recording.refresh_from_db()
            for line in recording.transcript:
                self.stdout.write(f"{line['at'] // 60:02d}:{line['at'] % 60:02d}  {line['speaker']}: {line['text']}")
        else:
            recording.refresh_from_db()
            self.stderr.write(f"Not transcribed yet: {recording.error or 'Groq asked to wait; process_recordings will retry.'}")
