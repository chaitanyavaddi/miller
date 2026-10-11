from dataclasses import dataclass
from datetime import date

from django.contrib import messages
from django.utils import timezone

from scheduled.models import ScheduledItem
from scheduled.rhythm import once_limit
from todos.schemas import TITLE_MAX_LENGTH

Rhythm = ScheduledItem.Rhythm


@dataclass
class ScheduledChange:
    """One row as the modal leaves it: an existing item (with its id) or a new one, or one to remove."""
    id: int | None
    removed: bool
    title: str
    rhythm: str
    on_date: date | None
    weekdays: list[int]
    month_day: int | None


@dataclass
class ScheduledSaveSchema:
    """Everything changed in the Scheduled modal, sent together by Save: one entry per changed row,
    as parallel lists (row_id, row_title, ...). It saves whole or not at all."""
    changes: list[ScheduledChange]

    @classmethod
    def from_post(cls, request) -> "ScheduledSaveSchema":
        POST = request.POST
        columns = [POST.getlist(name) for name in ("row_id", "row_removed", "row_title", "row_rhythm", "row_on_date", "row_weekdays", "row_month_day")]
        changes = [
            ScheduledChange(
                id=int(row_id) if row_id.isdigit() else None,
                removed=removed == "1",
                title=title.strip(),
                rhythm=rhythm.strip(),
                on_date=cls._date(on_date.strip()),
                weekdays=sorted({int(d) for d in weekdays.split(",") if d.strip().isdigit() and 0 <= int(d) <= 6}),
                month_day=int(month_day) if month_day.strip().isdigit() else None,
            )
            for row_id, removed, title, rhythm, on_date, weekdays, month_day in zip(*columns)
        ]
        obj = cls(changes=changes)
        obj._request = request
        return obj

    @staticmethod
    def _date(raw: str) -> date | None:
        try:
            return date.fromisoformat(raw) if raw else None
        except ValueError:
            return None

    def is_valid_titles(self) -> bool:
        kept = [c for c in self.changes if not c.removed]
        if any(not c.title for c in kept):
            messages.error(self._request, "Every scheduled todo needs a title")
            return False
        if any(len(c.title) > TITLE_MAX_LENGTH for c in kept):
            messages.error(self._request, f"Keep titles under {TITLE_MAX_LENGTH} characters")
            return False
        return True

    def is_valid_rhythms(self) -> bool:
        ok = True
        today = timezone.localdate()
        for c in self.changes:
            if c.removed:
                continue
            if c.rhythm not in Rhythm.values:
                messages.error(self._request, f"“{c.title or 'Untitled'}”: pick once, weekly or monthly")
                ok = False
            elif c.rhythm == Rhythm.ONCE and not (c.on_date and today <= c.on_date <= once_limit(today)):
                messages.error(self._request, f"“{c.title or 'Untitled'}”: pick a date from today, within a year")
                ok = False
            elif c.rhythm == Rhythm.WEEKLY and not c.weekdays:
                messages.error(self._request, f"“{c.title or 'Untitled'}”: pick at least one day")
                ok = False
            elif c.rhythm == Rhythm.MONTHLY and not (c.month_day and 1 <= c.month_day <= 31):
                messages.error(self._request, f"“{c.title or 'Untitled'}”: pick a day of the month")
                ok = False
        return ok

    def is_valid_removals(self) -> bool:
        if any(c.removed and c.id is None for c in self.changes):
            messages.error(self._request, "Nothing to remove there")
            return False
        return True

    def is_valid(self) -> bool:
        return all([self.is_valid_titles(), self.is_valid_rhythms(), self.is_valid_removals()])
