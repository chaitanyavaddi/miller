from dataclasses import dataclass

from django.contrib import messages
from django.db import models

TITLE_MAX_LENGTH = 300


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
    ids: list[int]

    @classmethod
    def from_post(cls, request) -> "TodoReorderSchema":
        raw = request.POST.get("ids", "").split(",")
        obj = cls(ids=[int(i) for i in raw if i.strip().isdigit()])
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
