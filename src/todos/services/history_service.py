from datetime import date, timedelta

from django.utils import timezone

from todos.models import PositionDay, Todo


class PositionHistoryService:
    @staticmethod
    def snapshot(todos: list[Todo]) -> None:
        """Record each todo's current position as today's. One row per todo per day, so later
        changes the same day (an undo, another drag) overwrite it."""
        today = timezone.localdate()
        PositionDay.objects.bulk_create(
            [PositionDay(todo=t, day=today, position=t.position) for t in todos],
            update_conflicts=True, unique_fields=["todo", "day"], update_fields=["position", "updated_at"],
        )

    @staticmethod
    def journey(todo: Todo) -> list[tuple[date, int]]:
        """(day, position) for every day the todo was open, carrying a position across quiet days."""
        recorded = {d.day: d.position for d in todo.days.all()}
        day  = timezone.localtime(todo.created_at).date()
        last = timezone.localtime(todo.closed_at).date() if todo.closed_at else timezone.localdate()
        journey, position = [], None
        while day <= last:
            position = recorded.get(day, position)
            if position is not None:
                journey.append((day, position))
            day += timedelta(days=1)
        return journey
