from django.conf import settings
from django.db import models


class Recording(models.Model):
    """A meeting's call, recorded in the browser while it's on (the mic, plus the call's audio from a
    shared tab), then transcribed; the transcript folds under a note in the meeting's thread. (A voice note
    said into a todo's composer isn't one of these: it's transcribed at once and nothing is kept.)
    It comes in parts: a track per source and a new part every hour or after a resume, each uploaded in
    numbered chunks. Once stopped (or quiet too long), the parts are transcribed and merged by clock time;
    the audio then goes."""

    class Kind(models.TextChoices):
        VOICE = "voice", "Voice update"  # not recorded this way any more (said into the composer instead)
        CALL  = "call",  "Call"


    class Status(models.TextChoices):
        RECORDING  = "recording",  "Recording"
        PROCESSING = "processing", "Transcribing"
        READY      = "ready",      "Ready"
        FAILED     = "failed",     "Failed"

    user          = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recordings")
    todo          = models.ForeignKey("todos.Todo", on_delete=models.SET_NULL, null=True, blank=True, related_name="recordings")
    kind          = models.CharField(max_length=10, choices=Kind.choices, default=Kind.CALL)
    status        = models.CharField(max_length=20, choices=Status.choices, default=Status.RECORDING)
    interrupted   = models.BooleanField(default=False)   # stopped because its chunks stopped coming (a closed tab)
    last_chunk_at = models.DateTimeField(null=True, blank=True)
    ended_at      = models.DateTimeField(null=True, blank=True)
    duration      = models.PositiveIntegerField(default=0)  # seconds, from the first word to the last
    transcript    = models.JSONField(default=list, blank=True)  # [{"at": seconds, "speaker": "You" | "Others", "text": ...}]
    claimed_at    = models.DateTimeField(null=True, blank=True)  # a run is transcribing it: others leave it alone
    attempts      = models.PositiveSmallIntegerField(default=0)
    error         = models.TextField(blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"Recording {self.pk} ({self.get_status_display()})"


class RecordingPart(models.Model):
    """One track's stretch of audio, appended chunk by chunk. `next_seq` is the chunk it expects next, so
    a retried chunk is ignored and a gap is refused, never written."""

    class Track(models.TextChoices):
        MIC  = "mic",  "You"
        CALL = "call", "Others"

    recording  = models.ForeignKey(Recording, on_delete=models.CASCADE, related_name="parts")
    track      = models.CharField(max_length=10, choices=Track.choices)
    index      = models.PositiveIntegerField()
    started_at = models.DateTimeField()               # when the browser began this part: lines it up with the others
    next_seq   = models.PositiveIntegerField(default=0)
    size       = models.PositiveBigIntegerField(default=0)
    transcribed = models.BooleanField(default=False)
    segments   = models.JSONField(default=list, blank=True)  # [{"start": s, "end": s, "text": ...}], seconds into the part
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["recording", "track", "index"]
        unique_together = ("recording", "track", "index")

    def __str__(self):
        return f"{self.recording_id}/{self.track}-{self.index}"

    @property
    def path(self):
        """Where its audio is kept until transcribed: under MEDIA_ROOT, never served."""
        return settings.MEDIA_ROOT / "recordings" / f"u{self.recording.user_id}" / str(self.recording_id) / f"{self.track}-{self.index}.webm"
