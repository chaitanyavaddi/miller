from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib import admin
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from accounts.services import GuestService, IdentityService
from calendars.models import CalendarConnection, CalendarSource
from comments.models import Attachment, Comment
from recordings.models import Recording, RecordingPart
from scheduled.models import ScheduledItem
from staff.usage import UsageReport
from todos.models import Todo
from todos.schemas import TodoTitleSchema
from todos.services import TodoService
from voice.models import VoiceCommand


def staff_user(email="boss@x.com"):
    user = IdentityService.create_user(email)
    user.is_superuser = True
    user.save()
    return user


class Base(TestCase):
    def setUp(self):
        self.boss = staff_user()
        self.ada = IdentityService.create_user("ada@x.com", name="Ada")
        self.invoice = TodoService.create(self.ada, TodoTitleSchema(title="Send invoice to Globex"))
        self.acme = TodoService.create(self.ada, TodoTitleSchema(title="Acme follow-up"))
        TodoService.close(self.acme)
        self.note = Comment.objects.create(todo=self.invoice, body="Finance said Friday")
        self.said = VoiceCommand.objects.create(
            user=self.ada, transcript="close the acme one", reply="Closed “Acme follow-up”", outcome="applied", model="m/x",
            operations=[{"op": "close", "todo_id": self.acme.id, "text": None}], applied=[{"op": "close", "todo_id": self.acme.id}],
            heard_ms=400, decide_ms=1200, total_ms=1900, audio_seconds=2.1, llm_cost=Decimal("0.0003"), stt_cost=Decimal("0.0003"))
        self.client.force_login(self.boss)

    def get(self, name, *args, **query):
        return self.client.get(reverse(name, args=args), query)


class AccessTests(Base):
    def test_a_signed_out_visitor_is_sent_to_the_google_login_and_remembered_for_after(self):
        self.client.logout()
        first = self.client.get(reverse("admin:index"))   # the admin's own login address, which hands on to Google's
        self.assertRedirects(first, reverse("admin:login") + "?next=" + reverse("admin:index"), fetch_redirect_response=False)
        self.assertRedirects(self.client.get(first.url), reverse("accounts:login"), fetch_redirect_response=False)
        self.assertEqual(self.client.session["next_after_login"], reverse("admin:index"))

    def test_there_is_no_password_form_to_find(self):
        self.client.logout()
        response = self.client.get(reverse("admin:login"))
        self.assertEqual(response.status_code, 302)
        self.assertNotContains(self.client.get(reverse("accounts:login")), 'type="password"')

    def test_a_guest_goes_to_sign_in_not_to_the_admin(self):
        self.client.force_login(GuestService.create_guest())
        response = self.client.get(reverse("admin:index"), follow=True)
        self.assertEqual(response.redirect_chain[-1][0], reverse("accounts:login"))

    def test_a_member_who_is_not_staff_is_refused(self):
        self.client.force_login(self.ada)
        for name in ("admin:index", "admin:usage", "admin:accounts_user_changelist", "admin:voice_voicecommand_changelist"):
            self.assertEqual(self.client.get(reverse(name), follow=True).status_code, 403, name)

    def test_a_disabled_staff_account_is_refused(self):
        self.boss.is_active = False
        self.assertFalse(admin.site.has_permission(type("Request", (), {"user": self.boss})()))

    def test_staff_are_in(self):
        self.assertContains(self.get("admin:index"), "Right now")

    def test_google_sign_in_takes_staff_back_to_where_they_were_going(self):
        self.client.logout()
        self.client.get(reverse("admin:usage"), follow=True)   # on to Google's login, remembering where they were going
        profile = {"email": "boss@x.com", "email_verified": True, "name": "Boss"}
        with patch("accounts.views.GoogleOAuthService.consume_state", return_value=True), patch("accounts.views.GoogleOAuthService.fetch_userinfo", return_value=profile):
            response = self.client.get(reverse("accounts:google-callback"), {"code": "c", "state": "s"})
        self.assertRedirects(response, reverse("admin:usage"), fetch_redirect_response=False)
        self.assertEqual(self.get("admin:usage").status_code, 200)

    def test_it_never_sends_anyone_off_the_site(self):
        self.client.logout()
        session = self.client.session
        session["next_after_login"] = "https://evil.example/steal"
        session.save()
        profile = {"email": "ada@x.com", "email_verified": True, "name": "Ada"}
        with patch("accounts.views.GoogleOAuthService.consume_state", return_value=True), patch("accounts.views.GoogleOAuthService.fetch_userinfo", return_value=profile):
            response = self.client.get(reverse("accounts:google-callback"), {"code": "c", "state": "s"})
        self.assertRedirects(response, reverse("board:index"), fetch_redirect_response=False)


