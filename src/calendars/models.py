from django.conf import settings
from django.db import models


class CalendarConnection(models.Model):
    """One calendar account a user connected. Recall.ai holds the provider's refresh token and keeps
    the events in sync; we keep its id and the address it belongs to."""

    class Provider(models.TextChoices):
        GOOGLE    = "google",    "Google Calendar"
        MICROSOFT = "microsoft", "Outlook Calendar"

    class Status(models.TextChoices):
        CONNECTING   = "connecting",   "Connecting"
        CONNECTED    = "connected",    "Connected"
        DISCONNECTED = "disconnected", "Disconnected"

    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calendar_connections")
    provider   = models.CharField(max_length=20, choices=Provider.choices)
    email      = models.EmailField()
    recall_id  = models.CharField(max_length=64, unique=True)
    status     = models.CharField(max_length=20, choices=Status.choices, default=Status.CONNECTING)
    synced_at  = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        unique_together = ("user", "provider", "email")

    def __str__(self):
        return f"{self.email} ({self.get_provider_display()})"


class CalendarEvent(models.Model):
    """A Recall calendar event, tied to the meeting it shows up as. The same meeting seen through
    two connected calendars (same iCal UID and start) points at one meeting, not two."""
    connection = models.ForeignKey(CalendarConnection, on_delete=models.CASCADE, related_name="events")
    meeting    = models.ForeignKey("todos.Todo", on_delete=models.CASCADE, related_name="calendar_events")
    recall_id  = models.CharField(max_length=64, unique=True)
    ical_uid   = models.CharField(max_length=1024, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.meeting} via {self.connection}"
