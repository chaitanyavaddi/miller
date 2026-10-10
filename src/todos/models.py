from django.conf import settings
from django.db import models
from django.utils import timezone


def today():
    return timezone.localdate()


class Todo(models.Model):
    """Everything on the list is a todo, filed under a `day` and ordered within it by `position`
    (1..n per day, no gaps). A task the user wrote can carry a reminder; a meeting comes from a
    connected calendar and is read-only. Closing and deleting set a timestamp instead of removing
    the row, so Undo can put a todo back exactly where it was."""

    class Kind(models.TextChoices):
        TASK    = "task",    "Task"
        MEETING = "meeting", "Meeting"

    class Removal(models.TextChoices):
        CLOSED  = "closed",  "Closed"
        DELETED = "deleted", "Deleted"

    user        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="todos")
    kind        = models.CharField(max_length=20, choices=Kind.choices, default=Kind.TASK)
    title       = models.CharField(max_length=300)
    day         = models.DateField(default=today)
    position    = models.PositiveIntegerField(default=0)
    remind_at   = models.DateTimeField(null=True, blank=True)
    starts_at   = models.DateTimeField(null=True, blank=True)   # meetings only
    ends_at     = models.DateTimeField(null=True, blank=True)   # meetings only
    meeting_url = models.URLField(max_length=1000, blank=True)  # meetings only
    closed_at   = models.DateTimeField(null=True, blank=True)
    deleted_at  = models.DateTimeField(null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["day", "position", "id"]
        indexes = [models.Index(fields=["user", "closed_at", "day", "position"])]

    def __str__(self):
        return self.title

    @property
    def is_meeting(self) -> bool:
        return self.kind == Todo.Kind.MEETING


class PositionDay(models.Model):
    """The last position a todo held on a day, written whenever the order is committed."""
    todo       = models.ForeignKey(Todo, on_delete=models.CASCADE, related_name="days")
    day        = models.DateField()
    position   = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["day"]
        constraints = [models.UniqueConstraint(fields=["todo", "day"], name="one_position_per_todo_day")]

    def __str__(self):
        return f"{self.todo} #{self.position} on {self.day}"
