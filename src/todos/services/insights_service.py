from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.db.models import QuerySet
from django.utils import timezone

from todos.models import Todo
from todos.schemas import ReviewPeriod



@dataclass(frozen=True)
class Window:
    start: datetime | None
    end: datetime
    previous_start: datetime | None = None
    previous_end: datetime | None = None


@dataclass(frozen=True)
class LongestWait:
    title: str
    days: int


@dataclass(frozen=True)
class Insights:
    """What the review modal says about a period: how much got closed, against what, and the
    small facts that show progress."""
    period: str
    closed: int
    previous: int | None
    streak: int
    longest_wait: LongestWait | None
    best_week: int | None

    @property
    def delta(self) -> int | None:
        return None if self.previous is None else self.closed - self.previous

    @property
    def delta_size(self) -> int:
        return abs(self.delta or 0)

    @property
    def previous_name(self) -> str:
        return "last week" if self.period == ReviewPeriod.WEEK else "last month"


class InsightsService:
    @staticmethod
    def window(period: str) -> Window:
        """The calendar week or month so far, and the same stretch of the one before it, so a
        Wednesday is compared with last Wednesday rather than with a whole finished week."""
        now = timezone.localtime()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if period == ReviewPeriod.ALL:
            return Window(start=None, end=now)
        if period == ReviewPeriod.WEEK:
            start = midnight - timedelta(days=now.weekday())
            previous_start = start - timedelta(days=7)
        else:
            start = midnight.replace(day=1)
            previous_start = (start - timedelta(days=1)).replace(day=1)
        return Window(start=start, end=now, previous_start=previous_start, previous_end=min(previous_start + (now - start), start))

    @staticmethod
    def for_period(user, period: str) -> Insights:
        window = InsightsService.window(period)
        closed = InsightsService._closed(user)
        in_window = closed if window.start is None else closed.filter(closed_at__gte=window.start)
        close_days = [timezone.localtime(at).date() for at in closed.values_list("closed_at", flat=True)]
        previous = None
        if window.previous_start is not None:
            previous = closed.filter(closed_at__gte=window.previous_start, closed_at__lt=window.previous_end).count()
        return Insights(
            period=period,
            closed=in_window.count(),
            previous=previous,
            streak=InsightsService._streak(set(close_days)),
            longest_wait=InsightsService._longest_wait(in_window),
            best_week=InsightsService._best_week(close_days) if period == ReviewPeriod.ALL else None,
        )

    @staticmethod
    def _closed(user) -> QuerySet:
        return Todo.objects.filter(user=user, closed_at__isnull=False, deleted_at__isnull=True)

    @staticmethod
    def _streak(close_days: set[date]) -> int:
        """Consecutive days with at least one close, ending today (or yesterday, if today has none yet)."""
        day = timezone.localdate()
        if day not in close_days:
            day -= timedelta(days=1)
        streak = 0
        while day in close_days:
            streak += 1
            day -= timedelta(days=1)
        return streak

    @staticmethod
    def _longest_wait(closed: QuerySet) -> LongestWait | None:
        longest = max(closed, key=lambda todo: todo.closed_at - todo.created_at, default=None)
        if longest is None:
            return None
        return LongestWait(title=longest.title, days=InsightsService.days_between(longest.created_at, longest.closed_at))

    @staticmethod
    def days_between(start: datetime, end: datetime) -> int:
        """Calendar days, as the list counts them: added yesterday is one day, whatever the hour."""
        return (timezone.localtime(end).date() - timezone.localtime(start).date()).days

    @staticmethod
    def _best_week(close_days: list[date]) -> int | None:
        weeks = Counter(day - timedelta(days=day.weekday()) for day in close_days)
        return max(weeks.values(), default=None)
