import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.services import GuestService, IdentityService
from calendars.models import CalendarConnection, CalendarEvent, CalendarSource
from calendars.services import (
    NOTICE_KEY, CalendarApiError, CalendarAuthError, CalendarGrant, CalendarOAuthService, CalendarService,
    GoogleCalendarApi, MicrosoftCalendarApi, RemoteEvent, TokenVault,
)
from calendars.services.calendar_api import Channel, RemoteCalendar
from calendars.services.oauth_service import AccessGrant
from todos.models import Todo

KEY = Fernet.generate_key().decode()
GOOGLE = "calendars.services.calendar_service.APIS"


def google_item(event_id, start, title="Design sync", **extra):
    return {
        "id": event_id, "iCalUID": f"uid-{event_id}", "summary": title, "status": "confirmed",
        "start": {"dateTime": start.isoformat()}, "end": {"dateTime": (start + timedelta(minutes=30)).isoformat()},
        "hangoutLink": "https://meet.google.com/abc-defg-hij", "htmlLink": f"https://www.google.com/calendar/event?eid={event_id}", **extra,
    }


def remote(event_id, start, title="Design sync", **extra):
    return RemoteEvent(event_id, GoogleCalendarApi.fields(google_item(event_id, start, title, **extra)))


class FakeApi:
    """Stands in for GoogleCalendarApi/MicrosoftCalendarApi: canned calendars and events, recorded channel calls."""

    def __init__(self, calendars=None, events=None):
        self.calendars_list = calendars if calendars is not None else [RemoteCalendar("primary-id", "Work", True)]
        self.events_by_calendar = events or {}
        self.watched, self.unwatched, self.revoked = [], [], []

    def calendars(self, token, email):
        return self.calendars_list

    def events(self, token, calendar_id, start, end):
        return iter(self.events_by_calendar.get(calendar_id, []))

    def watch(self, token, source, address, secret_for):
        self.watched.append(source.external_id)
        return Channel(f"ch-{len(self.watched)}", f"res-{len(self.watched)}", timezone.now() + timedelta(days=7))

    def renew(self, token, source, address, secret_for):
        return self.watch(token, source, address, secret_for)

    def unwatch(self, token, source):
        self.unwatched.append(source.channel_id)

    def revoke(self, refresh_token):
        self.revoked.append(refresh_token)


@override_settings(CALENDAR_TOKEN_KEY=KEY, GOOGLE_CLIENT_ID="id", GOOGLE_CLIENT_SECRET="secret")
class CalendarTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.user = IdentityService.create_user("a@x.com", show_meetings=True)
        self.connection = CalendarConnection.objects.create(user=self.user, provider="google", email="a@x.com", refresh_token=TokenVault.seal("r1"))
        self.source = CalendarSource.objects.create(connection=self.connection, external_id="primary-id", name="Work", is_primary=True, enabled=True)
        self.start = timezone.now().replace(minute=0, second=0, microsecond=0) + timedelta(days=1)
        patcher = patch.object(CalendarOAuthService, "refresh", side_effect=lambda provider, token: AccessGrant("access", token))
        self.refresh = patcher.start()
        self.addCleanup(patcher.stop)

    def meetings(self):
        return sorted(Todo.objects.filter(kind=Todo.Kind.MEETING).values_list("title", flat=True))


