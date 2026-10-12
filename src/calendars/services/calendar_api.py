import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from calendars.models import CalendarConnection, CalendarSource
from todos.schemas import TITLE_MAX_LENGTH

GOOGLE_API = "https://www.googleapis.com/calendar/v3"
GRAPH_API  = "https://graph.microsoft.com/v1.0"
GOOGLE_CHANNEL_TTL = timedelta(days=7)
GRAPH_SUBSCRIPTION_TTL = timedelta(minutes=4200)  # under Graph's limit for Outlook events
# Where a meeting link is looked for when the event has no conferencing field, in this order.
MEETING_LINKS = [re.compile(r"https://[^\s<>\"')]*" + host + r"[^\s<>\"')]*") for host in (
    r"zoom\.us/", r"meet\.google\.com/", r"teams\.microsoft\.com/", r"teams\.live\.com/", r"webex\.com/", r"(?:gotomeeting\.com|meet\.goto\.com)/",
)]


class CalendarApiError(Exception):
    """The provider could not be reached, or refused the request."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class CalendarAuthError(CalendarApiError):
    """The refresh token no longer works (revoked, password changed, key lost): needs a reconnect."""


@dataclass(frozen=True)
class MeetingFields:
    """What a calendar event becomes on the list."""
    title: str
    starts_at: datetime
    ends_at: datetime
    meeting_url: str
    ical_uid: str
    event_url: str = ""  # the event in the user's calendar


@dataclass(frozen=True)
class RemoteCalendar:
    external_id: str
    name: str
    is_primary: bool


@dataclass(frozen=True)
class RemoteEvent:
    """One event (one occurrence, for a recurring one). `fields` is None for anything that shouldn't
    be on the list: cancelled, declined, all-day."""
    external_id: str
    fields: MeetingFields | None


@dataclass(frozen=True)
class Channel:
    """A push channel as the provider confirmed it."""
    channel_id: str
    resource: str
    expires_at: datetime


def call(method: str, url: str, token: str = "", payload: dict | None = None, form: dict | None = None,
         headers: dict | None = None) -> dict:
    """One JSON request. A 401 means the access token was refused; anything else that fails is a
    CalendarApiError carrying the status."""
    data, sent = None, {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        data, sent["Content-Type"] = json.dumps(payload).encode(), "application/json"
    elif form is not None:
        data, sent["Content-Type"] = urllib.parse.urlencode(form).encode(), "application/x-www-form-urlencoded"
    if token:
        sent["Authorization"] = f"Bearer {token}"
    try:
        request = urllib.request.Request(url, data=data, method=method, headers=sent)
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")[:500]
        if error.code == 401:
            raise CalendarAuthError(body, error.code) from error
        raise CalendarApiError(body, error.code) from error
    except (OSError, ValueError) as error:
        raise CalendarApiError(str(error)) from error


def meeting_link(*texts: str) -> str:
    text = " ".join(t for t in texts if t)
    for pattern in MEETING_LINKS:
        found = pattern.search(text)
        if found:
            return found.group(0)
    return ""


def meeting_fields(title: str, starts_at: datetime | None, ends_at: datetime | None, meeting_url: str, ical_uid: str,
                   event_url: str = "") -> MeetingFields | None:
    if starts_at is None or ends_at is None:
        return None
    return MeetingFields(
        title=((title or "").strip() or "Untitled meeting")[:TITLE_MAX_LENGTH],
        starts_at=starts_at,
        ends_at=ends_at,
        meeting_url=(meeting_url or "")[:1000],
        ical_uid=(ical_uid or "")[:1024],
        event_url=(event_url or "")[:2000],
    )


class GoogleCalendarApi:
    @staticmethod
    def calendars(token: str, email: str) -> list[RemoteCalendar]:
        """The account's primary calendar, the only one Miller reads. Its id is the account's address, and
        the event list carries its name, so reading events is all the access this needs (no calendar list)."""
        page = call("GET", f"{GOOGLE_API}/calendars/{urllib.parse.quote(email, safe='')}/events?maxResults=1", token)
        return [RemoteCalendar(external_id=email, name=(page.get("summary") or email)[:255], is_primary=True)]

    @staticmethod
    def events(token: str, calendar_id: str, start: datetime, end: datetime) -> Iterator[RemoteEvent]:
        """Every occurrence starting in the window, recurring events expanded by Google."""
        query = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true", "maxResults": "250"}
        base = f"{GOOGLE_API}/calendars/{urllib.parse.quote(calendar_id, safe='')}/events?"
        url = base + urllib.parse.urlencode(query)
        while url:
            page = call("GET", url, token)
            for item in page.get("items", []):
                yield RemoteEvent(item["id"], GoogleCalendarApi.fields(item))
            next_page = page.get("nextPageToken")
            url = base + urllib.parse.urlencode({**query, "pageToken": next_page}) if next_page else ""

    @staticmethod
    def fields(item: dict) -> MeetingFields | None:
        start, end = item.get("start") or {}, item.get("end") or {}
        declined = any(a.get("self") and a.get("responseStatus") == "declined" for a in item.get("attendees") or [])
        if "date" in start or item.get("status") == "cancelled" or declined or item.get("eventType") == "workingLocation":
            return None
        video = next((p.get("uri", "") for p in (item.get("conferenceData") or {}).get("entryPoints") or [] if p.get("entryPointType") == "video"), "")
        return meeting_fields(
            item.get("summary", ""),
            parse_datetime(start.get("dateTime") or ""),
            parse_datetime(end.get("dateTime") or ""),
            item.get("hangoutLink") or video or meeting_link(item.get("location", ""), item.get("description", "")),
            item.get("iCalUID", ""),
            item.get("htmlLink", ""),
        )

    @staticmethod
    def watch(token: str, source: CalendarSource, address: str, secret_for) -> Channel:
        channel_id = uuid.uuid4().hex
        made = call("POST", f"{GOOGLE_API}/calendars/{urllib.parse.quote(source.external_id, safe='')}/events/watch", token, {
            "id": channel_id, "type": "web_hook", "address": address, "token": secret_for(channel_id),
            "params": {"ttl": str(int(GOOGLE_CHANNEL_TTL.total_seconds()))},
        })
        expires_at = datetime.fromtimestamp(int(made.get("expiration") or 0) / 1000, tz=UTC)
        return Channel(channel_id, made.get("resourceId", ""), expires_at)

    @staticmethod
    def renew(token: str, source: CalendarSource, address: str, secret_for) -> Channel:
        """Google channels can't be extended: open a new one, then close the old."""
        channel = GoogleCalendarApi.watch(token, source, address, secret_for)
        GoogleCalendarApi.unwatch(token, source)
        return channel

    @staticmethod
    def unwatch(token: str, source: CalendarSource) -> None:
        call("POST", f"{GOOGLE_API}/channels/stop", token, {"id": source.channel_id, "resourceId": source.channel_resource})

    @staticmethod
    def revoke(refresh_token: str) -> None:
        call("POST", "https://oauth2.googleapis.com/revoke", form={"token": refresh_token})


