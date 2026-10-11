"""Todos that carried a reminder (a date and time on the row) become "Once" scheduled items with
the same title, date and time, read in the owner's zone. A reminder already past becomes due today,
so it produces its todo on the next load rather than being lost. The old todo goes."""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import migrations
from django.utils import timezone


def _zone(user):
    try:
        return ZoneInfo(user.timezone or settings.TIME_ZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(settings.TIME_ZONE)


def forwards(apps, schema_editor):
    Todo = apps.get_model("todos", "Todo")
    ScheduledItem = apps.get_model("scheduled", "ScheduledItem")
    reminded = Todo.objects.filter(kind="task", remind_at__isnull=False, closed_at__isnull=True, deleted_at__isnull=True).select_related("user")
    for todo in reminded:
        zone = _zone(todo.user)
        local = todo.remind_at.astimezone(zone)
        today = timezone.now().astimezone(zone).date()
        ScheduledItem.objects.create(
            user=todo.user, title=todo.title, rhythm="once",
            on_date=max(local.date(), today), time=local.time().replace(second=0, microsecond=0),
        )
        todo.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("scheduled", "0001_initial"),
        ("todos", "0010_todo_notify_at_todo_produced_on_todo_scheduled_item"),
        ("accounts", "0005_user_show_meetings_user_timezone"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
