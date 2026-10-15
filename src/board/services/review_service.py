from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db.models import Count, Q
from django.utils import timezone

from comments.service import CommentService
from todos.services import InsightsService, TodoService

RAIL_MIN_DAYS = 30    # the rail always reaches back at least this far, so it reads as a calendar
RAIL_MAX_DAYS = 365   # and no further than this
WEEKDAY_LETTERS = "MTWTFSS"


@dataclass(frozen=True)
class ReviewRow:
    id: int
    title: str
    stamp: str
    days_on_list: int
    is_meeting: bool = False  # from the calendar: no reopen, no delete
    day: str = ""             # the day it was closed, 2026-10-14: where its thread's back link returns to
    notes: int = 0            # updates a person wrote on it


@dataclass(frozen=True)
class RailDay:
    value: str        # 2026-10-14
    number: int       # 14
    weekday: str      # W
    count: int        # todos closed that day
    month: str = ""   # "Oct", on the first day of a month and the rail's first day: where a month starts
    is_on: bool = False
    is_today: bool = False


class ReviewService:
    """Read model for All todos → Past: a rail of days, and what was closed on the chosen one."""

    @staticmethod
    def build(user, day: date | None = None) -> dict:
        TodoService.close_ended_meetings(user)
        closed = TodoService.reviewed(user)   # tasks and ended meetings alike
        today = timezone.localdate()
        counts = Counter(timezone.localtime(at).date() for at in closed.values_list("closed_at", flat=True))
        busy = sorted(d for d in counts if d <= today)
        start = max(min([today - timedelta(days=RAIL_MIN_DAYS - 1), *busy]), today - timedelta(days=RAIL_MAX_DAYS - 1))
        if day is None or not (start <= day <= today):
            day = today if counts.get(today) or not busy else busy[-1]   # today, else the last day something was done
        rail = [
            RailDay(value=d.isoformat(), number=d.day, weekday=WEEKDAY_LETTERS[d.weekday()], count=counts.get(d, 0),
                    month=f"{d:%b}" if d.day == 1 or d == start else "", is_on=d == day, is_today=d == today)
            for d in (start + timedelta(days=n) for n in range((today - start).days + 1))
        ]
        rows = [ReviewService.row(todo) for todo in ReviewService._closed_on(closed, day)]
        return {"rail": rail, "rows": rows, "day": day.isoformat(), "day_label": ReviewService._day_label(day, today)}

    @staticmethod
    def _closed_on(closed, day: date):
        """The todos closed on `day` (in the user's zone), in the order they were done, with how many notes each had."""
        begin = timezone.make_aware(datetime.combine(day, time.min))
        return (closed.filter(closed_at__gte=begin, closed_at__lt=begin + timedelta(days=1))
                .annotate(notes=Count("comments", filter=Q(comments__automatic=False)))
                .order_by("closed_at"))

    @staticmethod
    def row(todo) -> ReviewRow:
        closed_day = timezone.localtime(todo.closed_at).date()
        return ReviewRow(
            id=todo.id,
            title=todo.title,
            stamp=ReviewService._day(closed_day),
            days_on_list=max(0, InsightsService.days_between(todo.created_at, todo.closed_at)),
            is_meeting=todo.is_meeting,
            day=closed_day.isoformat(),
            notes=getattr(todo, "notes", 0),
        )

    @staticmethod
    def thread(todo) -> dict:
        return {"row": ReviewService.row(todo), "comments": CommentService.for_todo(todo.id)}

    @staticmethod
    def parse_day(raw: str | None) -> date | None:
        try:
            return date.fromisoformat(raw or "")
        except ValueError:
            return None

    @staticmethod
    def _day(day) -> str:
        return f"{day:%b} {day.day}"

    @staticmethod
    def _day_label(day: date, today: date) -> str:
        """Today · Yesterday · Mon, Oct 12."""
        if day == today:
            return "Today"
        if day == today - timedelta(days=1):
            return "Yesterday"
        return f"{day:%a}, {day:%b} {day.day}"
