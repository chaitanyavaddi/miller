import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.services import GuestService, IdentityService
from recordings.models import Recording, RecordingPart
from recordings.services import GroqService, RecordingService, TranscriptionError
from todos.models import Todo

MEDIA = Path(tempfile.mkdtemp())


@override_settings(MEDIA_ROOT=MEDIA, GROQ_API_KEY="key")
class RecordingTestCase(TestCase):
    def setUp(self):
        self.user = IdentityService.create_user("r@x.com", show_meetings=True, timezone="UTC")
        self.client.force_login(self.user)
        start = timezone.now() - timedelta(minutes=1)  # on now: its call can be recorded
        self.meeting = Todo.objects.create(user=self.user, kind=Todo.Kind.MEETING, title="Design sync", starts_at=start, ends_at=start + timedelta(hours=1))
        self.started_ms = int(timezone.now().timestamp() * 1000)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def start(self, todo=None):
        return self.client.post(reverse("recordings:start", args=[(todo or self.meeting).id])).json()

    def chunk(self, recording_id, track="mic", part=0, seq=0, data=b"audio", started=None):
        url = reverse("recordings:chunk", args=[recording_id]) + f"?track={track}&part={part}&seq={seq}&started={started or self.started_ms}"
        return self.client.post(url, data, content_type="application/octet-stream")

    def notes(self):
        return list(self.meeting.comments.filter(automatic=True).values_list("body", flat=True))


class UploadTests(RecordingTestCase):
    def test_chunks_are_appended_in_order_and_retries_are_ignored(self):
        recording = self.start()
        self.assertEqual(self.chunk(recording["id"], seq=0, data=b"one-").json(), {"result": "ok", "next": 1})
        self.assertEqual(self.chunk(recording["id"], seq=0, data=b"one-").json()["result"], "duplicate")  # resent after a drop
        self.assertEqual(self.chunk(recording["id"], seq=1, data=b"two").json()["next"], 2)
        part = RecordingPart.objects.get()
        self.assertEqual(part.path.read_bytes(), b"one-two")

    def test_a_gap_is_refused_with_the_chunk_it_expects(self):
        recording = self.start()
        response = self.chunk(recording["id"], seq=3)
        self.assertEqual((response.status_code, response.json()["next"]), (409, 0))

    def test_resuming_continues_the_same_recording_in_new_parts(self):
        first = self.start()
        self.chunk(first["id"], track="mic", part=0)
        self.chunk(first["id"], track="call", part=0)
        again = self.start()
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(again["next_parts"], {"mic": 1, "call": 1})

    def test_a_stopped_recording_takes_no_more_chunks(self):
        recording = self.start()
        self.client.post(reverse("recordings:stop", args=[recording["id"]]))
        self.assertEqual(self.chunk(recording["id"]).status_code, 410)
        self.assertEqual(Recording.objects.get().status, Recording.Status.PROCESSING)

    def test_only_its_owner_can_send_chunks(self):
        recording = self.start()
        self.client.force_login(IdentityService.create_user("o@x.com"))
        self.assertEqual(self.chunk(recording["id"]).status_code, 403)

    def test_guests_cannot_record(self):
        guest = GuestService.create_guest()
        todo = Todo.objects.create(user=guest, title="chat")
        self.client.force_login(guest)
        self.assertEqual(self.client.post(reverse("recordings:start", args=[todo.id])).status_code, 403)

    def test_a_recording_gone_quiet_is_wrapped_up_as_interrupted(self):
        recording = self.start()
        Recording.objects.filter(pk=recording["id"]).update(last_chunk_at=timezone.now() - timedelta(hours=1))
        RecordingService.wrap_up_quiet()
        self.assertTrue(Recording.objects.filter(pk=recording["id"], status=Recording.Status.PROCESSING, interrupted=True).exists())


