from dataclasses import dataclass

from django.utils import timezone

from comments.service import CommentService
from todos.services import InsightsService, TodoService


@dataclass(frozen=True)
class ReviewRow:
    id: int
    title: str
    stamp: str
    days_on_list: int
    is_meeting: bool = False  # from the calendar: no reopen, no delete


class ReviewService:
    """Read model for All todos: the closed ones, the most recently closed first."""

    @staticmethod
    def build(user) -> dict:
        TodoService.close_ended_meetings(user)
        return {"rows": [ReviewService.row(todo) for todo in TodoService.reviewed(user)]}

    @staticmethod
    def row(todo) -> ReviewRow:
        return ReviewRow(
            id=todo.id,
            title=todo.title,
            stamp=ReviewService._day(timezone.localtime(todo.closed_at).date()),
            days_on_list=InsightsService.days_between(todo.created_at, todo.closed_at),
            is_meeting=todo.is_meeting,
        )

    @staticmethod
    def thread(todo) -> dict:
        return {"row": ReviewService.row(todo), "comments": CommentService.for_todo(todo.id)}

    @staticmethod
    def _day(day) -> str:
        return f"{day:%b} {day.day}"
