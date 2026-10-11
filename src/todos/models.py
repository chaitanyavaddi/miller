from django.conf import settings
from django.db import models


class Todo(models.Model):
    """Everything on today's list is a todo. The user orders their tasks by `position` (1..n, no
    gaps); today's meetings from a connected calendar sit after them by start time, read-only. A task
    a scheduled item produced remembers where it came from and the day. Closing and
    deleting set a timestamp instead of removing the row, so Undo can put a todo back where it was."""

    class Kind(models.TextChoices):
        TASK    = "task",    "Task"
        MEETING = "meeting", "Meeting"

    class Removal(models.TextChoices):
        CLOSED  = "closed",  "Closed"
        DELETED = "deleted", "Deleted"

    user        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="todos")
    kind        = models.CharField(max_length=20, choices=Kind.choices, default=Kind.TASK)
    title       = models.CharField(max_length=300)
    position    = models.PositiveIntegerField(default=0)
    scheduled_item = models.ForeignKey("scheduled.ScheduledItem", on_delete=models.SET_NULL, null=True, blank=True, related_name="produced_todos")
    produced_on = models.DateField(null=True, blank=True)       # the day a scheduled item produced it
    starts_at   = models.DateTimeField(null=True, blank=True)   # meetings only
    ends_at     = models.DateTimeField(null=True, blank=True)   # meetings only
    meeting_url = models.URLField(max_length=1000, blank=True)  # meetings only
    closed_at   = models.DateTimeField(null=True, blank=True)
    deleted_at  = models.DateTimeField(null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "id"]
        indexes = [models.Index(fields=["user", "closed_at", "position"])]

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
