from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

from comments.service import CommentService
from recordings.services import GroqService, RecordingService
from scheduled import rhythm
from scheduled.services import ProductionService
from todos import reminders
from todos.models import Todo
from todos.services import InsightsService, RemovedTodo, TodoService

AGE_SHOWN_FROM = 1  # days on the list before a row (or its thread) shows how long


@dataclass(frozen=True)
class BoardRow:
    id: int
    title: str
    position: int   # the number shown: 1..n down the whole list; closing one changes no one's number
    age: int
    kind: str
    when: str = ""          # a meeting's start, as the row says it (10am)
    span: str = ""          # a meeting's start – end, as the thread says it
    meeting_url: str = ""
    starts_iso: str = ""    # a meeting's start, for the page's "it started: record the call?" nudge
    event_url: str = ""     # a meeting's event in its calendar: the row links out to it
    from_schedule: bool = False  # Scheduled produced it today: a clock marks the row
    schedule_label: str = ""     # its rhythm, while the scheduled item is there: "every day"
    is_done: bool = False        # done today: still in its place

    @property
    def is_meeting(self) -> bool:
        return self.kind == Todo.Kind.MEETING

    @property
    def shows_age(self) -> bool:
        """A todo's age only earns a mention once it has been waiting a while."""
        return self.age >= AGE_SHOWN_FROM

    @property
    def added(self) -> str:
        """The thread's word for its age: added yesterday, added 3 days ago."""
        return "added yesterday" if self.age == 1 else f"added {self.age} days ago"

    @property
    def days_on_list(self) -> str:
        if self.age == 0:
            return "added today"
        return f"{self.age} day{'' if self.age == 1 else 's'} on list"


@dataclass(frozen=True)
class DoneRow:
    id: int
    title: str
    closed_on: str   # how long ago, as the open rows show their age: "1d", "2d"
    closed_date: str = ""   # the day itself, for the tooltip


RECENTLY_DONE = 10


class BoardService:
    """Read model for the board screen: the numbered list on the left, one todo's thread on the right."""

    @staticmethod
    def rows(user) -> list[BoardRow]:
        now = timezone.now()
        today = timezone.localdate()
        return [BoardService._row(todo, position, now, today) for position, todo in enumerate(TodoService.listed_for(user), 1)]

    @staticmethod
    def _row(todo: Todo, position: int, now: datetime, today) -> BoardRow:
        return BoardRow(
            id=todo.id,
            title=todo.title,
            position=position,
            age=InsightsService.days_between(todo.created_at, now),
            kind=todo.kind,
            when=reminders.clock(todo.starts_at) if todo.is_meeting and todo.starts_at else "",
            span=f"{reminders.exact(todo.starts_at)} – {reminders.clock(todo.ends_at)}" if todo.is_meeting and todo.ends_at else "",
            meeting_url=todo.meeting_url,
            starts_iso=todo.starts_at.isoformat() if todo.is_meeting and todo.starts_at else "",
            event_url=todo.event_url,
            is_done=todo.closed_at is not None,
            from_schedule=todo.produced_on == today,
            schedule_label=rhythm.label(todo.scheduled_item, today) if todo.scheduled_item else "",
        )

    @staticmethod
    def done(user) -> list[DoneRow]:
        """The last few todos closed, newest first: they close out the list, one click from reopening."""
        now = timezone.now()
        rows = []
        for todo in TodoService.recently_closed(user, RECENTLY_DONE):
            day = timezone.localtime(todo.closed_at).date()
            days = InsightsService.days_between(todo.closed_at, now)   # calendar days, as the open rows count their age
            rows.append(DoneRow(id=todo.id, title=todo.title, closed_on=f"{days}d" if days > 0 else "today", closed_date=f"{day:%b} {day.day}"))
        return rows

    @staticmethod
    def listing(user) -> dict:
        """Today's list: whatever scheduled items are due first land on it, then the user's order,
        today's meetings, and the last few done ones."""
        ProductionService.produce_due(user)              # what's scheduled for today lands on top…
        TodoService.place_todays_meetings(user)          # …and today's meetings above it; carry-overs follow
        TodoService.close_ended_meetings(user)
        today = timezone.localdate()
        return {
            "rows":         BoardService.rows(user),
            "today_label":  f"{today:%a}, {today:%b} {today.day}",  # Sun, Oct 11
            "done":         BoardService.done(user),
            "reopen_index": TodoService.ordered_for(user).count() + 1,
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
        """Removing the todo being read moves the thread to whichever todo slid into its place."""
        if selected_id != removed.id:
            return selected_id
        rows = BoardService.rows(user)
        return rows[min(max(removed.index, 1), len(rows)) - 1].id if rows else None

    @staticmethod
    def workspace(user, selected_id: int | None, undo: RemovedTodo | None = None) -> dict:
        context = BoardService.listing(user)
        current = BoardService.select(context["rows"], selected_id)
        return {
            **context,
            "current":    current,
            "comments":   CommentService.for_todo(current.id) if current else [],
            "undo":       undo,
            "recorder":   RecordingService.composer(user, current),
            "transcript": bool(current and current.is_meeting and RecordingService.has_transcript(current.id)),
            "can_record": not user.is_guest and GroqService.is_configured(),  # the start-of-meeting nudge
        }
