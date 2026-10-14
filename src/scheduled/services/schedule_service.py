import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from django.utils import timezone

from comments.service import CommentService
from scheduled import rhythm
from scheduled.models import ScheduledItem
from todos.models import Todo
from todos.services import TodoService

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
    at_time: str = ""     # 15:30, for the time box
    date_label: str = ""  # 17th October, 2026
    day_label: str = ""   # 1st
    moved: bool = False   # an existing todo moved to a date: its title and rhythm are its own, only the date changes


class ScheduleService:
    @staticmethod
    def items_for(user) -> QuerySet:
        return ScheduledItem.objects.filter(user=user)

    @staticmethod
    def short_date(day: date) -> str:
        return f"{day:%a}, {day:%b} {day.day}"

    @staticmethod
    def moment(day: date, at_time: time | None):
        """When a reminder falls: that day at that time, in the zone in force (the user's). None without a time."""
        return timezone.make_aware(datetime.combine(day, at_time)) if at_time else None

    @staticmethod
    def when_words(day: date, at_time: time | None) -> str:
        """Fri, Oct 16 · 3pm: the day, and the time if it has one."""
        return ScheduleService.short_date(day) + (f" · {rhythm.clock_label(at_time)}" if at_time else "")

    @staticmethod
    @transaction.atomic
    def move(todo, day: date, at_time: time | None = None) -> ScheduledItem:
        """Take a todo off today's list until `day` (and `at_time`, if one is given), when the same todo comes back, with a
        reminder at that time. Its thread says so."""
        item, _ = ScheduledItem.objects.update_or_create(
            todo=todo, defaults={"user": todo.user, "title": todo.title, "rhythm": Rhythm.ONCE, "on_date": day, "at_time": at_time, "last_produced_on": None},
        )
        if todo.notify_at:   # a reminder for the day it's leaving: it gets its new one when it comes back
            Todo.objects.filter(pk=todo.pk).update(notify_at=None)
        CommentService.note(todo, f"Moved to {ScheduleService.when_words(day, at_time)}")
        TodoService._renumber(todo.user)
        return item

    @staticmethod
    @transaction.atomic
    def remind(todo, day: date, at_time: time) -> None:
        """A reminder for today, the todo staying where it is."""
        Todo.objects.filter(pk=todo.pk).update(notify_at=ScheduleService.moment(day, at_time))

    @staticmethod
    @transaction.atomic
    def unmove(todo) -> bool:
        """Put a moved todo back where it was (taking back a move): its item goes, so it's on today's list again."""
        removed, _ = ScheduledItem.objects.filter(todo=todo).delete()
        if removed:
            note = todo.comments.filter(automatic=True, body__startswith="Moved to").order_by("-created_at", "-id").first()
            if note:
                note.delete()
            TodoService._renumber(todo.user)
        return bool(removed)

    @staticmethod
    def bring_back(item: ScheduledItem, note: str, remind: bool = False) -> None:
        """A moved todo, back on today's list now (its date came, or the user brought it back). On its day (`remind`),
        a time it was given becomes the todo's reminder."""
        todo, day, at_time = item.todo, item.on_date, item.at_time
        item.delete()
        if todo.closed_at is None and todo.deleted_at is None:
            TodoService.bring_back(todo)
            Todo.objects.filter(pk=todo.pk).update(notify_at=ScheduleService.moment(day, at_time) if remind else None)
            CommentService.note(todo, note)

    @staticmethod
    @transaction.atomic
    def save(user, changes) -> None:
        """Apply everything the modal changed, at once: new items, edited ones, removed ones. A moved
        todo only takes a new date; removing it brings it back to today."""
        for change in changes:
            if change.removed:
                item = get_object_or_404(ScheduleService.items_for(user), pk=change.id)
                if item.todo_id:
                    ScheduleService.bring_back(item, "Back on today's list")
                else:
                    item.delete()
                continue
            item = get_object_or_404(ScheduleService.items_for(user), pk=change.id) if change.id else ScheduledItem(user=user)
            if item.todo_id:
                if (change.on_date and change.on_date != item.on_date) or change.at_time != item.at_time:
                    item.on_date = change.on_date or item.on_date
                    item.at_time = change.at_time
                    item.save(update_fields=["on_date", "at_time", "updated_at"])
                    CommentService.note(item.todo, f"Moved to {ScheduleService.when_words(item.on_date, item.at_time)}")
                continue
            item.title = change.title
            item.rhythm = change.rhythm
            item.on_date = change.on_date if change.rhythm == Rhythm.ONCE else item.on_date
            item.weekdays = ",".join(str(d) for d in change.weekdays) if change.rhythm == Rhythm.WEEKLY else item.weekdays
            item.month_day = change.month_day if change.rhythm == Rhythm.MONTHLY else item.month_day
            item.at_time = change.at_time
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
            at_time=item.at_time.strftime("%H:%M") if item.at_time else "",
            on_date=item.on_date.isoformat() if item.on_date else "", weekdays=item.weekdays, month_day=str(item.month_day or ""),
            date_label=ScheduleService.date_label(item.on_date) if item.on_date else "",
            day_label=rhythm.ordinal(item.month_day) if item.month_day else "",
            moved=item.todo_id is not None,
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
    def move_months(today: date) -> list[CalendarMonth]:
        """The Scheduled calendar for Remind or move…: today (a reminder) and the days after it."""
        return [
            CalendarMonth(m.title, m.lead, [Choice(d.value, d.label, enabled=d.enabled, today=d.today) for d in m.days], m.shown)
            for m in ScheduleService._months(ScheduledItem(rhythm=Rhythm.ONCE), today)
        ]

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