class TranscriptionTests(RecordingTestCase):
    def record_both_tracks(self):
        recording = self.start()
        self.chunk(recording["id"], track="mic", data=b"mic")
        self.chunk(recording["id"], track="call", data=b"call", started=self.started_ms + 2000)  # the tab share began 2s later
        self.client.post(reverse("recordings:stop", args=[recording["id"]]))
        return Recording.objects.get(pk=recording["id"])

    def fake_groq(self, path):
        if path.name.startswith("mic"):
            return [{"start": 0.0, "end": 3.0, "text": "Can you send the plan?"}, {"start": 9.0, "end": 12.0, "text": "Great, thanks."}]
        return [{"start": 2.0, "end": 5.0, "text": "Sure, by Friday."}, {"start": 5.5, "end": 6.5, "text": "With the numbers."}]

    def test_both_tracks_merge_into_one_transcript_by_clock_time(self):
        recording = self.record_both_tracks()
        with patch.object(GroqService, "transcribe", side_effect=self.fake_groq), self.captureOnCommitCallbacks(execute=True):
            RecordingService.process_due()
        recording.refresh_from_db()
        self.assertEqual(recording.status, Recording.Status.READY)
        self.assertEqual(recording.transcript, [
            {"at": 0, "speaker": "You", "text": "Can you send the plan?"},
            {"at": 4, "speaker": "Others", "text": "Sure, by Friday. With the numbers."},  # 2s offset, one speaker's run read as one line
            {"at": 9, "speaker": "You", "text": "Great, thanks."},
        ])
        self.assertEqual(self.notes(), ["Recording · 1 min"])
        self.assertFalse(RecordingPart.objects.first().path.parent.exists())  # only the words are kept

    def test_groqs_rate_limit_waits_for_a_later_run(self):
        recording = self.record_both_tracks()
        with patch.object(GroqService, "transcribe", side_effect=TranscriptionError("slow down", retry_later=True)):
            RecordingService.process_due()
        recording.refresh_from_db()
        self.assertEqual((recording.status, recording.attempts, recording.claimed_at), (Recording.Status.PROCESSING, 0, None))

    def test_after_three_failures_the_thread_says_it_couldnt_be_transcribed(self):
        recording = self.record_both_tracks()
        with patch.object(GroqService, "transcribe", side_effect=TranscriptionError("bad audio")):
            for _ in range(3):
                RecordingService.process_due()
        recording.refresh_from_db()
        self.assertEqual(recording.status, Recording.Status.FAILED)
        self.assertEqual(self.notes(), ["Recording couldn't be transcribed"])

    def test_an_interrupted_recording_says_so(self):
        recording = self.record_both_tracks()
        Recording.objects.filter(pk=recording.pk).update(interrupted=True)
        with patch.object(GroqService, "transcribe", side_effect=self.fake_groq):
            RecordingService.process_due()
        self.assertEqual(self.notes(), ["Recording · 1 min · stopped when the tab closed"])


@override_settings(MEDIA_ROOT=MEDIA, GROQ_API_KEY="key")
class ThreadTests(RecordingTestCase):
    def thread(self, todo=None):
        return self.client.get(f"{reverse('board:index')}?t={(todo or self.meeting).id}")

    def task(self):
        from todos.schemas import TodoTitleSchema
        from todos.services import TodoService
        return TodoService.create(self.user, TodoTitleSchema(title="write the plan"))

    def test_a_todos_update_box_has_no_mic_of_its_own_the_one_voice_button_covers_it(self):
        page = self.thread(self.task())
        self.assertNotContains(page, "data-voice-url")
        self.assertNotContains(page, "data-record-url")

    def test_a_meeting_thats_on_offers_its_call_in_the_thread_and_no_mic(self):
        page = self.thread()
        self.assertContains(page, f'class="text-btn text-btn--small thread__record" data-record data-record-url="{reverse("recordings:start", args=[self.meeting.id])}"')
        self.assertContains(page, f'data-call-slot="{self.meeting.id}"')
        self.assertNotContains(page, "data-voice-url")

    def test_a_meeting_thats_over_offers_no_call(self):
        Todo.objects.filter(pk=self.meeting.pk).update(starts_at=timezone.localtime().replace(hour=0, minute=0, second=1, microsecond=0),
                                                         ends_at=timezone.localtime().replace(hour=0, minute=0, second=2, microsecond=0))
        self.assertNotContains(self.thread(), "data-record-url")

    @override_settings(GROQ_API_KEY="")
    def test_without_groq_there_is_nothing_to_record(self):
        self.assertNotContains(self.thread(), "data-record-url")

    def test_a_call_left_open_offers_resume(self):
        self.start()
        self.assertContains(self.thread(), ">Resume call</button>")

    def test_the_transcript_stays_out_of_the_thread_and_opens_from_the_meetings_menu(self):
        recording = Recording.objects.create(user=self.user, todo=self.meeting, status=Recording.Status.PROCESSING)
        RecordingPart.objects.create(recording=recording, track="mic", index=0, started_at=timezone.now(), size=1, transcribed=True,
                                     segments=[{"start": 65.0, "end": 70.0, "text": "Send it by https://example.com/plan"}])
        RecordingService.finish(recording)
        page = self.thread().content.decode()
        self.assertIn("Recording · 1 min", page)                 # the short note stays
        self.assertNotIn("Send it by", page)                     # the words don't
        self.assertIn(f'hx-get="{reverse("recordings:transcript", args=[self.meeting.id])}"', page)
        modal = self.client.get(reverse("recordings:transcript", args=[self.meeting.id])).content.decode()
        self.assertIn('<span class="transcript__at">01:05</span><span class="transcript__who">You</span>', modal)
        self.assertIn('class="comment__link"', modal)

    def test_a_meeting_without_a_transcript_has_no_menu(self):
        self.assertNotContains(self.thread(), reverse("recordings:transcript", args=[self.meeting.id]))

    def test_the_page_carries_todays_meeting_starts_for_the_nudge(self):
        self.assertContains(self.thread(), f'data-nudge-url="{reverse("recordings:start", args=[self.meeting.id])}" data-nudge-todo="{self.meeting.id}"')


