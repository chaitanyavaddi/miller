import hashlib
import hmac
import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import QuerySet
from django.urls import reverse
from django.utils import timezone

from calendars.models import CalendarConnection, CalendarEvent, CalendarSource
from calendars.services.calendar_api import APIS, CalendarApiError, CalendarAuthError, MeetingFields, RemoteEvent
from calendars.services.oauth_service import CalendarGrant, CalendarOAuthService
from calendars.services.token_vault import TokenVault
from todos.models import Todo
from todos.services import TodoService

logger = logging.getLogger(__name__)

SYNC_LOOKBACK = timedelta(days=1)    # today's earlier meetings still show; older ones are never listed
SYNC_LOOKAHEAD = timedelta(days=14)  # meetings are listed on their day; two weeks is plenty of notice
RENEW_BEFORE = timedelta(days=1)     # push channels are renewed when they'd lapse within this
ACCESS_TOKEN_TTL = 50 * 60           # both providers issue hour-long access tokens


class CalendarService:
    @staticmethod
    def connections_for(user) -> QuerySet:
        return CalendarConnection.objects.filter(user=user).prefetch_related("sources")

    @staticmethod
    def can_connect(provider: str) -> bool:
        return TokenVault.is_configured() and CalendarOAuthService.is_configured(provider)

    # Connecting

    @staticmethod
    def connect(user, provider: str, grant: CalendarGrant) -> CalendarConnection:
        """Remember the account (or take the new token, if it was connected before and lost access),
        switch its primary calendar on, show meetings and pull in what's on it."""
        connection, _ = CalendarConnection.objects.update_or_create(
            user=user, provider=provider, email=grant.email,
            defaults={"refresh_token": TokenVault.seal(grant.refresh_token), "status": CalendarConnection.Status.CONNECTED},
        )
        cache.delete(CalendarService._token_key(connection))
        if not user.show_meetings:
            user.show_meetings = True
            user.save(update_fields=["show_meetings", "updated_at"])
        CalendarService.sync(connection)
        return connection

    @staticmethod
    def disconnect(connection: CalendarConnection) -> None:
        """Stop the push channels, give the token back and take the account's meetings off the list
        (a meeting another connected calendar also has stays). The provider being unreachable or the
        token already dead doesn't stop it: it's forgotten here either way."""
        api = APIS[connection.provider]
        try:
            token = CalendarService.access_token(connection)
            for source in connection.sources.exclude(channel_id=""):
                CalendarService._unwatch(token, source)
            api.revoke(TokenVault.open(connection.refresh_token))
        except CalendarApiError:
            pass
        with transaction.atomic():
            for event in CalendarEvent.objects.filter(source__connection=connection).select_related("meeting"):
                CalendarService._unlink(event)
            connection.delete()
        cache.delete(CalendarService._token_key(connection))

    # Syncing

    @staticmethod
    def access_token(connection: CalendarConnection) -> str:
        """An access token for the account, cached for most of its hour. A refused refresh token
        marks the connection disconnected, so Settings offers a reconnect."""
        key = CalendarService._token_key(connection)
        token = cache.get(key)
        if token:
            return token
        refresh_token = TokenVault.open(connection.refresh_token)
        try:
            grant = CalendarOAuthService.refresh(connection.provider, refresh_token)
        except CalendarAuthError:
            CalendarService._mark(connection, CalendarConnection.Status.DISCONNECTED)
            raise
        if grant.refresh_token != refresh_token:
            connection.refresh_token = TokenVault.seal(grant.refresh_token)
            connection.save(update_fields=["refresh_token", "updated_at"])
        cache.set(key, grant.access_token, ACCESS_TOKEN_TTL)
        return grant.access_token

    @staticmethod
    def sync(connection: CalendarConnection) -> None:
        """The whole account: its list of calendars, each enabled one's push channel, and their meetings."""
        token = CalendarService.access_token(connection)
        CalendarService.refresh_sources(connection, token)
        for source in connection.sources.filter(enabled=True):
            CalendarService._ensure_channel(token, source)
            CalendarService.sync_source(source, token)
        connection.synced_at = timezone.now()
        connection.save(update_fields=["synced_at", "updated_at"])

    @staticmethod
    def refresh_sources(connection: CalendarConnection, token: str) -> None:
        """Mirror the account's primary calendar (the only one Miller reads): it's on from the start,
        follows a rename, and if it ever changes, the old one goes with its meetings."""
        primary = next((calendar for calendar in APIS[connection.provider].calendars(token) if calendar.is_primary), None)
        for source in list(connection.sources.all()):
            if primary is not None and source.external_id == primary.external_id:
                if source.name != primary.name or not source.enabled:
                    source.name, source.is_primary, source.enabled = primary.name, True, True
                    source.save(update_fields=["name", "is_primary", "enabled", "updated_at"])
                continue
            with transaction.atomic():
                for event in source.events.select_related("meeting"):
                    CalendarService._unlink(event)
                source.delete()
        if primary is not None and not connection.sources.filter(external_id=primary.external_id).exists():
            CalendarSource.objects.create(connection=connection, external_id=primary.external_id, name=primary.name, is_primary=True, enabled=True)

    @staticmethod
    def sync_source(source: CalendarSource, token: str | None = None) -> None:
        """Re-read the calendar's window and make the list match it: new and changed events are
        applied, events no longer in it come off. Days are counted in the owner's zone, wherever this runs."""
        connection = source.connection
        token = token or CalendarService.access_token(connection)
        now = timezone.now()
        start, end = now - SYNC_LOOKBACK, now + SYNC_LOOKAHEAD
        seen = set()
        with timezone.override(connection.user.tzinfo):
            for event in APIS[connection.provider].events(token, source.external_id, start, end):
                CalendarService.apply(source, event)
                seen.add(event.external_id)
            gone = (source.events.filter(meeting__starts_at__gte=start, meeting__starts_at__lt=end)
                    .exclude(external_id__in=seen).select_related("meeting"))
            with transaction.atomic():
                for event in gone:
                    CalendarService._unlink(event)

    @staticmethod
    @transaction.atomic
    def apply(source: CalendarSource, event: RemoteEvent) -> None:
        fields = event.fields
        linked = source.events.filter(external_id=event.external_id).select_related("meeting").first()
        if fields is None:
            if linked is not None:
                CalendarService._unlink(linked)
            return
        if linked is not None:
            CalendarService._update(linked.meeting, fields)
            return
        user = source.connection.user
        meeting = CalendarService._twin(user, fields) or CalendarService._create(user, fields)
        CalendarEvent.objects.create(source=source, meeting=meeting, external_id=event.external_id, ical_uid=fields.ical_uid)

    # Push channels

    @staticmethod
    def webhook_address(provider: str) -> str:
        return settings.SITE_URL + reverse("calendars:webhook", args=[provider])

    @staticmethod
    def channel_secret(channel_id: str) -> str:
        """What a channel's pushes must carry (Google's channel token, Graph's clientState). Derived,
        not stored: only we can make it."""
        return hmac.new(settings.SECRET_KEY.encode(), f"calendar-channel:{channel_id}".encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def source_for_push(provider: str, channel_id: str = "", resource: str = "", secret: str = "") -> CalendarSource | None:
        """The enabled source a push is about, if its secret checks out."""
        found = CalendarSource.objects.filter(connection__provider=provider, enabled=True)
        found = found.filter(channel_id=channel_id) if channel_id else found.filter(channel_resource=resource)
        source = found.select_related("connection__user").first() if (channel_id or resource) else None
        if source is None or not source.channel_id:
            return None
        if not hmac.compare_digest(CalendarService.channel_secret(source.channel_id), secret or ""):
            return None
        return source

    @staticmethod
    def _ensure_channel(token: str, source: CalendarSource) -> None:
        """Open a push channel for the source, or renew one about to lapse. Providers only push to
        https; elsewhere (and if the provider refuses) the scheduled sync is what keeps it fresh."""
        if not settings.SITE_URL.startswith("https://"):
            return
        api, address = APIS[source.connection.provider], CalendarService.webhook_address(source.connection.provider)
        if source.channel_id and source.channel_expires_at and source.channel_expires_at > timezone.now() + RENEW_BEFORE:
            return
        try:
            if source.channel_id:
                try:
                    channel = api.renew(token, source, address, CalendarService.channel_secret)
                except CalendarApiError:
                    channel = api.watch(token, source, address, CalendarService.channel_secret)  # the old one is gone: start over
            else:
                channel = api.watch(token, source, address, CalendarService.channel_secret)
        except CalendarApiError as error:
            logger.warning("No push channel for %s: %s", source, error)
            return
        source.channel_id, source.channel_resource, source.channel_expires_at = channel.channel_id, channel.resource, channel.expires_at
        source.save(update_fields=["channel_id", "channel_resource", "channel_expires_at", "updated_at"])

    @staticmethod
    def _unwatch(token: str, source: CalendarSource) -> None:
        if source.channel_id:
            try:
                APIS[source.connection.provider].unwatch(token, source)
            except CalendarApiError:
                pass  # already lapsed or gone: nothing to stop
        source.channel_id, source.channel_resource, source.channel_expires_at = "", "", None
        source.save(update_fields=["channel_id", "channel_resource", "channel_expires_at", "updated_at"])

    # Meetings

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
            starts_at=fields.starts_at, ends_at=fields.ends_at, meeting_url=fields.meeting_url, event_url=fields.event_url,
        )

    @staticmethod
    def _update(meeting: Todo, fields: MeetingFields) -> None:
        meeting.title       = fields.title
        meeting.starts_at   = fields.starts_at
        meeting.ends_at     = fields.ends_at
        meeting.meeting_url = fields.meeting_url
        meeting.event_url   = fields.event_url
        meeting.save(update_fields=["title", "starts_at", "ends_at", "meeting_url", "event_url", "updated_at"])

    @staticmethod
    def _unlink(event: CalendarEvent) -> None:
        meeting = event.meeting
        event.delete()
        if not meeting.calendar_events.exists():
            meeting.delete()  # it left every calendar it was on; its thread goes with it

    @staticmethod
    def _mark(connection: CalendarConnection, status: str) -> None:
        if connection.status != status:
            connection.status = status
            connection.save(update_fields=["status", "updated_at"])

    @staticmethod
    def _token_key(connection: CalendarConnection) -> str:
        return f"calendar-access-token:{connection.pk}"