class PagesTests(Base):
    def setUp(self):
        super().setUp()
        self.recording = Recording.objects.create(user=self.ada, todo=self.invoice, kind="call", status="failed", error="Groq said 429",
                                                  transcript=[{"at": 1, "speaker": "You", "text": "the secret plan is purple"}])
        RecordingPart.objects.create(recording=self.recording, track="mic", index=0, started_at=timezone.now(), segments=[{"text": "purple"}])
        self.connection = CalendarConnection.objects.create(user=self.ada, provider="google", email="ada@gmail.com", refresh_token="ENCRYPTED-TOKEN-VALUE")
        CalendarSource.objects.create(connection=self.connection, external_id="primary", name="Ada", channel_id="chan-123")
        ScheduledItem.objects.create(user=self.ada, title="Weekly review", rhythm="weekly", weekdays="0")
        Attachment.objects.create(comment=self.note, file=ContentFile(b"x", name="plan.txt"), name="plan.txt", size=1, content_type="text/plain")

    def test_every_page_of_the_admin_opens(self):
        for model in admin.site._registry:
            if model._meta.app_label in {"auth"}:
                continue
            url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
            self.assertEqual(self.client.get(url).status_code, 200, url)
        for name, pk in (("accounts_user", self.ada.pk), ("todos_todo", self.invoice.pk), ("comments_comment", self.note.pk),
                         ("voice_voicecommand", self.said.pk), ("recordings_recording", self.recording.pk),
                         ("calendars_calendarconnection", self.connection.pk), ("scheduled_scheduleditem", ScheduledItem.objects.get().pk)):
            self.assertEqual(self.get(f"admin:{name}_change", pk).status_code, 200, name)
        self.assertEqual(self.get("admin:usage").status_code, 200)

    def test_nothing_can_be_added_changed_or_deleted_from_here(self):
        for name, pk in (("accounts_user", self.ada.pk), ("todos_todo", self.invoice.pk), ("voice_voicecommand", self.said.pk),
                         ("comments_comment", self.note.pk), ("recordings_recording", self.recording.pk)):
            self.assertEqual(self.get(f"admin:{name}_add").status_code, 403, name)
            self.assertEqual(self.client.post(reverse(f"admin:{name}_delete", args=[pk]), {"post": "yes"}).status_code, 403, name)
            self.assertEqual(self.client.post(reverse(f"admin:{name}_change", args=[pk]), {}).status_code, 403, name)
        self.assertTrue(VoiceCommand.objects.filter(pk=self.said.pk).exists())

    def test_what_is_private_is_not_shown(self):
        recording = self.get("admin:recordings_recording_change", self.recording.pk)
        self.assertContains(recording, "1 lines (not shown)")
        self.assertContains(recording, "Groq said 429")                   # how it went wrong is
        self.assertNotContains(recording, "the secret plan is purple")    # what was said in it is not
        self.assertNotContains(recording, "purple")
        calendar = self.get("admin:calendars_calendarconnection_change", self.connection.pk)
        self.assertNotContains(calendar, "ENCRYPTED-TOKEN-VALUE")
        self.assertNotContains(calendar, "chan-123")
        note = self.get("admin:comments_comment_change", self.note.pk)
        self.assertContains(note, "plan.txt")
        self.assertNotContains(note, "/media/")

    def test_a_person_shows_their_todos_and_what_they_said(self):
        page = self.get("admin:accounts_user_change", self.ada.pk)
        self.assertContains(page, "Send invoice to Globex")
        self.assertContains(page, "close the acme one")
        self.assertContains(page, f"user__id__exact={self.ada.pk}")

    def test_the_people_list_counts_todos_voice_and_spend(self):
        page = self.get("admin:accounts_user_changelist")
        self.assertContains(page, "ada@x.com")
        self.assertContains(page, "$0.0006")
        row = next(r for r in page.context["cl"].result_list if r.email == "ada@x.com")
        self.assertEqual((row._tasks, row._open, row._commands), (2, 1, 1))

    def test_what_someone_said_can_be_found_by_its_words_or_its_person(self):
        self.assertContains(self.get("admin:voice_voicecommand_changelist", q="acme"), "close the acme one")
        self.assertNotContains(self.get("admin:voice_voicecommand_changelist", q="zebra"), "close the acme one")
        self.assertContains(self.get("admin:voice_voicecommand_changelist", q="ada@x.com"), "close the acme one")

    def test_a_voice_command_shows_everything_needed_to_debug_it(self):
        page = self.get("admin:voice_voicecommand_change", self.said.pk)
        for text in ("close the acme one", "Closed “Acme follow-up”", "m/x", "&quot;op&quot;: &quot;close&quot;", "1200", "1900", "0.000300"):
            self.assertContains(page, text)

    def test_voice_can_be_filtered_to_what_went_wrong_or_was_slow(self):
        VoiceCommand.objects.create(user=self.ada, transcript="uh oh", outcome="failed", total_ms=9000)
        failed = self.get("admin:voice_voicecommand_changelist", outcome__exact="failed")
        self.assertContains(failed, "uh oh")
        self.assertNotContains(failed, "close the acme one")
        slow = self.get("admin:voice_voicecommand_changelist", speed="8")
        self.assertContains(slow, "uh oh")
        self.assertNotContains(slow, "close the acme one")

    def test_todos_can_be_filtered_by_status(self):
        done = self.get("admin:todos_todo_changelist", status="done")
        self.assertContains(done, "Acme follow-up")
        self.assertNotContains(done, "Send invoice to Globex")
        self.assertContains(self.get("admin:todos_todo_changelist", status="open"), "Send invoice to Globex")

    def test_a_todos_thread_shows_on_its_page(self):
        self.assertContains(self.get("admin:todos_todo_change", self.invoice.pk), "Finance said Friday")


