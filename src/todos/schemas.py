from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.contrib import messages
from django.db import models
from django.utils import timezone

from todos import reminders

TITLE_MAX_LENGTH = 300
LATER = "later"  # the day token for the bucket past the dated ones


@dataclass
class TodoTitleSchema:
    title: str

    @classmethod
    def from_post(cls, request) -> "TodoTitleSchema":
        """Used both to add a todo and to rename one."""
        obj = cls(title=request.POST.get("title", "").strip())
        obj._request = request
        return obj

    def is_valid_title(self) -> bool:
        if not self.title:
            messages.error(self._request, "Write something first")
            return False
        if len(self.title) > TITLE_MAX_LENGTH:
            messages.error(self._request, f"Keep it under {TITLE_MAX_LENGTH} characters")
            return False
        return True

    def is_valid(self) -> bool:
        return all([self.is_valid_title()])


@dataclass
class TodoReorderSchema:
    """The list as it now stands: todo ids in order, each with the day it sits under
    (an ISO date, or `later`). Without days, every todo keeps its own."""
    ids: list[int]
    days: list[str] = field(default_factory=list)

    @classmethod
    def from_post(cls, request) -> "TodoReorderSchema":
        raw_ids  = request.POST.get("ids", "").split(",")
        raw_days = request.POST.get("days", "").split(",")
        pairs = [(int(i), (raw_days[n] if n < len(raw_days) else "").strip()) for n, i in enumerate(raw_ids) if i.strip().isdigit()]
        obj = cls(ids=[i for i, _ in pairs], days=[d for _, d in pairs])
        obj._request = request
        return obj

    def is_valid_ids(self) -> bool:
        if not self.ids:
            messages.error(self._request, "Nothing to reorder")
            return False
        return True

    def is_valid(self) -> bool:
        return all([self.is_valid_ids()])


@dataclass
class TodoReminderSchema:
    """A reminder: one of the presets (`remind_preset`), or a time picked in a datetime-local field
    (`remind_at`), read in the user's zone. Neither means no reminder (or clearing one)."""
    raw: str
    remind_at: datetime | None
    preset: str = ""

    @classmethod
    def from_post(cls, request) -> "TodoReminderSchema":
        preset = request.POST.get("remind_preset", "").strip()
        raw = request.POST.get("remind_at", "").strip()
        if preset:
            remind_at = reminders.resolve(preset)
        else:
            try:
                remind_at = timezone.make_aware(datetime.fromisoformat(raw)) if raw else None
            except ValueError:
                remind_at = None
        obj = cls(raw=raw, remind_at=remind_at, preset=preset)
        obj._request = request
        return obj

    def is_valid_remind_at(self) -> bool:
        if (self.raw or self.preset) and self.remind_at is None:
            messages.error(self._request, "That isn't a date and time")
            return False
        if self.remind_at is not None and self.remind_at < timezone.now() - timedelta(minutes=1):
            messages.error(self._request, "Pick a time that hasn't passed")
            return False
        return True

    def is_valid(self) -> bool:
        return all([self.is_valid_remind_at()])


@dataclass
class TodoReopenSchema:
    index: int
    select: bool

    @classmethod
    def from_post(cls, request) -> "TodoReopenSchema":
        raw = request.POST.get("index", "").strip()
        obj = cls(index=int(raw) if raw.isdigit() else 1, select=request.POST.get("select") == "1")
        obj._request = request
        return obj

    def is_valid(self) -> bool:
        return True


class ReviewStatus(models.TextChoices):
    DONE    = "done",    "Done"
    OPEN    = "open",    "Open"
    DELETED = "deleted", "Deleted"


class ReviewPeriod(models.TextChoices):
    WEEK  = "week",  "This week"
    MONTH = "month", "This month"
    ALL   = "all",   "All time"


@dataclass
class ReviewFilterSchema:
    status: str
    period: str

    @classmethod
    def from_request(cls, request) -> "ReviewFilterSchema":
        """Filters arrive in the query string when browsing and in the body with an action."""
        source = request.POST if request.method == "POST" else request.GET
        status = source.get("status", "").strip()
        period = source.get("period", "").strip()
        return cls(
            status=status if status in ReviewStatus.values else ReviewStatus.DONE,
            period=period if period in ReviewPeriod.values else ReviewPeriod.WEEK,
        )

    @property
    def period_label(self) -> str:
        return ReviewPeriod(self.period).label

    def is_valid(self) -> bool:
        return True
