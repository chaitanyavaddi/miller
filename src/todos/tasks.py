"""Todo jobs, run by the django-q2 cluster (qcluster)."""
from django.contrib.auth import get_user_model
from django.utils import timezone

from live.bus import publish
from todos.models import Todo
from todos.services import TodoService


def close_ended_meetings() -> int:
    """Every minute: meetings whose time ran out are closed (done), and each owner's open pages refresh at
    once, whether or not anyone is looking. Returns how many were closed."""
    ended = Todo.objects.filter(kind=Todo.Kind.MEETING, closed_at__isnull=True, deleted_at__isnull=True, ends_at__lt=timezone.now())
    closed = 0
    for user in get_user_model().objects.filter(pk__in=ended.values("user_id")):
        count = TodoService.close_ended_meetings(user)
        if count:
            publish(user.pk, "meetings-changed")
            closed += count
    return closed