@override_settings(MEDIA_ROOT=MEDIA, GROQ_API_KEY="key")
class VoiceTests(RecordingTestCase):
    """A voice note said into a todo's composer: its words come straight back, and nothing is kept."""

    def setUp(self):
        super().setUp()
        from todos.schemas import TodoTitleSchema
        from todos.services import TodoService
        self.todo = TodoService.create(self.user, TodoTitleSchema(title="write the plan"))

    def say(self, audio=b"audio"):
        return self.client.post(reverse("recordings:voice", args=[self.todo.id]), audio, content_type="application/octet-stream")

    def test_the_words_come_back_for_the_composer(self):
        with patch.object(GroqService, "transcribe", return_value=[{"start": 0.0, "end": 2.0, "text": "Call Priya about"}, {"start": 2.5, "end": 4.0, "text": "the invoice."}]):
            response = self.say()
        self.assertEqual(response.json(), {"text": "Call Priya about the invoice."})
        self.assertFalse(Recording.objects.exists())          # nothing recorded
        self.assertFalse(self.todo.comments.exists())         # nothing posted: the user edits and sends it
        self.assertFalse((MEDIA / "recordings").exists() and any((MEDIA / "recordings").rglob("*.webm")))

    def test_groq_failing_says_so(self):
        with patch.object(GroqService, "transcribe", side_effect=TranscriptionError("down")):
            response = self.say()
        self.assertEqual(response.status_code, 502)

    def test_too_long_or_empty_is_refused(self):
        self.assertEqual(self.say(b"").status_code, 400)

    def test_guests_cannot_use_voice(self):
        guest = GuestService.create_guest()
        todo = Todo.objects.create(user=guest, title="chat")
        self.client.force_login(guest)
        self.assertEqual(self.client.post(reverse("recordings:voice", args=[todo.id]), b"audio", content_type="application/octet-stream").status_code, 403)

    def test_a_call_needs_a_meeting_thats_on(self):
        self.assertEqual(self.client.post(reverse("recordings:start", args=[self.todo.id])).status_code, 409)
        Todo.objects.filter(pk=self.meeting.pk).update(ends_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.client.post(reverse("recordings:start", args=[self.meeting.id])).status_code, 409)


@override_settings(MEDIA_ROOT=MEDIA, GROQ_API_KEY="key")
class JobTests(RecordingTestCase):
    def test_stopping_queues_its_transcription_once_saved(self):
        recording = self.start()
        with patch("django_q.tasks.async_task") as async_task, self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("recordings:stop", args=[recording["id"]]))
        self.assertEqual(async_task.call_args.args, ("recordings.tasks.process_recording", recording["id"]))

    def test_the_queued_job_transcribes_and_a_second_run_finds_nothing_to_do(self):
        from recordings.tasks import process_recording
        recording = self.start()
        self.chunk(recording["id"], data=b"mic")
        Recording.objects.filter(pk=recording["id"]).update(status=Recording.Status.PROCESSING)
        with patch.object(GroqService, "transcribe", return_value=[{"start": 0.0, "end": 2.0, "text": "Hello."}]) as transcribe:
            self.assertTrue(process_recording(recording["id"]))
            self.assertFalse(process_recording(recording["id"]))  # already done: nothing is sent to Groq again
        self.assertEqual(transcribe.call_count, 1)
        self.assertEqual(Recording.objects.get(pk=recording["id"]).status, Recording.Status.READY)

    def test_the_recurring_jobs_are_written_once_and_kept_in_step(self):
        from django_q.models import Schedule
        from jobs.schedules import SCHEDULES, ensure
        Schedule.objects.create(name="Old job", func="nowhere.gone", schedule_type=Schedule.HOURLY)
        ensure()
        ensure()
        self.assertEqual(sorted(Schedule.objects.values_list("name", flat=True)), sorted(name for name, _, _ in SCHEDULES))
        self.assertEqual(Schedule.objects.get(name="Process stuck recordings").minutes, 15)
