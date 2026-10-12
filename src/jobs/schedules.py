"""Every recurring job, in one place. `ensure_schedules` writes these into django-q2's schedule table;
the cluster (qcluster) runs them. One-off jobs (a recording to transcribe) are queued where they happen."""
from django_q.models import Schedule

SCHEDULES = [
    # name,                     function,                             every (minutes)
    ("Produce scheduled todos",  "scheduled.tasks.produce_all",       60),
    ("Sync calendars",           "calendars.tasks.sync_all",          10),  # the net under pushes: one missed is caught within ten minutes
    ("Close ended meetings",     "todos.tasks.close_ended_meetings",  1),   # the clock: a meeting turns done (live) as its time runs out
    ("Process stuck recordings", "recordings.tasks.process_stuck",    15),
]


def ensure() -> list[str]:
    """Create or update each schedule by name, and drop ones no longer listed. Safe on every start."""
    names = []
    for name, func, minutes in SCHEDULES:
        Schedule.objects.update_or_create(name=name, defaults={
            "func": func, "schedule_type": Schedule.MINUTES, "minutes": minutes, "repeats": -1,
        })
        names.append(name)
    Schedule.objects.exclude(name__in=names).delete()
    return names
