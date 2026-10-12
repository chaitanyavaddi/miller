"""Recording jobs, run by the django-q2 cluster (qcluster)."""
from recordings.models import Recording
from recordings.services import GroqService, RecordingService


def process_recording(recording_id: int) -> bool:
    """Transcribe one stopped recording: queued the moment the user stops it."""
    recording = Recording.objects.filter(pk=recording_id).select_related("todo").first()
    if recording is None or not GroqService.is_configured():
        return False
    return RecordingService.process_claimed(recording)


def process_stuck() -> int:
    """The safety net, every 15 minutes: recordings whose tab closed without Stop, or whose job was cut off."""
    if not GroqService.is_configured():
        return 0
    return RecordingService.process_due()