class UsageTests(Base):
    def setUp(self):
        super().setUp()
        self.guest = GuestService.create_guest()
        TodoService.create(self.guest, TodoTitleSchema(title="guest todo"))
        VoiceCommand.objects.create(user=self.ada, transcript="x", outcome="failed", total_ms=500, llm_cost=Decimal("0.001"))
        VoiceCommand.objects.create(user=self.ada, transcript="y", outcome="asked", total_ms=3000)
        VoiceCommand.objects.create(user=self.guest, transcript="z", outcome="applied", total_ms=1000, llm_cost=Decimal("0.0002"))

    def test_people_are_counted_with_who_is_active(self):
        people = UsageReport.people()
        self.assertEqual((people["members"], people["guests"]), (2, 1))   # the boss and Ada; one guest
        self.assertEqual(people["active"]["24h"], {"members": 1, "guests": 1})
        self.assertEqual(people["active"]["30d"]["members"], 1)

    def test_someone_long_quiet_is_not_active(self):
        Todo.objects.all().update(updated_at=timezone.now() - timedelta(days=40))
        VoiceCommand.objects.all().update(created_at=timezone.now() - timedelta(days=40))
        Comment.objects.all().update(created_at=timezone.now() - timedelta(days=40))
        self.assertEqual(UsageReport.people()["active"]["30d"], {"members": 0, "guests": 0})

    def test_voice_is_summed_up(self):
        voice = UsageReport.voice(7)
        self.assertEqual((voice["total"], voice["users"], voice["failed"], voice["failure_rate"]), (4, 2, 1, "25%"))
        self.assertEqual(voice["outcomes"], {"applied": 2, "failed": 1, "asked": 1})
        self.assertEqual(voice["models"], [{"model": "m/x", "n": 1}])
        self.assertGreater(voice["p95"], voice["p50"] - 1)
        self.assertEqual(voice["spend"], "$0.0018")

    def test_each_day_has_its_row(self):
        today = UsageReport.daily(30)[0]
        self.assertEqual((today["new_users"], today["todos"], today["closed"], today["voice"], today["failed"]), (3, 3, 1, 4, 1))

    def test_who_spends_most_comes_first(self):
        spenders = UsageReport.top_spenders()
        self.assertEqual([s["email"] for s in spenders][0], "ada@x.com")
        self.assertEqual(spenders[0]["commands"], 3)

    def test_problems_point_at_what_is_failing(self):
        Recording.objects.create(user=self.ada, status="failed")
        CalendarConnection.objects.create(user=self.ada, provider="google", email="a@g.com", refresh_token="t", status="disconnected")
        problems = UsageReport.problems()
        self.assertEqual((problems["voice_failed_24h"], problems["recordings_failed"], problems["calendars_lost"]), (1, 1, 1))
        self.assertIn("outcome__exact=failed", problems["voice_failed_url"])

    def test_the_report_pages_show_it(self):
        usage = self.get("admin:usage")
        for text in ("Day by day", "25%", "ada@x.com", "Latest failed voice commands"):
            self.assertContains(usage, text)
        self.assertContains(self.get("admin:index"), "failed voice command")

    def test_an_empty_site_still_reports(self):
        VoiceCommand.objects.all().delete()
        Todo.objects.all().delete()
        self.assertEqual(self.get("admin:usage").status_code, 200)
        self.assertEqual(UsageReport.voice(7)["failure_rate"], "–")


class MakeStaffTests(TestCase):
    def run_command(self, *args):
        out = StringIO()
        call_command("make_staff", *args, stdout=out)
        return out.getvalue()

    def test_it_flags_an_existing_account(self):
        user = IdentityService.create_user("ada@x.com")
        self.assertFalse(user.is_staff)
        self.run_command("ada@x.com")
        self.assertTrue(User.objects.get(pk=user.pk).is_staff)

    def test_it_makes_the_account_when_they_have_not_signed_in_yet_and_google_lands_on_it(self):
        self.assertIn("Made the account", self.run_command("New@X.com"))
        user = User.objects.get(email="new@x.com")
        self.assertTrue(user.is_staff and not user.has_usable_password())
        from accounts.services import GoogleProfile
        self.assertEqual(IdentityService.claim(None, GoogleProfile("new@x.com", "New", "")).pk, user.pk)

    def test_it_takes_it_away_again(self):
        self.run_command("ada@x.com")
        self.run_command("ada@x.com", "--revoke")
        self.assertFalse(User.objects.get(email="ada@x.com").is_staff)

    def test_it_refuses_a_guest_and_an_unknown_revoke(self):
        with self.assertRaises(CommandError):
            self.run_command("guest-abc@guest.invalid")
        with self.assertRaises(CommandError):
            self.run_command("nobody@x.com", "--revoke")
