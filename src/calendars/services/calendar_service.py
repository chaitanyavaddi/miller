from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from calendars.models import CalendarConnection, CalendarEvent
from calendars.services.oauth_service import CalendarGrant, CalendarOAuthService
from calendars.services.recall_service import RecallError, RecallService
from todos.models import Todo
from todos.schemas import TITLE_MAX_LENGTH
from todos.services import TodoService

SYNC_LOOKBACK = timedelta(days=1)  # today's earlier meetings still show; older ones are never listed


@dataclass(frozen=True)
class MeetingFields:
    """What a calendar event becomes on the list."""
    title: str
    starts_at: datetime
    ends_at: datetime
    meeting_url: str
    ical_uid: str


class CalendarService:
    @staticmethod
    def connections_for(user) -> QuerySet:
        return CalendarConnection.objects.filter(user=user)

    @staticmethod
    def can_connect(provider: str) -> bool:
        return RecallService.is_configured() and CalendarOAuthService.is_configured(provider)

    @staticmethod
    def suggested_email(user) -> str:
        """The signed-in address, offered first while its Google calendar isn't connected yet."""
        if user.is_guest:
            return ""
        connected = CalendarService.connections_for(user).filter(provider=CalendarConnection.Provider.GOOGLE, email=user.email)
        return "" if connected.exists() else user.email

    @staticmethod
    def connect(user, provider: str, grant: CalendarGrant) -> CalendarConnection:
        """Hand the calendar to Recall, remember it, show meetings and pull in what's on it."""
        existing = CalendarService.connections_for(user).filter(provider=provider, email=grant.email).first()
        if existing is not None:
            return existing
        config = CalendarOAuthService.config(provider)
        created = RecallService.create_calendar(provider, config.client_id, config.client_secret, grant.refresh_token)
        connection = CalendarConnection.objects.create(
            user=user, provider=provider, email=grant.email, recall_id=created["id"],
            status=CalendarService._status(created.get("status")),
        )
        if not user.show_meetings:
            user.show_meetings = True
            user.save(update_fields=["show_meetings", "updated_at"])
        CalendarService.sync(connection)
        return connection

    @staticmethod
    def disconnect(connection: CalendarConnection) -> None:
        """Stop syncing and take the calendar's meetings off the list (a meeting another connected
        calendar also has stays)."""
        try:
            RecallService.delete_calendar(connection.recall_id)
        except RecallError:
            pass  # already gone on Recall's side, or unreachable: forget it here either way
        with transaction.atomic():
            for event in list(connection.events.select_related("meeting")):
                CalendarService._unlink(event)
            connection.delete()

    @staticmethod
    def sync(connection: CalendarConnection, updated_since: datetime | None = None) -> None:
        """Apply the calendar's events from yesterday on (only those changed since `updated_since`, if
        given). Days are counted in the owner's zone, wherever this runs."""
        now = timezone.now()
        with timezone.override(connection.user.tzinfo):
            for event in RecallService.events(connection.recall_id, now - SYNC_LOOKBACK, updated_since):
                CalendarService.apply(connection, event)
        connection.synced_at = now
        connection.save(update_fields=["synced_at", "updated_at"])

    @staticmethod
    def refresh_status(connection: CalendarConnection) -> None:
        status = CalendarService._status(RecallService.calendar(connection.recall_id).get("status"))
        if status != connection.status:
            connection.status = status
            connection.save(update_fields=["status", "updated_at"])

    @staticmethod
    def handle_webhook(payload: dict) -> None:
        """Recall's calendar webhooks: `calendar.sync_events` (events changed) and `calendar.update`
        (the calendar itself, e.g. it lost access)."""
        data = payload.get("data") or {}
        connection = CalendarConnection.objects.filter(recall_id=data.get("calendar_id", "")).select_related("user").first()
        if connection is None:
            return
        if payload.get("event") == "calendar.sync_events":
            CalendarService.sync(connection, parse_datetime(data.get("last_updated_ts") or "") or None)
        elif payload.get("event") == "calendar.update":
            CalendarService.refresh_status(connection)

    @staticmethod
    @transaction.atomic
    def apply(connection: CalendarConnection, event: dict) -> None:
        fields = CalendarService.meeting_fields(event)
        linked = CalendarEvent.objects.filter(recall_id=event.get("id", "")).select_related("meeting").first()
        if fields is None:
            if linked is not None:
                CalendarService._unlink(linked)
            return
        if linked is not None:
            CalendarService._update(linked.meeting, fields)
            return
        meeting = CalendarService._twin(connection.user, fields) or CalendarService._create(connection.user, fields)
        CalendarEvent.objects.create(connection=connection, meeting=meeting, recall_id=event["id"], ical_uid=fields.ical_uid)

    @staticmethod
    def meeting_fields(event: dict) -> MeetingFields | None:
        """None for anything that shouldn't be on the list: deleted, cancelled, declined or all-day."""
        starts_at = parse_datetime(event.get("start_time") or "")
        ends_at = parse_datetime(event.get("end_time") or "")
        raw = event.get("raw") or {}
        if event.get("is_deleted") or starts_at is None or ends_at is None:
            return None
        if event.get("platform") == "microsoft_outlook":
            title = raw.get("subject")
            skip = raw.get("isAllDay") or raw.get("isCancelled") or (raw.get("responseStatus") or {}).get("response") == "declined"
        else:
            title = raw.get("summary")
            declined = any(a.get("self") and a.get("responseStatus") == "declined" for a in raw.get("attendees") or [])
            skip = "date" in (raw.get("start") or {}) or raw.get("status") == "cancelled" or declined
        if skip:
            return None
        return MeetingFields(
            title=((title or "").strip() or "Untitled meeting")[:TITLE_MAX_LENGTH],
            starts_at=starts_at,
            ends_at=ends_at,
            meeting_url=(event.get("meeting_url") or "")[:1000],
            ical_uid=(event.get("ical_uid") or "")[:1024],
        )

    @staticmethod
    def _twin(user, fields: MeetingFields) -> Todo | None:
        """The same meeting already listed through another of the user's calendars."""
        if not fields.ical_uid:
            return None
        return (TodoService.open_for(user)
                .filter(kind=Todo.Kind.MEETING, starts_at=fields.starts_at, calendar_events__ical_uid=fields.ical_uid)
                .first())

    @staticmethod
    def _create(user, fields: MeetingFields) -> Todo:
        return Todo.objects.create(
            user=user, kind=Todo.Kind.MEETING, title=fields.title,
            starts_at=fields.starts_at, ends_at=fields.ends_at, meeting_url=fields.meeting_url,
        )

    @staticmethod
    def _update(meeting: Todo, fields: MeetingFields) -> None:
        meeting.title       = fields.title
        meeting.starts_at   = fields.starts_at
        meeting.ends_at     = fields.ends_at
        meeting.meeting_url = fields.meeting_url
        meeting.save(update_fields=["title", "starts_at", "ends_at", "meeting_url", "updated_at"])

    @staticmethod
    def _unlink(event: CalendarEvent) -> None:
        meeting = event.meeting
        event.delete()
        if not meeting.calendar_events.exists():
            meeting.delete()  # it left every calendar it was on; its thread goes with it

    @staticmethod
    def _status(raw: str | None) -> str:
        return raw if raw in CalendarConnection.Status.values else CalendarConnection.Status.CONNECTING
