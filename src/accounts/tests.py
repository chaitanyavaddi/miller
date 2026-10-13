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
