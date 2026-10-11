import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from django.db import transaction
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from django.utils import timezone

from scheduled import rhythm
from scheduled.models import ScheduledItem

Rhythm = ScheduledItem.Rhythm
RHYTHM_WORDS = {Rhythm.ONCE: "once", Rhythm.WEEKLY: "weekly", Rhythm.MONTHLY: "monthly"}


@dataclass(frozen=True)
class Choice:
    value: str
    label: str
    on: bool = False
    enabled: bool = True
    today: bool = False
    name: str = ""   # the full name, where the label is a single letter


@dataclass(frozen=True)
class CalendarMonth:
    title: str
    lead: list          # blanks before the 1st, so it falls under its weekday
    days: list[Choice]
    shown: bool = False


@dataclass(frozen=True)
class ScheduledRow:
    """One line of the Scheduled modal: its title, its rhythm and that rhythm's detail, and how they read."""
    id: int
    title: str
    rhythm: str
    on_date: str = ""     # ISO
    weekdays: str = ""    # "0,3"
    month_day: str = ""
    date_label: str = ""  # 17th October, 2026
    day_label: str = ""   # 1st


class ScheduleService:
    @staticmethod
    def items_for(user) -> QuerySet:
        return ScheduledItem.objects.filter(user=user)

    @staticmethod
    @transaction.atomic
    def save(user, changes) -> None:
        """Apply everything the modal changed, at once: new items, edited ones, removed ones."""
        for change in changes:
            if change.removed:
                get_object_or_404(ScheduleService.items_for(user), pk=change.id).delete()
                continue
            item = get_object_or_404(ScheduleService.items_for(user), pk=change.id) if change.id else ScheduledItem(user=user)
            item.title = change.title
            item.rhythm = change.rhythm
            item.on_date = change.on_date if change.rhythm == Rhythm.ONCE else item.on_date
            item.weekdays = ",".join(str(d) for d in change.weekdays) if change.rhythm == Rhythm.WEEKLY else item.weekdays
            item.month_day = change.month_day if change.rhythm == Rhythm.MONTHLY else item.month_day
            item.save()

    @staticmethod
    def rows(user) -> list[ScheduledRow]:
        """Soonest first: by the next day each will appear (tomorrow on, once it has produced today)."""
        today = timezone.localdate()

        def next_appearance(item):
            start = today + timedelta(days=1) if item.last_produced_on == today else today
            return rhythm.next_due(item, start) or date.max

        items = sorted(ScheduleService.items_for(user), key=lambda item: (next_appearance(item), item.id))
        return [ScheduleService._row(item, today) for item in items]

    @staticmethod
    def _row(item: ScheduledItem, today: date) -> ScheduledRow:
        return ScheduledRow(
            id=item.id, title=item.title, rhythm=item.rhythm,
            on_date=item.on_date.isoformat() if item.on_date else "", weekdays=item.weekdays, month_day=str(item.month_day or ""),
            date_label=ScheduleService.date_label(item.on_date) if item.on_date else "",
            day_label=rhythm.ordinal(item.month_day) if item.month_day else "",
        )

    @staticmethod
    def date_label(day: date) -> str:
        return f"{rhythm.ordinal(day.day)} {calendar.month_name[day.month]}, {day.year}"

    @staticmethod
    def _months(item: ScheduledItem, today: date) -> list[CalendarMonth]:
        """This month and the next eleven, one shown at a time: the chosen date's month, else this one.
        Days before today can't be picked."""
        months = []
        year, month = today.year, today.month
        for _ in range(rhythm.ONCE_AHEAD_MONTHS):
            first = date(year, month, 1)
            count = calendar.monthrange(year, month)[1]
            days = [date(year, month, n) for n in range(1, count + 1)]
            months.append(CalendarMonth(
                title=f"{calendar.month_name[month]} {year}", lead=[None] * first.weekday(),
                days=[Choice(d.isoformat(), str(d.day), on=d == item.on_date, enabled=d >= today, today=d == today) for d in days],
                shown=(item.on_date is not None and (item.on_date.year, item.on_date.month) == (year, month)),
            ))
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        if not any(m.shown for m in months):
            months[0] = CalendarMonth(months[0].title, months[0].lead, months[0].days, shown=True)
        return months

    @staticmethod
    def modal(user) -> dict:
        """The Scheduled modal: every item, soonest first, and what its shared pickers need."""
        today = timezone.localdate()
        return {
            "rows":       ScheduleService.rows(user),
            "rhythms":    [Choice(value, RHYTHM_WORDS[value]) for value in Rhythm.values],
            "months":     ScheduleService._months(ScheduledItem(rhythm=Rhythm.ONCE), today),
            "weekdays":   [Choice(str(d), letter, name=rhythm.WEEKDAY_NAMES[d]) for d, letter in enumerate(rhythm.WEEKDAY_LETTERS)],
            "month_days": [Choice(str(d), str(d)) for d in range(1, 32)],
        }
