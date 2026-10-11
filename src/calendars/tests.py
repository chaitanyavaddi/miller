import base64
import hashlib
import hmac
import json
import time
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.services import GuestService, IdentityService
from calendars.models import CalendarConnection, CalendarEvent
from calendars.services import NOTICE_KEY, CalendarGrant, CalendarService, RecallService
from todos.models import Todo
from todos.services import TodoService

SECRET = "whsec_" + base64.b64encode(b"recall-test-secret").decode()


def event(recall_id, start, title="Design sync", platform="google_calendar", **raw):
    return {
        "id": recall_id, "platform": platform, "is_deleted": False, "ical_uid": f"uid-{recall_id}",
        "start_time": start.isoformat(), "end_time": (start + timedelta(minutes=30)).isoformat(),
        "meeting_url": "https://meet.google.com/abc-defg-hij",
        "raw": {"summary": title, "start": {"dateTime": start.isoformat()}, **raw},
    }


def signed(body: bytes, secret=SECRET):
    message_id, stamp = "msg_1", str(int(time.time()))
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signature = base64.b64encode(hmac.new(key, f"{message_id}.{stamp}.".encode() + body, hashlib.sha256).digest()).decode()
    return {"HTTP_WEBHOOK_ID": message_id, "HTTP_WEBHOOK_TIMESTAMP": stamp, "HTTP_WEBHOOK_SIGNATURE": f"v1,{signature}"}


class MeetingSyncTests(TestCase):
    def setUp(self):
        self.user = IdentityService.create_user("a@x.com", show_meetings=True)
        self.connection = CalendarConnection.objects.create(user=self.user, provider="google", email="a@x.com", recall_id="cal-1")
        self.start = timezone.now().replace(minute=0, second=0, microsecond=0) + timedelta(days=1)

    def meetings(self):
        return list(Todo.objects.filter(kind=Todo.Kind.MEETING).values_list("title", flat=True))

    def test_an_event_becomes_a_meeting_at_its_time(self):
        CalendarService.apply(self.connection, event("e1", self.start))
        meeting = Todo.objects.get(kind=Todo.Kind.MEETING)
        self.assertEqual((meeting.title, meeting.starts_at), ("Design sync", self.start))
        self.assertEqual(meeting.meeting_url, "https://meet.google.com/abc-defg-hij")

    def test_a_moved_event_moves_its_meeting_and_keeps_its_thread(self):
        CalendarService.apply(self.connection, event("e1", self.start))
        meeting = Todo.objects.get(kind=Todo.Kind.MEETING)
        CalendarService.apply(self.connection, event("e1", self.start + timedelta(days=2), title="Moved sync"))
        meeting.refresh_from_db()
        self.assertEqual((meeting.title, meeting.starts_at), ("Moved sync", self.start + timedelta(days=2)))

    def test_deleted_cancelled_declined_and_all_day_events_are_not_listed(self):
        CalendarService.apply(self.connection, event("e1", self.start))
        CalendarService.apply(self.connection, {**event("e1", self.start), "is_deleted": True})
        CalendarService.apply(self.connection, event("e2", self.start, status="cancelled"))
        CalendarService.apply(self.connection, event("e3", self.start, attendees=[{"self": True, "responseStatus": "declined"}]))
        CalendarService.apply(self.connection, {**event("e4", self.start), "raw": {"summary": "Holiday", "start": {"date": "2026-10-12"}}})
        self.assertEqual(self.meetings(), [])

    def test_outlook_events_read_their_subject_and_flags(self):
        outlook = {**event("o1", self.start, platform="microsoft_outlook"), "raw": {"subject": "Vendor check-in"}}
        CalendarService.apply(self.connection, outlook)
        CalendarService.apply(self.connection, {**event("o2", self.start, platform="microsoft_outlook"), "raw": {"subject": "Off", "isAllDay": True}})
        self.assertEqual(self.meetings(), ["Vendor check-in"])

    def test_the_same_meeting_in_two_calendars_is_listed_once(self):
        other = CalendarConnection.objects.create(user=self.user, provider="google", email="b@x.com", recall_id="cal-2")
        CalendarService.apply(self.connection, event("e1", self.start))
        CalendarService.apply(other, {**event("e2", self.start), "ical_uid": "uid-e1"})
        self.assertEqual(self.meetings(), ["Design sync"])
        CalendarService.apply(self.connection, {**event("e1", self.start), "is_deleted": True})
        self.assertEqual(self.meetings(), ["Design sync"])  # still on the other calendar

    @patch.object(RecallService, "delete_calendar")
    def test_disconnecting_takes_the_calendars_meetings_off_the_list(self, delete_calendar):
        CalendarService.apply(self.connection, event("e1", self.start))
        CalendarService.disconnect(self.connection)
        delete_calendar.assert_called_once_with("cal-1")
        self.assertEqual((self.meetings(), CalendarEvent.objects.count()), ([], 0))

    @patch.object(RecallService, "events")
    def test_sync_applies_every_event_recall_returns(self, events):
        events.return_value = iter([event("e1", self.start), event("e2", self.start + timedelta(hours=2), title="Retro")])
        CalendarService.sync(self.connection)
        self.assertEqual(sorted(self.meetings()), ["Design sync", "Retro"])
        self.connection.refresh_from_db()
        self.assertIsNotNone(self.connection.synced_at)


