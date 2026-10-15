from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from accounts.services import GoogleProfile, GuestService, IdentityService
from todos.schemas import TodoTitleSchema
from todos.services import TodoService


def profile(email, name=""):
    return GoogleProfile(email=email, name=name, avatar_url="")


class GuestSessionTests(TestCase):
    def test_first_visit_creates_a_guest_and_keeps_it(self):
        self.assertEqual(self.client.get(reverse("board:index")).status_code, 200)
        guest = User.objects.get()
        self.assertTrue(guest.is_guest)
        self.assertFalse(guest.has_usable_password())
        self.client.get(reverse("board:index"))
        self.assertEqual(User.objects.count(), 1)

    def test_guest_avatar_menu_shows_the_guest_id_and_sign_in(self):
        page = self.client.get(reverse("board:index"))
        guest = User.objects.get()
        self.assertContains(page, f"Guest {guest.email[6:12]}")
        self.assertContains(page, 'title="Sign in to keep your todos"')
        self.assertNotContains(page, "Log out")

    def test_settings_lists_the_keyboard_shortcuts_not_the_avatar_menu(self):
        self.assertNotContains(self.client.get(reverse("board:index")), "Move through todos")
        settings = self.client.get(reverse("board:settings"))
        for what in ("Move through todos", "Move a todo up or down", "New todo", "Search"):
            self.assertContains(settings, what)

    def test_signed_in_avatar_menu_shows_the_name_and_log_out(self):
        self.client.force_login(IdentityService.create_user("ada@x.com", name="Ada"))
        page = self.client.get(reverse("board:index"))
        self.assertContains(page, "Ada")
        self.assertContains(page, "Log out")

    def test_non_get_without_a_session_does_not_mint_guests(self):
        self.client.post(reverse("board:todo-create"), {"title": "x"})
        self.assertEqual(User.objects.count(), 0)

    def test_htmx_request_without_a_session_is_sent_to_the_board(self):
        response = self.client.get(reverse("board:todo-list"), headers={"HX-Request": "true"})
        self.assertEqual(response["HX-Redirect"], reverse("board:index"))

    def test_page_token_passes_csrf_even_beside_a_stale_cookie(self):
        client = Client(enforce_csrf_checks=True)
        client.cookies["csrftoken"] = "A" * 32
        page = client.get(reverse("board:index")).content.decode()
        token = page.split('name="csrf-token" content="')[1].split('"')[0]
        response = client.post(reverse("board:todo-create"), {"title": "x"}, headers={"X-CSRFToken": token})
        self.assertEqual(response.status_code, 200)

    def test_prune_removes_only_empty_old_guests(self):
        keep = GuestService.create_guest()
        TodoService.create(keep, TodoTitleSchema(title="mine"))
        GuestService.create_guest()
        self.assertEqual(GuestService.prune_abandoned(days=-1), 1)
        self.assertTrue(User.objects.filter(pk=keep.pk).exists())


class LoginTests(TestCase):
    def test_there_are_no_password_routes(self):
        for path in ("/signup/", "/forgot-password/"):
            self.assertEqual(self.client.get(path).status_code, 404)
        # The admin has no password form either: its login is Google's (see staff/).
        response = self.client.get("/admin/login/")
        self.assertEqual((response.status_code, response["Location"]), (302, reverse("accounts:login")))

    @override_settings(GOOGLE_CLIENT_ID="id", GOOGLE_CLIENT_SECRET="secret")
    def test_google_start_issues_state_and_redirects(self):
        response = self.client.get(reverse("accounts:google-start"))
        self.assertIn("accounts.google.com", response["Location"])
        self.assertIn("state=" + self.client.session["google_oauth_state"], response["Location"])

    def test_callback_with_a_bad_state_goes_back_to_login(self):
        response = self.client.get(reverse("accounts:google-callback"), {"code": "x", "state": "nope"})
        self.assertEqual(response["Location"], reverse("accounts:login"))

    def test_logout_lands_on_the_signed_out_page(self):
        self.client.force_login(IdentityService.create_user("a@x.com"))
        response = self.client.post(reverse("accounts:logout"), headers={"HX-Request": "true"})
        self.assertEqual(response["HX-Redirect"], reverse("accounts:login") + "?out=1")


