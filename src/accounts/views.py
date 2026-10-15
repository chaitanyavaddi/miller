from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme
from django.urls import reverse
from django.views import View

from accounts.schemas import CodeSchema, EmailSchema
from accounts.services import CodeService, GoogleOAuthService, GoogleProfile, IdentityService, MailService
from utils.view_helper import redirect_to

SIGNIN_EMAIL = "signin_email"   # in the session: the address a code went to


def finish_sign_in(request, user):
    """Signed in, by Google or by a code: back where the admin asked to go, else the list."""
    after = request.session.pop("next_after_login", "")   # read before login(), which can start a new session
    IdentityService.sign_in(request, user)
    if after and url_has_allowed_host_and_scheme(after, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect_to(request, after)
    return redirect_to(request, "board:index")


class LoginView(View):
    """The way in: Google, or an email that gets a code."""

    def get(self, request):
        if request.user.is_authenticated and not request.user.is_guest:
            return redirect_to(request, "board:index")
        return render(request, "accounts/login.html", {
            "step":           "start",
            "google_enabled": GoogleOAuthService.is_configured(),
            "email_enabled":  MailService.is_configured(),
            "signed_out":     request.GET.get("out") == "1",
            "email":          request.session.get(SIGNIN_EMAIL, ""),
        })


class EmailStartView(View):
    """An email typed in: a code goes to it."""

    def post(self, request):
        data = EmailSchema.from_post(request)
        if not data.is_valid():
            return redirect_to(request, "accounts:login")
        if IdentityService.is_disabled(data.email):
            messages.error(request, "This account is disabled.")
            return redirect_to(request, "accounts:login")
        problem = CodeService.send(data.email)
        if problem:
            messages.error(request, problem)
            return redirect_to(request, "accounts:login" if request.session.get(SIGNIN_EMAIL) != data.email else "accounts:code")
        request.session[SIGNIN_EMAIL] = data.email
        return redirect_to(request, "accounts:code")


class CodeView(View):
    """The code from the email: right, and you're in (a new account, or the one with that address)."""

    def dispatch(self, request, *args, **kwargs):
        self.email = request.session.get(SIGNIN_EMAIL, "")
        if not self.email:
            return redirect_to(request, "accounts:login")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return render(request, "accounts/login.html", {"step": "code", "email": self.email})

    def post(self, request):
        data = CodeSchema.from_post(request)
        problem = "" if data.is_valid() else "invalid"
        if not problem:
            problem = CodeService.check(self.email, data.code)
            if problem:
                messages.error(request, problem)
        if problem:
            return render(request, "accounts/login.html", {"step": "code", "email": self.email})
        if IdentityService.is_disabled(self.email):
            messages.error(request, "This account is disabled.")
            return redirect_to(request, "accounts:login")
        guest = request.user if request.user.is_authenticated else None
        user = IdentityService.claim(guest, GoogleProfile(email=self.email, name="", avatar_url=""))   # a guest's todos come along
        request.session.pop(SIGNIN_EMAIL, None)
        return finish_sign_in(request, user)


class ResendCodeView(View):
    def post(self, request):
        email = request.session.get(SIGNIN_EMAIL, "")
        if not email:
            return redirect_to(request, "accounts:login")
        problem = CodeService.send(email)
        messages.error(request, problem) if problem else messages.success(request, "A new code is on its way.")
        return redirect_to(request, "accounts:code")


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

        guest = request.user if request.user.is_authenticated else None
        return finish_sign_in(request, IdentityService.claim(guest, profile))

    @staticmethod
    def _fail(request, text):
        messages.error(request, text)
        return redirect_to(request, "accounts:login")
