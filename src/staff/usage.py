from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Sum
from django.db.models.functions import TruncDate
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from accounts.models import User
from calendars.models import CalendarConnection
from comments.models import Comment
from recordings.models import Recording
from todos.models import Todo
from voice.models import VoiceCommand

COST = Sum(F("llm_cost") + F("stt_cost"), output_field=DecimalField(max_digits=12, decimal_places=6))


def money(amount) -> str:
    amount = Decimal(amount or 0)
    cents = amount.quantize(Decimal("0.01"))
    return f"${cents}" if cents == amount else f"${amount.quantize(Decimal('0.0001'))}"


def percentile(values: list[int], q: float) -> int:
    if not values:
        return 0
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * q))]


def link(name: str, *args, query: str = "") -> str:
    try:
        return reverse(name, args=args) + query
    except NoReverseMatch:
        return ""


class UsageReport:
    """How Miller is being used and where it's going wrong, from the data it already keeps."""

    @staticmethod
    def active_ids(since) -> set[int]:
        """Everyone who did something since then: touched a todo, wrote a note, or spoke to Miller."""
        todos = Todo.objects.filter(updated_at__gte=since).values_list("user_id", flat=True)
        notes = Comment.objects.filter(created_at__gte=since, automatic=False).values_list("todo__user_id", flat=True)
        voice = VoiceCommand.objects.filter(created_at__gte=since).values_list("user_id", flat=True)
        return set(todos) | set(notes) | set(voice)

    @staticmethod
    def people() -> dict:
        now = timezone.now()
        guests = set(User.objects.filter(is_guest=True).values_list("id", flat=True))
        active = {label: UsageReport.active_ids(now - delta) for label, delta in (("24h", timedelta(days=1)), ("7d", timedelta(days=7)), ("30d", timedelta(days=30)))}
        return {
            "members": User.objects.filter(is_guest=False).count(),
            "guests": len(guests),
            "new_24h": User.objects.filter(created_at__gte=now - timedelta(days=1)).count(),
            "new_7d": User.objects.filter(created_at__gte=now - timedelta(days=7)).count(),
            "active": {label: {"members": len(ids - guests), "guests": len(ids & guests)} for label, ids in active.items()},
        }

    @staticmethod
    def voice(days: int = 7) -> dict:
        since = timezone.now() - timedelta(days=days)
        commands = VoiceCommand.objects.filter(created_at__gte=since)
        outcomes = Counter(commands.values_list("outcome", flat=True))
        spoken = commands.exclude(transcript="")
        latency = list(spoken.filter(outcome__in=("applied", "asked")).values_list("total_ms", flat=True)[:2000])
        total = sum(outcomes.values())
        return {
            "days": days, "total": total, "outcomes": dict(outcomes),
            "failed": outcomes.get("failed", 0),
            "failure_rate": f"{outcomes.get('failed', 0) / total:.0%}" if total else "–",
            "users": commands.values("user").distinct().count(),
            "p50": percentile(latency, 0.5), "p95": percentile(latency, 0.95),
            "spend": money(commands.aggregate(c=COST)["c"]),
            "per_command": money((commands.aggregate(c=COST)["c"] or 0) / total) if total else "–",
            "models": list(commands.exclude(model="").values("model").annotate(n=Count("id")).order_by("-n")),
        }

    @staticmethod
    def daily(days: int = 30) -> list[dict]:
        """One row per day, newest first: what was new, and what was done."""
        since = timezone.now() - timedelta(days=days)
        rows = defaultdict(lambda: {"new_users": 0, "todos": 0, "closed": 0, "voice": 0, "failed": 0, "spend": Decimal(0)})
        for day, n in User.objects.filter(created_at__gte=since).annotate(d=TruncDate("created_at")).values_list("d").annotate(n=Count("id")):
            rows[day]["new_users"] = n
        for day, n in Todo.objects.filter(created_at__gte=since, kind=Todo.Kind.TASK).annotate(d=TruncDate("created_at")).values_list("d").annotate(n=Count("id")):
            rows[day]["todos"] = n
        for day, n in Todo.objects.filter(closed_at__gte=since, kind=Todo.Kind.TASK).annotate(d=TruncDate("closed_at")).values_list("d").annotate(n=Count("id")):
            rows[day]["closed"] = n
        for day, outcome, n, spend in (VoiceCommand.objects.filter(created_at__gte=since).annotate(d=TruncDate("created_at"))
                                       .values_list("d", "outcome").annotate(n=Count("id"), c=COST).values_list("d", "outcome", "n", "c")):
            rows[day]["voice"] += n
            rows[day]["failed"] += n if outcome == "failed" else 0
            rows[day]["spend"] += spend or 0
        return [{"day": day, **{**row, "spend": money(row["spend"])}} for day, row in sorted(rows.items(), reverse=True)]

    @staticmethod
    def top_spenders(limit: int = 10) -> list[dict]:
        rows = (VoiceCommand.objects.values("user", "user__email", "user__is_guest").annotate(n=Count("id"), spent=COST).order_by("-spent")[:limit])
        return [{"email": r["user__email"], "guest": r["user__is_guest"], "commands": r["n"], "spent": money(r["spent"]),
                 "url": link("admin:accounts_user_change", r["user"])} for r in rows]

    @staticmethod
    def problems() -> dict:
        """What to look at first when something's wrong."""
        now = timezone.now()
        failed_voice = VoiceCommand.objects.filter(outcome="failed", created_at__gte=now - timedelta(days=1))
        return {
            "voice_failed_24h": failed_voice.count(),
            "voice_failed_url": link("admin:voice_voicecommand_changelist", query="?outcome__exact=failed"),
            "recordings_failed": Recording.objects.filter(status="failed").count(),
            "recordings_url": link("admin:recordings_recording_changelist", query="?status__exact=failed"),
            "calendars_lost": CalendarConnection.objects.exclude(status="connected").count(),
            "calendars_url": link("admin:calendars_calendarconnection_changelist"),
            "jobs_failed_24h": UsageReport.failed_jobs(now - timedelta(days=1)),
            "jobs_url": link("admin:django_q_failure_changelist"),
        }

    @staticmethod
    def failed_jobs(since) -> int:
        try:
            from django_q.models import Failure
            return Failure.objects.filter(stopped__gte=since).count()
        except Exception:  # noqa: BLE001 - the job tables are optional to the report
            return 0

    @staticmethod
    def recent_failures(limit: int = 10) -> list[dict]:
        rows = VoiceCommand.objects.filter(outcome__in=("failed",)).select_related("user").order_by("-created_at")[:limit]
        return [{"when": c.created_at, "user": c.user.email, "said": c.transcript, "reply": c.reply, "url": link("admin:voice_voicecommand_change", c.pk)} for c in rows]

    @staticmethod
    def headline() -> dict:
        """The numbers at the top of the admin's front page."""
        people, voice = UsageReport.people(), UsageReport.voice(1)
        return {
            "people": people, "voice": voice, "problems": UsageReport.problems(),
            "todos_today": Todo.objects.filter(created_at__gte=timezone.now() - timedelta(days=1), kind=Todo.Kind.TASK).count(),
            "spend_total": money(VoiceCommand.objects.aggregate(c=COST)["c"]),
        }

    @staticmethod
    def full() -> dict:
        return {
            "people": UsageReport.people(), "voice": UsageReport.voice(7), "daily": UsageReport.daily(30),
            "spenders": UsageReport.top_spenders(), "problems": UsageReport.problems(), "failures": UsageReport.recent_failures(),
            "spend_total": money(VoiceCommand.objects.aggregate(c=COST)["c"]),
        }