class EventParsingTests(TestCase):
    def setUp(self):
        self.start = timezone.now().replace(minute=0, second=0, microsecond=0)

    def test_a_google_event_reads_its_title_time_link_and_uid(self):
        fields = GoogleCalendarApi.fields(google_item("e1", self.start))
        self.assertEqual((fields.title, fields.starts_at, fields.ical_uid), ("Design sync", self.start, "uid-e1"))
        self.assertEqual(fields.meeting_url, "https://meet.google.com/abc-defg-hij")
        self.assertEqual(fields.event_url, "https://www.google.com/calendar/event?eid=e1")

    def test_google_cancelled_declined_all_day_and_working_location_events_are_skipped(self):
        self.assertIsNone(GoogleCalendarApi.fields(google_item("e", self.start, status="cancelled")))
        self.assertIsNone(GoogleCalendarApi.fields(google_item("e", self.start, attendees=[{"self": True, "responseStatus": "declined"}])))
        self.assertIsNone(GoogleCalendarApi.fields({**google_item("e", self.start), "start": {"date": "2026-10-12"}}))
        self.assertIsNone(GoogleCalendarApi.fields(google_item("e", self.start, eventType="workingLocation")))

    def test_a_meeting_link_is_found_in_the_description_when_there_is_no_conferencing(self):
        item = {**google_item("e", self.start), "hangoutLink": "", "description": "Join: https://acme.zoom.us/j/123?pwd=x thanks"}
        self.assertEqual(GoogleCalendarApi.fields(item).meeting_url, "https://acme.zoom.us/j/123?pwd=x")

    @patch("calendars.services.calendar_api.call", return_value={"summary": "a@x.com", "items": []})
    def test_googles_primary_calendar_is_read_by_the_accounts_address(self, call):
        calendars = GoogleCalendarApi.calendars("token", "a@x.com")
        self.assertEqual([(c.external_id, c.name, c.is_primary) for c in calendars], [("a@x.com", "a@x.com", True)])
        self.assertIn("/calendars/a%40x.com/events", call.call_args.args[1])

    def test_an_outlook_event_reads_its_utc_times_subject_and_teams_link(self):
        item = {
            "id": "o1", "iCalUId": "uid-o1", "subject": "Vendor check-in", "isAllDay": False, "isCancelled": False,
            "start": {"dateTime": "2026-10-12T09:00:00.0000000", "timeZone": "UTC"},
            "end": {"dateTime": "2026-10-12T09:30:00.0000000", "timeZone": "UTC"},
            "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/1"},
            "webLink": "https://outlook.office365.com/owa/?itemid=o1",
        }
        fields = MicrosoftCalendarApi.fields(item)
        self.assertEqual(fields.title, "Vendor check-in")
        self.assertEqual(fields.starts_at, datetime(2026, 10, 12, 9, 0, tzinfo=UTC))
        self.assertEqual(fields.meeting_url, "https://teams.microsoft.com/l/meetup-join/1")
        self.assertEqual(fields.event_url, "https://outlook.office365.com/owa/?itemid=o1")
        self.assertIsNone(MicrosoftCalendarApi.fields({**item, "isAllDay": True}))
        self.assertIsNone(MicrosoftCalendarApi.fields({**item, "responseStatus": {"response": "declined"}}))

    def test_only_meetings_come_in_not_blocks_on_the_calendar(self):
        block = {**google_item("e", self.start, "Deep work"), "hangoutLink": ""}   # nobody else, no call
        for kind in ("focusTime", "outOfOffice", "workingLocation", "birthday", "fromGmail"):
            self.assertIsNone(GoogleCalendarApi.fields(google_item("e", self.start, eventType=kind)), kind)
        self.assertIsNone(GoogleCalendarApi.fields(block))
        self.assertIsNone(GoogleCalendarApi.fields({**block, "attendees": [{"self": True, "responseStatus": "accepted"}, {"email": "room@x.com", "resource": True}]}))
        self.assertIsNotNone(GoogleCalendarApi.fields({**block, "attendees": [{"self": True}, {"email": "priya@x.com"}]}))   # someone else: a meeting
        self.assertIsNotNone(GoogleCalendarApi.fields(google_item("e", self.start)))                                       # a Meet link: a meeting

    def test_google_links_open_in_the_calendars_own_account(self):
        item = google_item("e", self.start)
        fields = GoogleCalendarApi.fields(item, account="me@work.com")
        self.assertEqual(fields.meeting_url, "https://meet.google.com/abc-defg-hij?authuser=me%40work.com")
        self.assertEqual(fields.event_url, "https://www.google.com/calendar/event?eid=e&authuser=me%40work.com")
        zoom = GoogleCalendarApi.fields({**item, "hangoutLink": "", "location": "https://acme.zoom.us/j/123"}, account="me@work.com")
        self.assertEqual(zoom.meeting_url, "https://acme.zoom.us/j/123")   # not Google's: left alone
        self.assertEqual(GoogleCalendarApi.fields(item).meeting_url, "https://meet.google.com/abc-defg-hij")

    def test_outlook_blocks_stay_out_too(self):
        item = {"id": "o1", "subject": "Lunch", "isAllDay": False, "isCancelled": False,
                "start": {"dateTime": "2026-10-12T09:00:00"}, "end": {"dateTime": "2026-10-12T09:30:00"}}
        self.assertIsNone(MicrosoftCalendarApi.fields(item))
        self.assertIsNone(MicrosoftCalendarApi.fields({**item, "attendees": [{"type": "required"}], "showAs": "oof"}))
        self.assertIsNotNone(MicrosoftCalendarApi.fields({**item, "attendees": [{"type": "required", "emailAddress": {"address": "a@x.com"}}]}))


