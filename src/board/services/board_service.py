from dataclasses import dataclass

from django.utils import timezone

from comments.service import CommentService
from todos.services import InsightsService, RemovedTodo, TodoService


@dataclass(frozen=True)
class BoardRow:
    id: int
    title: str
    position: int
    age: int

    @property
    def days_on_list(self) -> str:
        if self.age == 0:
            return "added today"
        return f"{self.age} day{'' if self.age == 1 else 's'} on list"


@dataclass(frozen=True)
class DoneRow:
    id: int
    title: str
    closed_on: str


RECENTLY_DONE = 10


class BoardService:
    """Read model for the board screen: the numbered list on the left, one todo's thread on the right."""

    @staticmethod
    def rows(user) -> list[BoardRow]:
        now = timezone.now()
        return [
            BoardRow(id=todo.id, title=todo.title, position=position, age=InsightsService.days_between(todo.created_at, now))
            for position, todo in enumerate(TodoService.open_for(user), 1)
        ]

    @staticmethod
    def done(user) -> list[DoneRow]:
        """The last few todos closed, newest first: they close out the list, one click from reopening."""
        today = timezone.localdate()
        rows = []
        for todo in TodoService.recently_closed(user, RECENTLY_DONE):
            day = timezone.localtime(todo.closed_at).date()
            rows.append(DoneRow(id=todo.id, title=todo.title, closed_on="today" if day == today else f"{day:%b} {day.day}"))
        return rows

    @staticmethod
    def listing(user) -> dict:
        """The whole list: active todos in order, then the last few done ones."""
        rows = BoardService.rows(user)
        return {"rows": rows, "done": BoardService.done(user), "reopen_index": len(rows) + 1}

    @staticmethod
    def search(user, query: str) -> list[BoardRow]:
        """Rows whose title, comments or attachment names match, keeping their place in the full list."""
        matching = TodoService.matching_title_ids(user, query) | CommentService.todo_ids_matching(user, query)
        return [row for row in BoardService.rows(user) if row.id in matching]

    @staticmethod
    def select(rows: list[BoardRow], selected_id: int | None) -> BoardRow | None:
        """The selected row, else the first: the thread pane always shows something."""
        return next((row for row in rows if row.id == selected_id), rows[0] if rows else None)

    @staticmethod
    def selection_after_removal(user, removed: RemovedTodo, selected_id: int | None) -> int | None:
        """Removing the todo being read moves the thread to whichever todo slid into its place."""
        if selected_id != removed.id:
            return selected_id
        rows = BoardService.rows(user)
        return rows[min(removed.index, len(rows)) - 1].id if rows else None

    @staticmethod
    def workspace(user, selected_id: int | None, undo: RemovedTodo | None = None) -> dict:
        context = BoardService.listing(user)
        current = BoardService.select(context["rows"], selected_id)
        return {
            **context,
            "current":  current,
            "comments": CommentService.for_todo(current.id) if current else [],
            "undo":     undo,
        }
