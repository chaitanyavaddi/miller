from django.conf import settings
from django.db import models


class Todo(models.Model):
    """Open todos are ordered by `position`, 1..n with no gaps. Closing and deleting set a timestamp
    instead of removing the row, so Undo can put a todo back exactly where it was."""

    class Removal(models.TextChoices):
        CLOSED  = "closed",  "Closed"
        DELETED = "deleted", "Deleted"

    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="todos")
    title      = models.CharField(max_length=300)
    position   = models.PositiveIntegerField(default=0)
    closed_at  = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "id"]
        indexes = [models.Index(fields=["user", "closed_at", "position"])]

    def __str__(self):
        return self.title


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