class MeetingSyncTests(CalendarTestCase):
    def test_an_event_becomes_a_meeting_at_its_time(self):
        CalendarService.apply(self.source, remote("e1", self.start))
        meeting = Todo.objects.get(kind=Todo.Kind.MEETING)
        self.assertEqual((meeting.title, meeting.starts_at), ("Design sync", self.start))
        self.assertEqual(meeting.event_url, "https://www.google.com/calendar/event?eid=e1")

    def test_a_moved_event_moves_its_meeting_and_keeps_its_thread(self):
        CalendarService.apply(self.source, remote("e1", self.start))
        meeting = Todo.objects.get(kind=Todo.Kind.MEETING)
        CalendarService.apply(self.source, remote("e1", self.start + timedelta(days=2), title="Moved sync"))
        meeting.refresh_from_db()
        self.assertEqual((meeting.title, meeting.starts_at), ("Moved sync", self.start + timedelta(days=2)))

    def test_a_cancelled_event_comes_off_the_list(self):
        CalendarService.apply(self.source, remote("e1", self.start))
        CalendarService.apply(self.source, remote("e1", self.start, status="cancelled"))
        self.assertEqual(self.meetings(), [])

    def test_the_same_meeting_in_two_calendars_is_listed_once(self):
        account = CalendarConnection.objects.create(user=self.user, provider="google", email="b@x.com", refresh_token="x")
        other = CalendarSource.objects.create(connection=account, external_id="b-primary", name="b@x.com", is_primary=True, enabled=True)
        CalendarService.apply(self.source, remote("e1", self.start))
        CalendarService.apply(other, RemoteEvent("e2", GoogleCalendarApi.fields({**google_item("e2", self.start), "iCalUID": "uid-e1"})))
        self.assertEqual(self.meetings(), ["Design sync"])
        CalendarService.apply(self.source, remote("e1", self.start, status="cancelled"))
        self.assertEqual(self.meetings(), ["Design sync"])  # still on the other calendar

    def test_sync_applies_the_window_and_drops_events_no_longer_in_it(self):
        api = FakeApi(events={"primary-id": [remote("e1", self.start), remote("e2", self.start + timedelta(hours=2), title="Retro")]})
        with patch.dict(GOOGLE, {"google": api}):
            CalendarService.sync_source(self.source)
            self.assertEqual(self.meetings(), ["Design sync", "Retro"])
            api.events_by_calendar["primary-id"] = [remote("e2", self.start + timedelta(hours=2), title="Retro")]
            CalendarService.sync_source(self.source)
        self.assertEqual(self.meetings(), ["Retro"])

    def test_only_the_primary_calendar_is_read(self):
        api = FakeApi(
            calendars=[RemoteCalendar("primary-id", "Work", True), RemoteCalendar("family", "Family", False)],
            events={"primary-id": [remote("e1", self.start)], "family": [remote("f1", self.start, title="Dentist")]},
        )
        with patch.dict(GOOGLE, {"google": api}):
            CalendarService.sync(self.connection)
        self.assertEqual(self.meetings(), ["Design sync"])
        self.assertFalse(CalendarSource.objects.filter(external_id="family").exists())  # secondary calendars are never added
        self.connection.refresh_from_db()
        self.assertIsNotNone(self.connection.synced_at)

    def test_a_calendar_removed_from_the_account_takes_its_meetings_with_it(self):
        CalendarService.apply(self.source, remote("e1", self.start))
        with patch.dict(GOOGLE, {"google": FakeApi(calendars=[RemoteCalendar("other", "Other", True)])}):
            CalendarService.refresh_sources(self.connection, "access")
        self.assertEqual((self.meetings(), list(self.connection.sources.values_list("name", flat=True))), ([], ["Other"]))

    def test_disconnecting_stops_channels_revokes_and_takes_meetings_off(self):
        self.source.channel_id, self.source.channel_resource = "ch-1", "res-1"
        self.source.save()
        CalendarService.apply(self.source, remote("e1", self.start))
        api = FakeApi()
        with patch.dict(GOOGLE, {"google": api}):
            CalendarService.disconnect(self.connection)
        self.assertEqual((api.unwatched, api.revoked), (["ch-1"], ["r1"]))
        self.assertEqual((self.meetings(), CalendarEvent.objects.count(), CalendarConnection.objects.count()), ([], 0, 0))

    def test_disconnecting_works_even_when_the_token_is_dead(self):
        self.refresh.side_effect = CalendarAuthError("invalid_grant", 400)
        CalendarService.disconnect(self.connection)
        self.assertFalse(CalendarConnection.objects.exists())


