from dataclasses import dataclass

from django.utils import timezone

from comments.service import CommentService
from todos.schemas import ReviewFilterSchema, ReviewPeriod, ReviewStatus
from todos.services import InsightsService, TodoService


@dataclass(frozen=True)
class ReviewRow:
    id: int
    title: str
    status: str
    stamp: str
    days_on_list: int

    @property
    def can_reopen(self) -> bool:
        return self.status != ReviewStatus.OPEN

    @property
    def state_label(self) -> str:
        return {ReviewStatus.DONE: "Closed", ReviewStatus.DELETED: "Deleted"}.get(self.status, "Open")


class ReviewService:
    """Read model for the review modal: every todo by state and period, what that period adds up
    to."""

    @staticmethod
    def build(user, filters: ReviewFilterSchema) -> dict:
        insights = InsightsService.for_period(user, filters.period)
        return {
            "filters":  filters,
            "statuses": ReviewStatus.choices,
            "periods":  ReviewPeriod.choices,
            "insights": insights,
            "rows":     ReviewService.rows(user, filters),
        }

    @staticmethod
    def rows(user, filters: ReviewFilterSchema) -> list[ReviewRow]:
        since = InsightsService.window(filters.period).start
        return [ReviewService.row(todo) for todo in TodoService.reviewed(user, filters.status, since)]

    @staticmethod
    def row(todo) -> ReviewRow:
        ended = todo.deleted_at or todo.closed_at
        if todo.deleted_at:
            status = ReviewStatus.DELETED
        elif todo.closed_at:
            status = ReviewStatus.DONE
        else:
            status = ReviewStatus.OPEN
        return ReviewRow(
            id=todo.id,
            title=todo.title,
            status=status,
            stamp=ReviewService._day(timezone.localtime(ended).date()) if ended else f"#{todo.position}",
            days_on_list=InsightsService.days_between(todo.created_at, ended or timezone.now()),
        )

    @staticmethod
    def thread(todo, filters: ReviewFilterSchema) -> dict:
        return {"filters": filters, "row": ReviewService.row(todo), "comments": CommentService.for_todo(todo.id)}

    @staticmethod
    def _day(day) -> str:
        return f"{day:%b} {day.day}"
