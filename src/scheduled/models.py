from django.conf import settings
from django.db import models


class ScheduledItem(models.Model):
    """Something that comes back, or can't start until a date. It never sits in today's list itself:
    on each day it falls on, it produces a normal todo at the top of the list. It's about days, not
    hours. `last_produced_on` makes sure that happens at most once a day.

    A once item can also hold an existing `todo` that was moved to a date: the todo is off today's list
    while the item waits, and on the day the same todo (and its thread) comes back instead of a new one."""

    class Rhythm(models.TextChoices):
        ONCE    = "once",    "Once"
        WEEKLY  = "weekly",  "Weekly"
        MONTHLY = "monthly", "Monthly"

    user             = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="scheduled_items")
    title            = models.CharField(max_length=300, blank=True)  # blank until named; an untitled item produces nothing
    rhythm           = models.CharField(max_length=20, choices=Rhythm.choices, default=Rhythm.ONCE)
    on_date          = models.DateField(null=True, blank=True)                    # once
    weekdays         = models.CharField(max_length=20, blank=True)                # weekly: "0,3" (Monday is 0)
    month_day        = models.PositiveSmallIntegerField(null=True, blank=True)    # monthly: 1–31
    last_produced_on = models.DateField(null=True, blank=True)
    todo             = models.ForeignKey("todos.Todo", on_delete=models.CASCADE, null=True, blank=True, related_name="moves")
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return self.title or "Untitled"

    @property
    def weekday_list(self) -> list[int]:
        return sorted(int(d) for d in self.weekdays.split(",") if d.strip().isdigit())
