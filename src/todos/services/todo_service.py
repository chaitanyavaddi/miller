from dataclasses import dataclass
from datetime import date, timedelta

from django.db import transaction
from django.db.models import F, Max, Q, QuerySet
from django.utils import timezone

from todos.models import Todo
from todos.schemas import ReviewStatus
from todos.services.history_service import PositionHistoryService


@dataclass(frozen=True)
class RemovedTodo:
    """What Undo needs to put a closed or deleted todo back where it was."""
    id: int
    title: str
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
    def live_queryset() -> QuerySet:
        """Everything not deleted: open todos, and closed ones whose thread can still be opened."""
        return Todo.objects.filter(deleted_at__isnull=True)

    @staticmethod
    def start_of_today():
        return timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def task_queryset() -> QuerySet:
        """What the user can rename, close and delete: their own todos, never a calendar's meetings."""
        return TodoService.open_queryset().filter(kind=Todo.Kind.TASK)

    @staticmethod
    def open_for(user) -> QuerySet:
        return TodoService.open_queryset().filter(user=user)

    @staticmethod
    def ordered_for(user) -> QuerySet:
        """The user's own order: their open tasks, and those done today — a done todo keeps its place,
        struck through, until the day is over."""
        done_today = Q(closed_at__gte=TodoService.start_of_today())
        return (TodoService.live_queryset().filter(user=user, kind=Todo.Kind.TASK)
                .filter(Q(closed_at__isnull=True) | done_today))

    @staticmethod
    def listed_for(user) -> QuerySet:
        """Today's list: the user's tasks in their order (today's done ones still in place), then (if
        the user shows them) today's meetings that haven't ended, by start time."""
        now = timezone.localtime()
        midnight = TodoService.start_of_today()
        shown = Q(kind=Todo.Kind.TASK) & (Q(closed_at__isnull=True) | Q(closed_at__gte=midnight))
        if user.show_meetings:
            shown |= Q(kind=Todo.Kind.MEETING, closed_at__isnull=True, ends_at__gte=now, starts_at__lt=midnight + timedelta(days=1))
        return (TodoService.live_queryset().filter(user=user).filter(shown).select_related("scheduled_item")
                .order_by(F("starts_at").asc(nulls_first=True), "position", "id"))

    @staticmethod
    def reviewed(user, status: str, since) -> QuerySet:
        """Todos in one state, newest first; `since` limits them to the period (None: all time)."""
        if status == ReviewStatus.OPEN:
            todos, stamp = TodoService.open_for(user).filter(kind=Todo.Kind.TASK).order_by("position"), "created_at"
        elif status == ReviewStatus.DELETED:
            todos, stamp = Todo.objects.filter(user=user, deleted_at__isnull=False).order_by("-deleted_at"), "deleted_at"
        else:
            todos, stamp = Todo.objects.filter(user=user, closed_at__isnull=False, deleted_at__isnull=True).order_by("-closed_at"), "closed_at"
        return todos if since is None else todos.filter(**{f"{stamp}__gte": since})

    @staticmethod
    def recently_closed(user, limit: int) -> QuerySet:
        """Done before today: today's are still in their place on the list."""
        return (Todo.objects.filter(user=user, closed_at__lt=TodoService.start_of_today(), deleted_at__isnull=True)
                .order_by("-closed_at")[:limit])

    @staticmethod
    def matching_title_ids(user, query: str) -> set[int]:
        return set(TodoService.open_for(user).filter(title__icontains=query).values_list("id", flat=True))

    @staticmethod
    @transaction.atomic
    def create(user, data) -> Todo:
        last = TodoService.ordered_for(user).aggregate(last=Max("position"))["last"] or 0
        todo = Todo.objects.create(user=user, title=data.title, position=last + 1)
        TodoService._renumber(user)  # also closes the gaps yesterday's done todos left
        todo.refresh_from_db(fields=["position"])
        return todo

    @staticmethod
    def rename(todo: Todo, data) -> Todo:
        todo.title = data.title
        todo.save(update_fields=["title", "updated_at"])
        return todo

    @staticmethod
    @transaction.atomic
    def produce(user, title: str, scheduled_item, day: date) -> Todo:
        """A todo a scheduled item made for `day`, at the top of the list."""
        TodoService.ordered_for(user).update(position=F("position") + 1)
        todo = Todo.objects.create(user=user, title=title, position=1, scheduled_item=scheduled_item, produced_on=day)
        TodoService._renumber(user)
        return todo

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
        """Back on the list at `index` in the user's order. One done today never left its place: it's
        just open again."""
        if todo.deleted_at is None and todo.closed_at and todo.closed_at >= TodoService.start_of_today():
            todo.closed_at = None
            todo.save(update_fields=["closed_at", "updated_at"])
            return
        index = min(max(data.index, 1), TodoService.ordered_for(todo.user).count() + 1)
        TodoService.ordered_for(todo.user).filter(position__gte=index).update(position=F("position") + 1)
        todo.closed_at  = None
        todo.deleted_at = None
        todo.position   = index
        todo.save(update_fields=["closed_at", "deleted_at", "position", "updated_at"])
        TodoService._renumber(todo.user)

    @staticmethod
    @transaction.atomic
    def reorder(user, data) -> None:
        """Apply the order given; any task the client didn't mention keeps its relative place at the
        end. Meetings aren't part of it: their time orders them."""
        by_id = {t.id: t for t in TodoService.ordered_for(user)}
        ordered = [by_id.pop(i) for i in data.ids if i in by_id] + list(by_id.values())
        TodoService._number(ordered)

    @staticmethod
    @transaction.atomic
    def transfer(from_user, to_user) -> None:
        """Hand every todo of one user to another; open ones join the end of the recipient's list."""
        last = TodoService.ordered_for(to_user).aggregate(last=Max("position"))["last"] or 0
        for offset, todo in enumerate(TodoService.ordered_for(from_user), 1):
            Todo.objects.filter(pk=todo.pk).update(user=to_user, position=last + offset)
        Todo.objects.filter(user=from_user).update(user=to_user)

    @staticmethod
    def _remove(todo: Todo, stamp: str, removal: Todo.Removal) -> RemovedTodo:
        removed = RemovedTodo(id=todo.id, title=todo.title, index=todo.position, removal=removal)
        setattr(todo, stamp, timezone.now())
        todo.save(update_fields=[stamp, "updated_at"])
        TodoService._renumber(todo.user)
        return removed

    @staticmethod
    def _renumber(user) -> None:
        TodoService._number(list(TodoService.ordered_for(user)))

    @staticmethod
    def _number(todos: list[Todo]) -> None:
        for position, todo in enumerate(todos, 1):
            todo.position = position
        Todo.objects.bulk_update(todos, ["position"])
        PositionHistoryService.snapshot(todos)
