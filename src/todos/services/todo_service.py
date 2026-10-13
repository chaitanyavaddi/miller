from dataclasses import dataclass
from datetime import date, timedelta

from django.db import transaction
from django.db.models import F, Max, Q, QuerySet
from django.utils import timezone

from todos.models import Todo


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
    def closed_queryset() -> QuerySet:
        return Todo.objects.filter(closed_at__isnull=False, deleted_at__isnull=True)

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
        """The user's own order: their open tasks and those done today (a done todo keeps its place,
        struck through, until the day is over), and today's meetings, ended ones included."""
        midnight = TodoService.start_of_today()
        tasks = Q(kind=Todo.Kind.TASK, moves__isnull=True) & (Q(closed_at__isnull=True) | Q(closed_at__gte=midnight))
        if user.show_meetings:
            tasks |= Q(kind=Todo.Kind.MEETING, starts_at__gte=midnight, starts_at__lt=midnight + timedelta(days=1))
        return TodoService.live_queryset().filter(user=user).filter(tasks)

    @staticmethod
    def listed_for(user) -> QuerySet:
        """Today's list, in the user's order: their tasks (today's done ones still in place) and (if the
        user shows them) every meeting starting today, which arrives on top and then moves like a todo; one
        that's ended is done, still in its place. A todo moved to a later date isn't on it until that day."""
        midnight = TodoService.start_of_today()
        shown = Q(kind=Todo.Kind.TASK) & (Q(closed_at__isnull=True) | Q(closed_at__gte=midnight))
        if user.show_meetings:
            shown |= Q(kind=Todo.Kind.MEETING, starts_at__gte=midnight, starts_at__lt=midnight + timedelta(days=1))
        return (TodoService.live_queryset().filter(user=user, moves__isnull=True).filter(shown).select_related("scheduled_item")
                .order_by("position", "id"))

    @staticmethod
    @transaction.atomic
    def place_todays_meetings(user) -> int:
        """Today's meetings that haven't taken a place in the order yet go on top, in time order: the day
        opens with its meetings, then whatever came before. Each is placed once; after that it moves like a
        todo. One that turns up later in the day (a new invite) also arrives on top."""
        if not user.show_meetings:
            return 0
        today = timezone.localdate()
        midnight = TodoService.start_of_today()
        arriving = list(Todo.objects.filter(user=user, kind=Todo.Kind.MEETING, deleted_at__isnull=True,
                                            starts_at__gte=midnight, starts_at__lt=midnight + timedelta(days=1))
                        .exclude(listed_on=today).order_by("starts_at", "id"))
        if not arriving:
            return 0
        rest = [todo for todo in TodoService.ordered_for(user).order_by("position", "id") if todo not in arriving]
        for todo in arriving:
            todo.listed_on = today
        Todo.objects.bulk_update(arriving, ["listed_on"])
        TodoService._number(arriving + rest)
        return len(arriving)

    @staticmethod
    def close_ended_meetings(user) -> int:
        """A meeting is done once it's over: closed at its end, like a todo ticked off. Run as the list is
        read; a meeting the calendar moves later opens again on sync."""
        return (Todo.objects.filter(user=user, kind=Todo.Kind.MEETING, closed_at__isnull=True, deleted_at__isnull=True,
                                    ends_at__lt=timezone.now()).update(closed_at=F("ends_at")))

    @staticmethod
    def reviewed(user) -> QuerySet:
        """The user's closed todos and ended meetings, the most recently closed first; deleted ones never show."""
        return (Todo.objects.filter(user=user, closed_at__isnull=False, deleted_at__isnull=True)
                .order_by("-closed_at"))

    @staticmethod
    def recently_closed(user, limit: int) -> QuerySet:
        """Todos done before today (today's are still in their place on the list). Meetings go to Past."""
        return (Todo.objects.filter(user=user, kind=Todo.Kind.TASK, closed_at__lt=TodoService.start_of_today(), deleted_at__isnull=True)
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
    def bring_back(todo: Todo) -> None:
        """A moved todo, back at the top of the list (its move has already gone)."""
        TodoService.ordered_for(todo.user).exclude(pk=todo.pk).update(position=F("position") + 1)
        todo.position = 1
        todo.save(update_fields=["position", "updated_at"])
        TodoService._renumber(todo.user)

    @staticmethod
    @transaction.atomic
    def close(todo: Todo) -> RemovedTodo:
        return TodoService._remove(todo, "closed_at", Todo.Removal.CLOSED)

    @staticmethod
    @transaction.atomic
    def delete(todo: Todo) -> RemovedTodo:
        return TodoService._remove(todo, "deleted_at", Todo.Removal.DELETED)

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
        """Apply the order given (todos and today's meetings alike); any the client didn't mention keep
        their relative place at the end."""
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
