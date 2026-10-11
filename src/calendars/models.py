from django.conf import settings
from django.db import models


class CalendarConnection(models.Model):
    """One calendar account a user connected. We keep its refresh token (encrypted) and call the
    provider's API ourselves; the calendars inside the account are its sources."""

    class Provider(models.TextChoices):
        GOOGLE    = "google",    "Google Calendar"
        MICROSOFT = "microsoft", "Outlook Calendar"

    class Status(models.TextChoices):
        CONNECTED    = "connected",    "Connected"
        DISCONNECTED = "disconnected", "Disconnected"

    user          = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calendar_connections")
    provider      = models.CharField(max_length=20, choices=Provider.choices)
    email         = models.EmailField()
    refresh_token = models.TextField()  # encrypted with CALENDAR_TOKEN_KEY, see TokenVault
    status        = models.CharField(max_length=20, choices=Status.choices, default=Status.CONNECTED)
    synced_at     = models.DateTimeField(null=True, blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        unique_together = ("user", "provider", "email")

    def __str__(self):
        return f"{self.email} ({self.get_provider_display()})"


class CalendarSource(models.Model):
    """One calendar inside a connected account: the primary one, or a secondary/shared one the user
    switched on. Each enabled source has a push channel (a Google watch channel or a Graph
    subscription) that tells us when its events change."""
    connection         = models.ForeignKey(CalendarConnection, on_delete=models.CASCADE, related_name="sources")
    external_id        = models.CharField(max_length=1024)
    name               = models.CharField(max_length=255)
    is_primary         = models.BooleanField(default=False)
    enabled            = models.BooleanField(default=False)
    channel_id         = models.CharField(max_length=64, blank=True, db_index=True)   # ours: a uuid
    channel_resource   = models.CharField(max_length=255, blank=True, db_index=True)  # theirs: Google resourceId / Graph subscription id
    channel_expires_at = models.DateTimeField(null=True, blank=True)
    created_at         = models.DateTimeField(auto_now_add=True)
    updated_at         = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_primary", "name"]
        unique_together = ("connection", "external_id")

    def __str__(self):
        return f"{self.name} ({self.connection.email})"


class CalendarEvent(models.Model):
    """A provider event, tied to the meeting it shows up as. The same meeting seen through two
    calendars (same iCal UID and start) points at one meeting, not two."""
    source      = models.ForeignKey(CalendarSource, on_delete=models.CASCADE, related_name="events")
    meeting     = models.ForeignKey("todos.Todo", on_delete=models.CASCADE, related_name="calendar_events")
    external_id = models.CharField(max_length=1024)
    ical_uid    = models.CharField(max_length=1024, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        unique_together = ("source", "external_id")

    def __str__(self):
        return f"{self.meeting} via {self.source}"
