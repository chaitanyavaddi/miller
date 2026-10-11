"""When a scheduled item falls, and how that reads. Pure date logic: no queries, no zones (the
caller passes the user's local dates)."""
import calendar
from datetime import date, timedelta

from scheduled.models import ScheduledItem

Rhythm = ScheduledItem.Rhythm
ONCE_AHEAD_MONTHS = 12  # a once date can be in this month or the next eleven
SEARCH_DAYS = 62  # long enough to find any monthly day
WEEKDAY_SHORT = ["mo", "tu", "we", "th", "fr", "sa", "su"]
WEEKDAY_LETTERS = ["M", "T", "W", "T", "F", "S", "S"]
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTH_SHORT = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def once_limit(today: date) -> date:
    """The last day a once item can be set for: the end of the last month shown."""
    year, month = divmod(today.month - 1 + ONCE_AHEAD_MONTHS - 1, 12)
    year += today.year
    return date(year, month + 1, calendar.monthrange(year, month + 1)[1])


def _clamped(year: int, month: int, day: int) -> int:
    """The day, or the month's last day if the month is shorter."""
    return min(day, calendar.monthrange(year, month)[1])


def is_complete(item) -> bool:
    """Has everything its rhythm needs to fall on a day."""
    if item.rhythm == Rhythm.ONCE:
        return item.on_date is not None
    if item.rhythm == Rhythm.WEEKLY:
        return bool(item.weekday_list)
    if item.rhythm == Rhythm.MONTHLY:
        return item.month_day is not None
    return True


def falls_on(item, day: date) -> bool:
    if not is_complete(item):
        return False
    if item.rhythm == Rhythm.ONCE:
        return day == item.on_date
    if item.rhythm == Rhythm.WEEKLY:
        return day.weekday() in item.weekday_list
    return day.day == _clamped(day.year, day.month, item.month_day)


def latest_due(item, start: date, end: date) -> date | None:
    """The last day in start..end the item falls on, if any."""
    day = end
    while day >= start and (end - day).days <= SEARCH_DAYS:
        if falls_on(item, day):
            return day
        day -= timedelta(days=1)
    return None


def next_due(item, start: date) -> date | None:
    """The first day from `start` the item falls on, if any."""
    for offset in range(SEARCH_DAYS):
        day = start + timedelta(days=offset)
        if falls_on(item, day):
            return day
    return None


def label(item, today: date) -> str:
    """once · 23 oct, every mo, th, every day (all seven), monthly · 1st."""
    if item.rhythm == Rhythm.ONCE:
        return f"once · {item.on_date.day} {MONTH_SHORT[item.on_date.month - 1]}" if item.on_date else "once"
    if item.rhythm == Rhythm.WEEKLY and len(item.weekday_list) == 7:
        return "every day"
    if item.rhythm == Rhythm.WEEKLY:
        return "every " + ", ".join(WEEKDAY_SHORT[d] for d in item.weekday_list) if item.weekday_list else "weekly"
    return f"monthly · {ordinal(item.month_day)}" if item.month_day else "monthly"
