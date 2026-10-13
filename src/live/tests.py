import asyncio
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.services import GuestService, IdentityService
from live.bus import deliver, publish, subscription
from todos.models import Todo


class BusTests(TestCase):
    async def test_an_event_reaches_that_users_stream_and_no_one_elses(self):
        async with subscription(5) as mine, subscription(6) as theirs:
            deliver("5:meetings-changed")
            self.assertEqual(await asyncio.wait_for(mine.get(), 1), ("meetings-changed", "meetings-changed"))
            await asyncio.sleep(0.05)
            self.assertTrue(theirs.empty())

    def test_publish_waits_for_the_change_to_be_saved(self):
        with patch("live.bus._send") as send:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                publish(5, "meetings-changed")
            send.assert_not_called()      # not before the change is saved…
            callbacks[0]()
            send.assert_called_once_with(5, "meetings-changed", None)  # …then at once


    async def test_an_event_can_carry_data(self):
        async with subscription(5) as mine:
            deliver('5:voice-result:{"reply":"Done: 1"}')
            self.assertEqual(await asyncio.wait_for(mine.get(), 1), ("voice-result", '{"reply":"Done: 1"}'))


class ListenerTests(TestCase):
    """The Postgres side, with a stand-in connection: LISTEN on the channel, and each NOTIFY handed to that user's streams."""

    async def test_a_notify_from_postgres_reaches_the_users_stream(self):
        from types import SimpleNamespace
        from live import bus

        executed = []

        class FakeConnection:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *exc):
                return False
            async def execute(self, sql):
                executed.append(sql)
            async def notifies(self):
                yield SimpleNamespace(payload="5:meetings-changed")
                await asyncio.Event().wait()  # then wait, like a quiet channel

        async def connect(**kwargs):
            return FakeConnection()

        db = {"ENGINE": "django.db.backends.postgresql", "NAME": "miller", "USER": "u", "PASSWORD": "p", "HOST": "db", "PORT": 5432}
        with self.settings(DATABASES={"default": db}), patch("psycopg.AsyncConnection.connect", side_effect=connect):
            bus._listener = None
            async with subscription(5) as queue:
                self.assertEqual(await asyncio.wait_for(queue.get(), 1), ("meetings-changed", "meetings-changed"))
            bus._listener.cancel()
            bus._listener = None
        self.assertEqual(executed, ["LISTEN miller_events"])


class StreamTests(TestCase):
    def setUp(self):
        self.user = IdentityService.create_user("live@x.com")
        self.guest = GuestService.create_guest()

    async def test_the_page_stream_carries_events_as_they_happen(self):
        await self.async_client.aforce_login(self.user)
        response = await self.async_client.get(reverse("live:events"))
        self.assertEqual(response["Content-Type"], "text/event-stream")
        chunks = response.streaming_content
        self.assertEqual(await anext(chunks), b"retry: 3000\n\n")
        reading = asyncio.ensure_future(anext(chunks))
        await asyncio.sleep(0.05)  # the stream is now waiting for an event
        deliver(f"{self.user.pk}:meetings-changed")
        self.assertEqual(await asyncio.wait_for(reading, 1), b"event: meetings-changed\ndata: meetings-changed\n\n")
        await chunks.aclose()

    async def test_guests_get_no_stream(self):
        await self.async_client.aforce_login(self.guest)
        self.assertEqual((await self.async_client.get(reverse("live:events"))).status_code, 204)


class ClockTests(TestCase):
    def test_ended_meetings_are_closed_every_minute_and_their_owner_told(self):
        from todos.tasks import close_ended_meetings
        user = IdentityService.create_user("clock@x.com")
        now = timezone.now()
        ended = Todo.objects.create(user=user, kind=Todo.Kind.MEETING, title="Earlier", starts_at=now - timedelta(hours=1), ends_at=now - timedelta(minutes=1))
        Todo.objects.create(user=user, kind=Todo.Kind.MEETING, title="Later", starts_at=now + timedelta(hours=1), ends_at=now + timedelta(hours=2))
        with patch("todos.tasks.publish") as publish:
            self.assertEqual(close_ended_meetings(), 1)
            self.assertEqual(close_ended_meetings(), 0)  # nothing new: no one told again
        ended.refresh_from_db()
        self.assertEqual(ended.closed_at, ended.ends_at)
        publish.assert_called_once_with(user.pk, "meetings-changed")
