from django.core.management.base import BaseCommand

from recordings.services import GroqService, RecordingService


class Command(BaseCommand):
    help = ("Transcribe recordings that were stopped (or went quiet), merge them into transcripts and drop the "
            "audio. Run it every minute (* * * * *); safe to overlap and to rerun.")

    def handle(self, *args, **options):
        if not GroqService.is_configured():
            self.stderr.write("GROQ_API_KEY isn't set: nothing transcribed.")
            return
        self.stdout.write(f"Transcribed {RecordingService.process_due()} recording(s).")