class ConnectionTests(CalendarTestCase):
    def test_tokens_are_stored_encrypted(self):
        sealed = TokenVault.seal("1//refresh-token-from-google")
        self.assertNotIn("refresh-token", sealed)
        self.assertEqual(TokenVault.open(sealed), "1//refresh-token-from-google")

    def test_connecting_switches_the_primary_calendar_on_and_turns_meetings_on(self):
        user = IdentityService.create_user("b@x.com")
        api = FakeApi(calendars=[RemoteCalendar("p", "Me", True), RemoteCalendar("s", "Team", False)], events={"p": [remote("e1", self.start)]})
        with patch.dict(GOOGLE, {"google": api}):
            connection = CalendarService.connect(user, "google", CalendarGrant(refresh_token="new", email="b@x.com"))
        self.assertEqual(list(connection.sources.filter(enabled=True).values_list("name", flat=True)), ["Me"])
        self.assertEqual(Todo.objects.filter(user=user, kind=Todo.Kind.MEETING).count(), 1)
        user.refresh_from_db()
        self.assertTrue(user.show_meetings)

    def test_reconnecting_a_lost_account_takes_the_new_token(self):
        self.connection.status = CalendarConnection.Status.DISCONNECTED
        self.connection.save()
        with patch.dict(GOOGLE, {"google": FakeApi()}):
            connection = CalendarService.connect(self.user, "google", CalendarGrant(refresh_token="r2", email="a@x.com"))
        self.assertEqual(connection.pk, self.connection.pk)
        self.assertEqual((connection.status, TokenVault.open(connection.refresh_token)), ("connected", "r2"))

    def test_a_refused_refresh_token_marks_the_account_disconnected(self):
        self.refresh.side_effect = CalendarAuthError("invalid_grant", 400)
        with self.assertRaises(CalendarAuthError):
            CalendarService.access_token(self.connection)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.status, CalendarConnection.Status.DISCONNECTED)

    def test_a_rotated_refresh_token_is_kept_and_access_tokens_are_cached(self):
        self.refresh.side_effect = None
        self.refresh.return_value = AccessGrant("access", "r-rotated")
        CalendarService.access_token(self.connection)
        CalendarService.access_token(self.connection)
        self.connection.refresh_from_db()
        self.assertEqual((self.refresh.call_count, TokenVault.open(self.connection.refresh_token)), (1, "r-rotated"))

    @override_settings(SITE_URL="https://miller.test")
    def test_push_channels_open_and_are_renewed_only_when_about_to_lapse(self):
        api = FakeApi()
        with patch.dict(GOOGLE, {"google": api}):
            CalendarService.sync(self.connection)
            CalendarService.sync(self.connection)
            self.assertEqual(api.watched, ["primary-id"])
            CalendarSource.objects.filter(pk=self.source.pk).update(channel_expires_at=timezone.now() + timedelta(hours=2))
            CalendarService.sync(self.connection)
        self.assertEqual(len(api.watched), 2)

    def test_without_https_no_channel_is_opened(self):
        api = FakeApi()
        with patch.dict(GOOGLE, {"google": api}):
            CalendarService.sync(self.connection)
        self.assertEqual(api.watched, [])

    def test_the_scheduled_sync_counts_accounts_that_lost_access(self):
        self.refresh.side_effect = CalendarAuthError("invalid_grant", 400)
        out = StringIO()
        call_command("sync_calendars", stdout=out)
        self.assertIn("1 need reconnecting", out.getvalue())


