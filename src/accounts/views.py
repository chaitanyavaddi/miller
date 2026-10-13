from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme
from django.urls import reverse
from django.views import View

from accounts.services import GoogleOAuthService, GoogleProfile, IdentityService
from utils.view_helper import redirect_to


class LoginView(View):
    def get(self, request):
        if request.user.is_authenticated and not request.user.is_guest:
            return redirect_to(request, "board:index")
        return render(request, "accounts/login.html", {
            "google_enabled": GoogleOAuthService.is_configured(),
            "signed_out":     request.GET.get("out") == "1",
        })


class LogoutView(View):
    def post(self, request):
        logout(request)
        return redirect_to(request, reverse("accounts:login") + "?out=1")


class GoogleStartView(View):
    def get(self, request):
        if not GoogleOAuthService.is_configured():
            messages.error(request, "Google sign-in isn't configured.")
            return redirect_to(request, "accounts:login")
        state = GoogleOAuthService.issue_state(request.session)
        return redirect_to(request, GoogleOAuthService.authorization_url(state))


class GoogleCallbackView(View):
    def get(self, request):
        if request.GET.get("error"):
            return self._fail(request, "Sign-in was cancelled.")
        if not GoogleOAuthService.consume_state(request.session, request.GET.get("state", "")):
            return self._fail(request, "That sign-in link expired. Try again.")
        code = request.GET.get("code", "")
        if not code:
            return self._fail(request, "Google didn't return a code.")

        info = GoogleOAuthService.fetch_userinfo(code)
        if info is None:
            return self._fail(request, "Couldn't reach Google. Try again.")
        profile = GoogleProfile.from_userinfo(info)
        if profile is None:
            return self._fail(request, "Google didn't give us a verified email.")
        if IdentityService.is_disabled(profile.email):
            return self._fail(request, "This account is disabled.")

        # Read before signing in, which can start a new session: the admin asks to be returned to.
        after = request.session.pop("next_after_login", "")
        guest = request.user if request.user.is_authenticated else None
        user = IdentityService.claim(guest, profile)
        IdentityService.sign_in(request, user)
        if after and url_has_allowed_host_and_scheme(after, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return redirect_to(request, after)
        return redirect_to(request, "board:index")

    @staticmethod
    def _fail(request, text):
        messages.error(request, text)
        return redirect_to(request, "accounts:login")
