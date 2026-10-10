"""Reminder times as the list speaks them: a few presets, and how a time reads on a row or a thread.
Everything is in the active time zone (the user's, via the middleware)."""
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.utils import timezone

EVENING = time(18, 0)
MORNING = time(9, 0)


def clock(moment: datetime) -> str:
    """9:30a, 12:00p."""
    local = timezone.localtime(moment)
    return f"{local.hour % 12 or 12}:{local.minute:02d}{'a' if local.hour < 12 else 'p'}"


def when(moment: datetime) -> str:
    """Today · 6:00p, Tomorrow · 9:00a, Mon, Oct 12 · 9:00a."""
    day = timezone.localtime(moment).date()
    today = timezone.localdate()
    if day == today:
        name = "Today"
    elif day == today + timedelta(days=1):
        name = "Tomorrow"
    else:
        name = f"{day:%a}, {day:%b} {day.day}"
    return f"{name} · {clock(moment)}"


@dataclass(frozen=True)
class ReminderPreset:
    key: str
    label: str
    at: datetime

    @property
    def hint(self) -> str:
        """The time for today's presets; the weekday for one that says its time already."""
        local = timezone.localtime(self.at)
        return clock(self.at) if local.date() == timezone.localdate() else f"{local:%a}"


def presets(now: datetime | None = None) -> list[ReminderPreset]:
    """In 1 hour; this evening, while it's still more than an hour off; tomorrow morning."""
    now = timezone.localtime(now)
    today = now.date()
    options = [ReminderPreset("hour", "In 1 hour", (now + timedelta(hours=1)).replace(second=0, microsecond=0))]
    evening = timezone.make_aware(datetime.combine(today, EVENING))
    if evening - now > timedelta(hours=1):
        options.append(ReminderPreset("evening", "This evening", evening))
    options.append(ReminderPreset("tomorrow", "Tomorrow 9:00", timezone.make_aware(datetime.combine(today + timedelta(days=1), MORNING))))
    return options


def resolve(key: str) -> datetime | None:
    return next((preset.at for preset in presets() if preset.key == key), None)