class ClaimTests(TestCase):
    def guest_with(self, *titles):
        guest = GuestService.create_guest()
        for title in titles:
            TodoService.create(guest, TodoTitleSchema(title=title))
        return guest

    def test_new_identity_promotes_the_guest_in_place(self):
        guest = self.guest_with("a", "b")
        user = IdentityService.claim(guest, profile("new@x.com", "New Person"))
        self.assertEqual(user.pk, guest.pk)
        self.assertFalse(user.is_guest)
        self.assertEqual(user.email, "new@x.com")
        self.assertEqual(TodoService.open_for(user).count(), 2)

    def test_existing_identity_receives_the_guests_todos_after_its_own(self):
        mine = IdentityService.create_user("me@x.com")
        TodoService.create(mine, TodoTitleSchema(title="old"))
        guest = self.guest_with("g1", "g2")
        TodoService.close(TodoService.create(guest, TodoTitleSchema(title="g3")))
        user = IdentityService.claim(guest, profile("me@x.com"))
        self.assertEqual(user.pk, mine.pk)
        self.assertEqual(list(TodoService.open_for(user).values_list("title", "position")), [("old", 1), ("g1", 2), ("g2", 3)])
        self.assertEqual(user.todos.filter(closed_at__isnull=False).count(), 1)
        self.assertFalse(User.objects.filter(pk=guest.pk).exists())

    def test_without_a_guest_it_creates_or_returns_the_identity(self):
        self.assertEqual(IdentityService.claim(None, profile("x@x.com")).email, "x@x.com")
        IdentityService.claim(None, profile("x@x.com"))
        self.assertEqual(User.objects.filter(email="x@x.com").count(), 1)

    def test_a_real_user_is_never_treated_as_a_guest(self):
        real = IdentityService.create_user("r@x.com")
        other = IdentityService.create_user("o@x.com")
        TodoService.create(real, TodoTitleSchema(title="keep"))
        IdentityService.claim(real, profile("o@x.com"))
        self.assertTrue(User.objects.filter(pk=real.pk).exists())
        self.assertEqual(TodoService.open_for(other).count(), 0)

    def test_unverified_google_email_is_rejected(self):
        self.assertIsNone(GoogleProfile.from_userinfo({"email": "a@x.com", "email_verified": False}))



