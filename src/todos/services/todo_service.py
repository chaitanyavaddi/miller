from dataclasses import dataclass

from django.db import transaction
from django.db.models import F, Max, QuerySet
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
    def open_for(user) -> QuerySet:
        return TodoService.open_queryset().filter(user=user)

    @staticmethod
    def reviewed(user, status: str, since) -> QuerySet:
        """Todos in one state, newest first; `since` limits them to the period (None: all time)."""
        if status == ReviewStatus.OPEN:
            todos, stamp = TodoService.open_for(user).order_by("position"), "created_at"
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
        return set(TodoService.open_for(user).filter(title__icontains=query).values_list("id", flat=True))

    @staticmethod
    @transaction.atomic
    def create(user, data) -> Todo:
        todo = Todo.objects.create(user=user, title=data.title, position=TodoService.open_for(user).count() + 1)
        PositionHistoryService.snapshot(list(TodoService.open_for(user)))
        return todo

    @staticmethod
    def rename(todo: Todo, data) -> Todo:
        todo.title = data.title
        todo.save(update_fields=["title", "updated_at"])
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
        index = min(max(data.index, 1), TodoService.open_for(todo.user).count() + 1)
        TodoService.open_for(todo.user).filter(position__gte=index).update(position=F("position") + 1)
        todo.closed_at  = None
        todo.deleted_at = None
        todo.position   = index
        todo.save(update_fields=["closed_at", "deleted_at", "position", "updated_at"])
        TodoService._renumber(todo.user)

    @staticmethod
    @transaction.atomic
    def reorder(user, data) -> None:
        """Apply the order given; any open todo the client didn't mention keeps its relative place at the end."""
        by_id = {t.id: t for t in TodoService.open_for(user)}
        ordered = [by_id.pop(i) for i in data.ids if i in by_id] + list(by_id.values())
        TodoService._number(ordered)

    @staticmethod
    @transaction.atomic
    def transfer(from_user, to_user) -> None:
        """Hand every todo of one user to another; open ones join the end of the recipient's list."""
        last = TodoService.open_for(to_user).aggregate(last=Max("position"))["last"] or 0
        for offset, todo in enumerate(TodoService.open_for(from_user), 1):
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
        TodoService._number(list(TodoService.open_for(user)))

    @staticmethod
    def _number(todos: list[Todo]) -> None:
        for position, todo in enumerate(todos, 1):
            todo.position = position
        Todo.objects.bulk_update(todos, ["position"])
        PositionHistoryService.snapshot(todos)
