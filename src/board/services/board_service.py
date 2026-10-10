from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from django.utils import timezone

from comments.service import CommentService
from todos import reminders
from todos.models import Todo
from todos.reminders import clock
from todos.schemas import LATER
from todos.services import HORIZON_DAYS, InsightsService, RemovedTodo, TodoService


def day_label(day: date) -> str:
    return f"{day:%a}, {day:%b} {day.day}".lower()


AGE_SHOWN_FROM = 2  # days on the list before a row shows how long


@dataclass(frozen=True)
class BoardRow:
    id: int
    title: str
    position: int   # the number shown: 1..n down the whole list, across days
    age: int
    kind: str
    day: date
    bucket: str     # the heading it sits under: an ISO date, or "later"
    when: str = ""  # the meeting's start, or the reminder's time
    remind_value: str = ""  # the reminder, as a datetime-local field wants it
    remind_label: str = ""  # the reminder, as the thread says it
    span: str = ""  # a meeting's start – end
    meeting_url: str = ""

    @property
    def is_meeting(self) -> bool:
        return self.kind == Todo.Kind.MEETING

    @property
    def is_today(self) -> bool:
        return self.day == timezone.localdate()

    @property
    def day_name(self) -> str:
        return "today" if self.is_today else day_label(self.day)

    @property
    def shows_age(self) -> bool:
        """A todo's age only earns a mention once it has been waiting a while."""
        return self.age >= AGE_SHOWN_FROM

    @property
    def days_on_list(self) -> str:
        if self.age == 0:
            return "added today"
        return f"{self.age} day{'' if self.age == 1 else 's'} on list"


@dataclass(frozen=True)
class DayBucket:
    """One heading of the list: a day of the coming week, or everything after it."""
    key: str
    label: str
    is_today: bool = False
    is_later: bool = False
    rows: list[BoardRow] = field(default_factory=list)

    @property
    def task_count(self) -> int:
        return sum(1 for row in self.rows if not row.is_meeting)


@dataclass(frozen=True)
class DoneRow:
    id: int
    title: str
    closed_on: str


RECENTLY_DONE = 10


class BoardService:
    """Read model for the board screen: the list filed by day on the left, one todo's thread on the right."""

    @staticmethod
    def rows(user) -> list[BoardRow]:
        now = timezone.now()
        horizon_end = TodoService.horizon_end()
        return [
            BoardService._row(todo, position, now, horizon_end)
            for position, todo in enumerate(TodoService.listed_for(user), 1)
        ]

    @staticmethod
    def _row(todo: Todo, position: int, now: datetime, horizon_end: date) -> BoardRow:
        moment = todo.starts_at if todo.is_meeting else todo.remind_at
        return BoardRow(
            id=todo.id,
            title=todo.title,
            position=position,
            age=InsightsService.days_between(todo.created_at, now),
            kind=todo.kind,
            day=todo.day,
            bucket=LATER if todo.day > horizon_end else todo.day.isoformat(),
            when=clock(moment) if moment else "",
            remind_value=timezone.localtime(todo.remind_at).strftime("%Y-%m-%dT%H:%M") if todo.remind_at else "",
            remind_label=reminders.when(todo.remind_at) if todo.remind_at else "",
            span=f"{clock(todo.starts_at)} – {clock(todo.ends_at)}" if todo.starts_at and todo.ends_at else "",
            meeting_url=todo.meeting_url,
        )

    @staticmethod
    def days(rows: list[BoardRow]) -> list[DayBucket]:
        """Today, each of the next few days, then later. All are rendered; a day with nothing in it
        stays hidden except as a drop target while the list is being reordered."""
        today = timezone.localdate()
        buckets = []
        for offset in range(HORIZON_DAYS):
            day = today + timedelta(days=offset)
            buckets.append(DayBucket(key=day.isoformat(), label=day_label(day), is_today=offset == 0,
                                     rows=[row for row in rows if row.day == day]))
        buckets.append(DayBucket(key=LATER, label="later", is_later=True, rows=[row for row in rows if row.bucket == LATER]))
        return buckets

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
        """The whole list filed by day, then the last few done ones. Yesterday's leftovers roll into today first."""
        TodoService.roll_over(user)
        rows = BoardService.rows(user)
        today = timezone.localdate()
        return {
            "rows":         rows,
            "days":         BoardService.days(rows),
            "today_label":  f"{today:%a}, {today:%b} {today.day}",
            "done":         BoardService.done(user),
            "reopen_index": TodoService.day_count(user, today) + 1,
            "presets":      reminders.presets(),
        }

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
        """Removing the todo being read moves the thread to whichever todo slid into its place on that day."""
        if selected_id != removed.id:
            return selected_id
        rows = BoardService.rows(user)
        same_day = [row for row in rows if row.day == removed.day]
        if same_day:
            return same_day[min(removed.index, len(same_day)) - 1].id
        return rows[0].id if rows else None

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