@override_settings(RESEND_API_KEY="test-key")
class EmailCodeTests(TestCase):
    """Signing in by email: a 6-digit code goes to the address; typing it back signs you in. No passwords."""

    def setUp(self):
        from unittest.mock import patch
        self.sent = []
        patcher = patch("accounts.services.code_service.MailService.send", side_effect=lambda to, subject, text: self.sent.append((to, text)) or True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def code(self):
        import re
        return re.search(r"\b(\d{6})\b", self.sent[-1][1]).group(1)

    def start(self, email):
        return self.client.post(reverse("accounts:email"), {"email": email})

    def signed_in(self):
        return User.objects.filter(pk=self.client.session.get("_auth_user_id")).first()

    def test_the_login_page_offers_google_and_an_email_code(self):
        page = self.client.get(reverse("accounts:login"))
        self.assertContains(page, 'name="email"')
        self.assertContains(page, ">Send code</button>")
        self.assertNotContains(page, "password")

    def test_a_new_email_gets_a_code_and_the_code_makes_the_account(self):
        self.assertRedirects(self.start("New@X.com"), reverse("accounts:code"))
        self.assertEqual(self.sent[0][0], "new@x.com")
        self.assertContains(self.client.get(reverse("accounts:code")), "Enter the code")
        response = self.client.post(reverse("accounts:code"), {"code": self.code()})
        self.assertRedirects(response, reverse("board:index"), fetch_redirect_response=False)
        user = User.objects.get(email="new@x.com")
        self.assertEqual(self.signed_in(), user)
        self.assertFalse(user.has_usable_password())

    def test_an_existing_google_account_signs_in_by_code_into_the_same_account(self):
        user = IdentityService.create_user("old@x.com", name="Old")
        self.start("old@x.com")
        self.client.post(reverse("accounts:code"), {"code": self.code()})
        self.assertEqual(self.signed_in(), user)
        self.assertEqual(User.objects.filter(email="old@x.com").count(), 1)

    def test_a_wrong_code_counts_down_and_then_stops_working(self):
        self.start("new@x.com")
        right = self.code()
        wrong = "000000" if right != "000000" else "111111"
        self.assertContains(self.client.post(reverse("accounts:code"), {"code": wrong}), "4 tries left")
        for _ in range(4):
            self.client.post(reverse("accounts:code"), {"code": wrong})
        self.assertContains(self.client.post(reverse("accounts:code"), {"code": right}), "expired")
        self.assertIsNone(self.signed_in())
        self.assertFalse(User.objects.filter(email="new@x.com").exists())

    def test_a_code_works_once_and_a_new_one_replaces_the_old(self):
        from datetime import timedelta
        from django.utils import timezone
        from accounts.models import EmailCode
        self.start("new@x.com")
        old = self.code()
        EmailCode.objects.update(created_at=timezone.now() - timedelta(minutes=1))
        self.client.post(reverse("accounts:resend-code"))
        self.assertEqual(len(self.sent), 2)
        self.assertContains(self.client.post(reverse("accounts:code"), {"code": old if old != self.code() else "000000"}), "form-message--error")

    def test_codes_are_rationed(self):
        self.start("new@x.com")
        self.assertContains(self.client.post(reverse("accounts:resend-code"), follow=True), "A code was just sent")
        self.assertEqual(len(self.sent), 1)

    def test_an_old_code_has_expired(self):
        from datetime import timedelta
        from django.utils import timezone
        from accounts.models import EmailCode
        self.start("new@x.com")
        EmailCode.objects.update(created_at=timezone.now() - timedelta(minutes=16))
        self.assertContains(self.client.post(reverse("accounts:code"), {"code": self.code()}), "expired")

    def test_a_disabled_account_cannot_get_in(self):
        IdentityService.create_user("gone@x.com", is_active=False)
        self.start("gone@x.com")
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.signed_in())

    def test_a_guests_todos_come_along(self):
        self.client.get(reverse("board:index"))   # a guest, from the first visit
        guest = User.objects.get(is_guest=True)
        TodoService.create(guest, TodoTitleSchema(title="from the guest"))
        self.start("new@x.com")
        self.client.post(reverse("accounts:code"), {"code": self.code()})
        user = User.objects.get(email="new@x.com")
        self.assertEqual(list(TodoService.open_for(user).values_list("title", flat=True)), ["from the guest"])

    def test_the_code_step_needs_an_email_first(self):
        self.assertRedirects(self.client.get(reverse("accounts:code")), reverse("accounts:login"))

    def test_only_the_codes_hash_is_kept(self):
        from accounts.models import EmailCode
        self.start("new@x.com")
        self.assertNotIn(self.code(), EmailCode.objects.get().code_hash)


class KeepSignedInTests(TestCase):
    def test_a_signed_in_session_lasts_400_days_and_is_moved_on_at_most_daily(self):
        from django.conf import settings
        from accounts.middleware import REFRESHED
        self.assertEqual(settings.SESSION_COOKIE_AGE, 60 * 60 * 24 * 400)
        self.client.force_login(IdentityService.create_user("me@x.com"))
        self.client.get(reverse("board:index"))
        first = self.client.session[REFRESHED]
        session = self.client.session
        session[REFRESHED] = first - 2 * 24 * 3600   # as if last moved on two days ago
        session.save()
        response = self.client.get(reverse("board:index"))
        self.assertGreater(self.client.session[REFRESHED], first - 3600)
        self.assertIn("sessionid", response.cookies)   # a fresh cookie, good for another 400 days