class WebhookTests(CalendarTestCase):
    def setUp(self):
        super().setUp()
        self.source.channel_id, self.source.channel_resource = "ch-1", "sub-1"
        self.source.save()
        self.secret = CalendarService.channel_secret("ch-1")

    def google_push(self, token, state="exists"):
        return self.client.post(reverse("calendars:webhook", args=["google"]), HTTP_X_GOOG_CHANNEL_ID="ch-1",
                                HTTP_X_GOOG_CHANNEL_TOKEN=token, HTTP_X_GOOG_RESOURCE_STATE=state)

    @patch.object(CalendarService, "sync_source", return_value=1)
    def test_a_google_push_with_the_channel_secret_rereads_that_calendar_at_once(self, sync_source):
        self.assertEqual(self.google_push(self.secret).status_code, 200)
        self.assertEqual(sync_source.call_args.args[0], self.source)

    @patch("django_q.tasks.async_task")
    @patch.object(CalendarService, "sync_source", side_effect=CalendarApiError("down"))
    def test_if_the_provider_stumbles_a_job_tries_again(self, sync_source, async_task):
        self.assertEqual(self.google_push(self.secret).status_code, 200)
        self.assertEqual(async_task.call_args.args, ("calendars.tasks.sync_source", self.source.pk))

    @patch.object(CalendarService, "sync_source")
    def test_a_forged_google_push_or_a_channel_hello_does_nothing(self, sync_source):
        self.assertEqual(self.google_push("forged").status_code, 200)
        self.assertEqual(self.google_push(self.secret, state="sync").status_code, 200)
        sync_source.assert_not_called()

    def test_graph_validation_echoes_the_token(self):
        response = self.client.post(reverse("calendars:webhook", args=["microsoft"]) + "?validationToken=abc%20123")
        self.assertEqual((response.status_code, response.content, response["Content-Type"]), (200, b"abc 123", "text/plain"))

    @patch.object(CalendarService, "sync_source", return_value=0)
    def test_graph_notifications_resync_only_calendars_with_a_matching_client_state(self, sync_source):
        CalendarConnection.objects.filter(pk=self.connection.pk).update(provider="microsoft")
        body = {"value": [
            {"subscriptionId": "sub-1", "clientState": self.secret},
            {"subscriptionId": "sub-1", "clientState": self.secret},
            {"subscriptionId": "sub-1", "clientState": "forged"},
        ]}
        response = self.client.post(reverse("calendars:webhook", args=["microsoft"]), json.dumps(body), content_type="application/json")
        self.assertEqual((response.status_code, sync_source.call_count), (202, 1))

    def test_a_change_tells_the_owners_open_pages_and_an_unchanged_reread_says_nothing(self):
        api = FakeApi(events={"primary-id": [remote("e1", self.start, title="New invite")]})
        with patch.dict(GOOGLE, {"google": api}), patch("calendars.services.calendar_service.publish") as publish:
            self.assertEqual(CalendarService.sync_source(self.source), 1)
            self.assertEqual(CalendarService.sync_source(self.source), 0)  # same calendar again: nothing to say
        self.assertEqual(publish.call_args_list, [((self.user.pk, "meetings-changed"),)])

    def test_the_queued_job_rereads_the_calendar_into_the_database(self):
        from calendars.tasks import sync_source
        api = FakeApi(events={"primary-id": [remote("e1", self.start, title="New invite")]})
        with patch.dict(GOOGLE, {"google": api}):
            self.assertTrue(sync_source(self.source.pk))
        self.assertEqual(self.meetings(), ["New invite"])

    @patch("calendars.tasks.time.sleep")
    def test_the_job_tries_again_when_the_provider_stumbles(self, sleep):
        from calendars.tasks import sync_source
        with patch.object(CalendarService, "sync_source", side_effect=[CalendarApiError("down"), None]) as resync:
            self.assertTrue(sync_source(self.source.pk))
        self.assertEqual(resync.call_count, 2)


