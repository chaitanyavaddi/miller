from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from comments.service import CommentService
from scheduled import rhythm
from scheduled.models import ScheduledItem
from todos.models import Todo
from todos.services import TodoService

User = get_user_model()


class ProductionService:
    """Turns scheduled items into today's todos. Runs whenever the board is read, so a new day, a
    reload, or a return after days away all catch up — and an item that fell due on several missed
    days still produces only one todo."""

    @staticmethod
    def produce_due(user) -> list[Todo]:
        today = timezone.localdate()
        produced = []
        for item in ScheduledItem.objects.filter(user=user).exclude(title=""):
            if item.last_produced_on == today:
                continue
            start = item.last_produced_on + timedelta(days=1) if item.last_produced_on else timezone.localtime(item.created_at).date()
            if start > today or rhythm.latest_due(item, start, today) is None:
                continue
            todo = ProductionService._produce(item, today)
            if todo is not None:
                produced.append(todo)
        return produced

    @staticmethod
    def produce_all() -> int:
        """For the cron job: every user with something scheduled, each in their own zone, so a todo is
        on the list shortly after that user's midnight. Safe to run as often as you like."""
        made = 0
        for user in User.objects.filter(scheduled_items__isnull=False).distinct():
            with timezone.override(user.tzinfo):
                made += len(ProductionService.produce_due(user))
        return made

    @staticmethod
    @transaction.atomic
    def _produce(item: ScheduledItem, today) -> Todo | None:
        """Claim the day first (only one request can move `last_produced_on` from what it was), then
        make the todo — unless the last one it made is still open, which simply stays where it is.
        A once item is done after this and goes."""
        claimed = ScheduledItem.objects.filter(pk=item.pk, last_produced_on=item.last_produced_on).update(last_produced_on=today)
        if not claimed:
            return None
        todo = None
        if not TodoService.open_for(item.user).filter(scheduled_item=item).exists():
            todo = TodoService.produce(item.user, item.title, item, today)
            CommentService.note(todo, f"From Scheduled · {rhythm.label(item, today)}")
        if item.rhythm == ScheduledItem.Rhythm.ONCE:
            item.delete()
        return todo