class MicrosoftCalendarApi:
    @staticmethod
    def calendars(token: str, email: str) -> list[RemoteCalendar]:
        found, url = [], f"{GRAPH_API}/me/calendars?$select=id,name,isDefaultCalendar&$top=100"
        while url:
            page = call("GET", url, token)
            for item in page.get("value", []):
                found.append(RemoteCalendar(item["id"], (item.get("name") or "Calendar")[:255], bool(item.get("isDefaultCalendar"))))
            url = page.get("@odata.nextLink", "")
        return found

    @staticmethod
    def events(token: str, calendar_id: str, start: datetime, end: datetime) -> Iterator[RemoteEvent]:
        """Every occurrence in the window (calendarView expands recurring events), times in UTC."""
        query = {
            "startDateTime": start.astimezone(UTC).isoformat(), "endDateTime": end.astimezone(UTC).isoformat(), "$top": "100",
            "$select": "id,iCalUId,subject,start,end,isAllDay,isCancelled,responseStatus,onlineMeeting,onlineMeetingUrl,location,bodyPreview,webLink",
        }
        url = f"{GRAPH_API}/me/calendars/{urllib.parse.quote(calendar_id, safe='')}/calendarView?" + urllib.parse.urlencode(query)
        while url:
            page = call("GET", url, token, headers={"Prefer": 'outlook.timezone="UTC"'})
            for item in page.get("value", []):
                yield RemoteEvent(item["id"], MicrosoftCalendarApi.fields(item))
            url = page.get("@odata.nextLink", "")

    @staticmethod
    def fields(item: dict) -> MeetingFields | None:
        if item.get("isAllDay") or item.get("isCancelled") or (item.get("responseStatus") or {}).get("response") == "declined":
            return None
        return meeting_fields(
            item.get("subject", ""),
            MicrosoftCalendarApi._utc((item.get("start") or {}).get("dateTime")),
            MicrosoftCalendarApi._utc((item.get("end") or {}).get("dateTime")),
            (item.get("onlineMeeting") or {}).get("joinUrl") or item.get("onlineMeetingUrl")
            or meeting_link((item.get("location") or {}).get("displayName", ""), item.get("bodyPreview", "")),
            item.get("iCalUId", ""),
            item.get("webLink", ""),
        )

    @staticmethod
    def watch(token: str, source: CalendarSource, address: str, secret_for) -> Channel:
        channel_id = uuid.uuid4().hex
        made = call("POST", f"{GRAPH_API}/subscriptions", token, {
            "changeType": "created,updated,deleted", "notificationUrl": address,
            "resource": f"me/calendars/{source.external_id}/events",
            "expirationDateTime": (timezone.now() + GRAPH_SUBSCRIPTION_TTL).isoformat(), "clientState": secret_for(channel_id),
        })
        return Channel(channel_id, made.get("id", ""), parse_datetime(made.get("expirationDateTime") or "") or timezone.now())

    @staticmethod
    def renew(token: str, source: CalendarSource, address: str, secret_for) -> Channel:
        made = call("PATCH", f"{GRAPH_API}/subscriptions/{source.channel_resource}", token, {
            "expirationDateTime": (timezone.now() + GRAPH_SUBSCRIPTION_TTL).isoformat(),
        })
        return Channel(source.channel_id, source.channel_resource, parse_datetime(made.get("expirationDateTime") or "") or timezone.now())

    @staticmethod
    def unwatch(token: str, source: CalendarSource) -> None:
        call("DELETE", f"{GRAPH_API}/subscriptions/{source.channel_resource}", token)

    @staticmethod
    def revoke(refresh_token: str) -> None:
        """Graph has no per-token revoke; the token just stops being used (the user can remove the
        app at myaccount.microsoft.com)."""

    @staticmethod
    def _utc(raw: str | None) -> datetime | None:
        parsed = parse_datetime(raw or "")
        if parsed is None:
            return None
        return parsed if timezone.is_aware(parsed) else parsed.replace(tzinfo=UTC)


APIS = {
    CalendarConnection.Provider.GOOGLE:    GoogleCalendarApi,
    CalendarConnection.Provider.MICROSOFT: MicrosoftCalendarApi,
}