@override_settings(CALENDAR_TOKEN_KEY=KEY, GOOGLE_CLIENT_ID="id", GOOGLE_CLIENT_SECRET="secret")
class ConnectFlowTests(TestCase):
    def setUp(self):
        self.user = IdentityService.create_user("a@x.com")
        self.client.force_login(self.user)

    def callback(self):
        self.client.get(reverse("calendars:connect", args=["google"]))
        state = self.client.session["calendar_oauth_state"].split(":", 1)[1]
        return self.client.get(reverse("calendars:callback", args=["google"]), {"state": state, "code": "c"})

    def test_connect_sends_the_user_to_google_asking_for_calendar_access(self):
        response = self.client.get(reverse("calendars:connect", args=["google"]), {"hint": "a@x.com"})
        self.assertIn("accounts.google.com", response["Location"])
        self.assertIn("calendar.events.readonly", response["Location"])
        self.assertNotIn("calendarlist", response["Location"])  # reading events is all Miller asks for
        self.assertIn("login_hint=a%40x.com", response["Location"])

    @override_settings(CALENDAR_TOKEN_KEY="")
    def test_without_a_token_key_calendars_cant_be_connected(self):
        response = self.client.get(reverse("calendars:connect", args=["google"]))
        self.assertEqual(self.client.session[NOTICE_KEY], "Calendar connections aren't set up on this server yet.")
        self.assertEqual(response.status_code, 302)

    def test_guests_cannot_connect_a_calendar(self):
        self.client.force_login(GuestService.create_guest())
        response = self.client.get(reverse("calendars:connect", args=["google"]))
        self.assertEqual(response["Location"], reverse("board:index"))

    @patch.object(CalendarService, "sync")
    @patch("calendars.views.CalendarOAuthService.exchange", return_value=CalendarGrant(refresh_token="r", email="work@x.com"))
    def test_the_callback_connects_the_calendar(self, exchange, sync):
        response = self.callback()
        self.assertEqual(response["Location"], reverse("board:index") + "?settings=1")
        self.assertEqual(self.client.session[NOTICE_KEY], "Connected work@x.com.")
        self.assertEqual(CalendarConnection.objects.get().email, "work@x.com")

    @patch("calendars.services.oauth_service.call", return_value={
        "refresh_token": "r", "scope": "openid email",
        "id_token": "x.eyJlbWFpbCI6ICJ3b3JrQHguY29tIn0.y",
    })
    def test_unticking_calendar_access_on_the_consent_screen_connects_nothing(self, call):
        self.callback()
        self.assertEqual(self.client.session[NOTICE_KEY], "The calendar didn't grant access. Try again.")
        self.assertFalse(CalendarConnection.objects.exists())

    def test_a_callback_with_a_stale_state_connects_nothing(self):
        response = self.client.get(reverse("calendars:callback", args=["google"]), {"state": "nope", "code": "c"})
        self.assertEqual(self.client.session[NOTICE_KEY], "That link expired. Try connecting again.")
        self.assertEqual((response.status_code, CalendarConnection.objects.count()), (302, 0))