@override_settings(RECALL_WEBHOOK_SECRET=SECRET)
class RecallWebhookTests(TestCase):
    def setUp(self):
        self.user = IdentityService.create_user("a@x.com", show_meetings=True)
        self.connection = CalendarConnection.objects.create(user=self.user, provider="google", email="a@x.com", recall_id="cal-1")
        self.url = reverse("calendars:webhook")

    def post(self, payload, headers=None):
        body = json.dumps(payload).encode()
        return self.client.post(self.url, body, content_type="application/json", **(headers if headers is not None else signed(body)))

    def test_an_unsigned_or_forged_webhook_is_refused(self):
        self.assertEqual(self.post({"event": "calendar.sync_events"}, headers={}).status_code, 401)
        forged = signed(b"something else")
        self.assertEqual(self.post({"event": "calendar.sync_events"}, headers=forged).status_code, 401)

    @patch.object(CalendarService, "sync")
    def test_sync_events_pulls_what_changed_since_the_stamp(self, sync):
        response = self.post({"event": "calendar.sync_events", "data": {"calendar_id": "cal-1", "last_updated_ts": "2026-10-10T08:00:00Z"}})
        self.assertEqual(response.status_code, 204)
        connection, since = sync.call_args.args
        self.assertEqual((connection, since.isoformat()), (self.connection, "2026-10-10T08:00:00+00:00"))

    @patch.object(RecallService, "calendar", return_value={"status": "disconnected"})
    def test_calendar_update_refreshes_the_connection_status(self, calendar):
        self.post({"event": "calendar.update", "data": {"calendar_id": "cal-1"}})
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.status, CalendarConnection.Status.DISCONNECTED)


@override_settings(RECALL_API_KEY="key", GOOGLE_CLIENT_ID="id", GOOGLE_CLIENT_SECRET="secret")
class ConnectFlowTests(TestCase):
    def setUp(self):
        self.user = IdentityService.create_user("a@x.com")
        self.client.force_login(self.user)

    def test_connect_sends_the_user_to_google_with_the_suggested_address(self):
        response = self.client.get(reverse("calendars:connect", args=["google"]), {"hint": "a@x.com"})
        self.assertIn("accounts.google.com", response["Location"])
        self.assertIn("calendar.events.readonly", response["Location"])
        self.assertIn("login_hint=a%40x.com", response["Location"])

    def test_guests_cannot_connect_a_calendar(self):
        self.client.force_login(GuestService.create_guest())
        response = self.client.get(reverse("calendars:connect", args=["google"]))
        self.assertEqual(response["Location"], reverse("board:index"))

    @patch.object(RecallService, "events", return_value=iter([]))
    @patch.object(RecallService, "create_calendar", return_value={"id": "cal-9", "status": "connected"})
    @patch("calendars.views.CalendarOAuthService.exchange", return_value=CalendarGrant(refresh_token="r", email="work@x.com"))
    def test_the_callback_connects_the_calendar_and_turns_meetings_on(self, exchange, create_calendar, events):
        self.client.get(reverse("calendars:connect", args=["google"]))
        state = self.client.session["calendar_oauth_state"].split(":", 1)[1]
        response = self.client.get(reverse("calendars:callback", args=["google"]), {"state": state, "code": "c"})
        self.assertEqual(response["Location"], reverse("board:index") + "?settings=1")
        self.assertEqual(self.client.session[NOTICE_KEY], "Connected work@x.com.")
        connection = CalendarConnection.objects.get()
        self.assertEqual((connection.email, connection.recall_id, connection.status), ("work@x.com", "cal-9", "connected"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.show_meetings)

    def test_a_callback_with_a_stale_state_connects_nothing(self):
        response = self.client.get(reverse("calendars:callback", args=["google"]), {"state": "nope", "code": "c"})
        self.assertEqual(self.client.session[NOTICE_KEY], "That link expired. Try connecting again.")
        self.assertEqual((response.status_code, CalendarConnection.objects.count()), (302, 0))
