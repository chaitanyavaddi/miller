"""How a todo's time reads. On the list it's as short as people think it: 6pm today, tmrw 7am,
th 7am within the week, 23 oct further out (the time is in the thread). The thread says it
exactly: 23 Oct 7am. Everything is in the active time zone (the user's, via the middleware)."""
from datetime import datetime

from django.utils import timezone

WEEK_AHEAD = 6  # days ahead that still read as a weekday


def clock(moment: datetime) -> str:
    """7am, 12pm, 10:30am: minutes only when there are some."""
    local = timezone.localtime(moment)
    minutes = f":{local.minute:02d}" if local.minute else ""
    return f"{local.hour % 12 or 12}{minutes}{'am' if local.hour < 12 else 'pm'}"


def clock_full(moment: datetime) -> str:
    """10:00 am, 08:30 pm: always two-digit hours and minutes, for a meeting's row, where the times line up in a column."""
    local = timezone.localtime(moment)
    return f"{local.hour % 12 or 12:02d}:{local.minute:02d} {'am' if local.hour < 12 else 'pm'}"


def when(moment: datetime) -> str:
    """For a row: 6pm, tmrw 7am, th 7am, 23 oct."""
    local = timezone.localtime(moment)
    ahead = (local.date() - timezone.localdate()).days
    if ahead == 0:
        return clock(moment)
    if ahead == 1:
        return f"tmrw {clock(moment)}"
    if 1 < ahead <= WEEK_AHEAD:
        return f"{local:%a}"[:2].lower() + f" {clock(moment)}"
    return f"{local.day} {local:%b}".lower()


def exact(moment: datetime) -> str:
    """For the thread: 23 Oct 7am."""
    local = timezone.localtime(moment)
    return f"{local.day} {local:%b} {clock(moment)}"
