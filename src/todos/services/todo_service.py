from dataclasses import dataclass
from datetime import date, timedelta

from django.db import transaction
from django.db.models import F, Q, QuerySet
from django.utils import timezone

from todos.models import Todo
from todos.schemas import LATER, ReviewStatus
from todos.services.history_service import PositionHistoryService

HORIZON_DAYS = 6  # today and the next five days can each have a heading; anything further is "later"


@dataclass(frozen=True)
class RemovedTodo:
    """What Undo needs to put a closed or deleted todo back where it was."""
    id: int
    title: str
    day: date
    index: int
    removal: Todo.Removal


class TodoService:
    @staticmethod
    def open_queryset() -> QuerySet:
        return Todo.objects.filter(closed_at__isnull=True, deleted_at__isnull=True)

    @staticmethod
    def any_queryset() -> QuerySet:
        return Todo.objects.all()

    @staticmethod
    def removed_queryset() -> QuerySet:
        return Todo.objects.exclude(closed_at__isnull=True, deleted_at__isnull=True)

    @staticmethod
    def task_queryset() -> QuerySet:
        """What the user can rename, close and delete: their own todos, never a calendar's meetings."""
        return TodoService.open_queryset().filter(kind=Todo.Kind.TASK)

    @staticmethod
    def open_for(user) -> QuerySet:
        return TodoService.open_queryset().filter(user=user)

    @staticmethod
    def listed_for(user) -> QuerySet:
        """What the board lists: every open task, and meetings from today on if the user shows them."""
        shown = Q(kind=Todo.Kind.TASK)
        if user.show_meetings:
            shown |= Q(kind=Todo.Kind.MEETING, day__gte=timezone.localdate())
        return TodoService.open_for(user).filter(shown)

    @staticmethod
    def horizon_end() -> date:
        """The last day with a heading of its own."""
        return timezone.localdate() + timedelta(days=HORIZON_DAYS - 1)

    @staticmethod
    def day_count(user, day: date) -> int:
        return TodoService.open_for(user).filter(day=day).count()

    @staticmethod
    def reviewed(user, status: str, since) -> QuerySet:
        """Todos in one state, newest first; `since` limits them to the period (None: all time)."""
        if status == ReviewStatus.OPEN:
            todos, stamp = TodoService.open_for(user).filter(kind=Todo.Kind.TASK), "created_at"
        elif status == ReviewStatus.DELETED:
            todos, stamp = Todo.objects.filter(user=user, deleted_at__isnull=False).order_by("-deleted_at"), "deleted_at"
        else:
            todos, stamp = Todo.objects.filter(user=user, closed_at__isnull=False, deleted_at__isnull=True).order_by("-closed_at"), "closed_at"
        return todos if since is None else todos.filter(**{f"{stamp}__gte": since})

    @staticmethod
    def recently_closed(user, limit: int) -> QuerySet:
        return Todo.objects.filter(user=user, closed_at__isnull=False, deleted_at__isnull=True).order_by("-closed_at")[:limit]

    @staticmethod
    def matching_title_ids(user, query: str) -> set[int]:
        return set(TodoService.listed_for(user).filter(title__icontains=query).values_list("id", flat=True))

    @staticmethod
    @transaction.atomic
    def create(user, data) -> Todo:
        today = timezone.localdate()
        return Todo.objects.create(user=user, title=data.title, day=today, position=TodoService.day_count(user, today) + 1)

    @staticmethod
    def rename(todo: Todo, data) -> Todo:
        todo.title = data.title
        todo.save(update_fields=["title", "updated_at"])
        return todo

    @staticmethod
    @transaction.atomic
    def remind(todo: Todo, data) -> Todo:
        """Set or clear a task's reminder. A reminder files the task under the day it fires."""
        todo.remind_at = data.remind_at
        todo.save(update_fields=["remind_at", "updated_at"])
        if data.remind_at is not None:
            TodoService.move_to_day(todo, timezone.localtime(data.remind_at).date())
        return todo

    @staticmethod
    @transaction.atomic
    def roll_over(user) -> None:
        """Unfinished tasks from past days move to the top of today, in the order they were in."""
        today = timezone.localdate()
        stale = list(TodoService.open_for(user).filter(kind=Todo.Kind.TASK, day__lt=today))
        if not stale:
            return
        for todo in stale:
            todo.day = today
        TodoService._number(stale + list(TodoService.open_for(user).filter(day=today).exclude(pk__in=[t.pk for t in stale])))

    @staticmethod
    @transaction.atomic
    def close(todo: Todo) -> RemovedTodo:
        return TodoService._remove(todo, "closed_at", Todo.Removal.CLOSED)

    @staticmethod
    @transaction.atomic
    def delete(todo: Todo) -> RemovedTodo:
        return TodoService._remove(todo, "deleted_at", Todo.Removal.DELETED)

    @staticmethod
    def purge(todo: Todo) -> None:
        """Delete for good: only for a todo already in Deleted. Its comments and files go with it."""
        todo.delete()

    @staticmethod
    @transaction.atomic
    def reopen(todo: Todo, data) -> None:
        """Back on the list at `index` within its day; a task whose day has passed comes back today."""
        day = max(todo.day, timezone.localdate())
        siblings = TodoService.open_for(todo.user).filter(day=day)
        index = min(max(data.index, 1), siblings.count() + 1)
        siblings.filter(position__gte=index).update(position=F("position") + 1)
        todo.closed_at  = None
        todo.deleted_at = None
        todo.day        = day
        todo.position   = index
        todo.save(update_fields=["closed_at", "deleted_at", "day", "position", "updated_at"])
        TodoService._renumber_day(todo.user, day)

    @staticmethod
    @transaction.atomic
    def reorder(user, data) -> None:
        """Apply the order given, day by day. A task goes to the day it was dropped in; a meeting
        stays on its own day whatever the client says. On every day touched, todos the client
        didn't mention keep their relative place at the end."""
        listed = {t.id: t for t in TodoService.listed_for(user)}
        placed: dict[date, list[Todo]] = {}
        touched: set[date] = set()
        for todo_id, token in zip(data.ids, data.days or [""] * len(data.ids)):
            todo = listed.pop(todo_id, None)
            if todo is None:
                continue
            touched.add(todo.day)
            todo.day = TodoService._dropped_day(todo, token)
            placed.setdefault(todo.day, []).append(todo)
        touched |= placed.keys()

        moved_ids = {t.id for todos in placed.values() for t in todos}
        for day in sorted(touched):
            rest = [t for t in TodoService.open_for(user).filter(day=day) if t.id not in moved_ids]
            TodoService._number(placed.get(day, []) + rest)

    @staticmethod
    @transaction.atomic
    def move_to_day(todo: Todo, day: date) -> None:
        """File a todo under another day, at the end of it (a meeting goes in by its time)."""
        if day == todo.day:
            return
        old_day = todo.day
        todo.day = day
        todo.save(update_fields=["day", "updated_at"])
        TodoService._renumber_day(todo.user, old_day)
        if todo.is_meeting:
            TodoService.slot_meeting(todo)
        else:
            rest = list(TodoService.open_for(todo.user).filter(day=day).exclude(pk=todo.pk))
            TodoService._number(rest + [todo])

    @staticmethod
    @transaction.atomic
    def slot_meeting(meeting: Todo) -> None:
        """Put a meeting among its day's todos: before the first meeting that starts later than it,
        else after the last one, else at the end of the day."""
        rest = list(TodoService.open_for(meeting.user).filter(day=meeting.day).exclude(pk=meeting.pk))
        later = next((i for i, t in enumerate(rest) if t.is_meeting and t.starts_at and t.starts_at > meeting.starts_at), None)
        if later is None:
            last = max((i for i, t in enumerate(rest) if t.is_meeting), default=len(rest) - 1)
            later = last + 1
        TodoService._number(rest[:later] + [meeting] + rest[later:])

    @staticmethod
    @transaction.atomic
    def redate_meetings(user) -> None:
        """After a time zone change, file every meeting under the day it falls on in the active zone."""
        for meeting in TodoService.open_for(user).filter(kind=Todo.Kind.MEETING, starts_at__isnull=False):
            TodoService.move_to_day(meeting, timezone.localtime(meeting.starts_at).date())

    @staticmethod
    @transaction.atomic
    def drop_meeting(meeting: Todo) -> None:
        """A meeting that left the calendar leaves the list; its thread goes with it."""
        user, day = meeting.user, meeting.day
        meeting.delete()
        TodoService._renumber_day(user, day)

    @staticmethod
    @transaction.atomic
    def transfer(from_user, to_user) -> None:
        """Hand every todo of one user to another; open ones join the end of the recipient's days."""
        arriving = list(TodoService.open_for(from_user))
        Todo.objects.filter(user=from_user).update(user=to_user)
        arriving_ids = {t.id for t in arriving}
        for day in sorted({t.day for t in arriving}):
            staying = [t for t in TodoService.open_for(to_user).filter(day=day) if t.id not in arriving_ids]
            TodoService._number(staying + [t for t in arriving if t.day == day])

    @staticmethod
    def _dropped_day(todo: Todo, token: str) -> date:
        if todo.is_meeting:
            return todo.day
        today = timezone.localdate()
        if token == LATER:
            return max(todo.day, TodoService.horizon_end() + timedelta(days=1))
        try:
            return max(date.fromisoformat(token), today)
        except ValueError:
            return max(todo.day, today)

    @staticmethod
    def _remove(todo: Todo, stamp: str, removal: Todo.Removal) -> RemovedTodo:
        removed = RemovedTodo(id=todo.id, title=todo.title, day=todo.day, index=todo.position, removal=removal)
        setattr(todo, stamp, timezone.now())
        todo.save(update_fields=[stamp, "updated_at"])
        TodoService._renumber_day(todo.user, todo.day)
        return removed

    @staticmethod
    def _renumber_day(user, day: date) -> None:
        TodoService._number(list(TodoService.open_for(user).filter(day=day)))

    @staticmethod
    def _number(todos: list[Todo]) -> None:
        """Number one day's todos 1..n in the order given, and record today's for the history."""
        for position, todo in enumerate(todos, 1):
            todo.position = position
        Todo.objects.bulk_update(todos, ["day", "position"])
        today = timezone.localdate()
        PositionHistoryService.snapshot([t for t in todos if t.day == today])
