import json
import secrets
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings
from django.urls import reverse

AUTH_URL     = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL    = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
STATE_KEY    = "google_oauth_state"


@dataclass(frozen=True)
class GoogleProfile:
    email: str
    name: str
    avatar_url: str

    @classmethod
    def from_userinfo(cls, info: dict) -> "GoogleProfile | None":
        email = (info.get("email") or "").strip().lower()
        if not email or not info.get("email_verified"):
            return None
        return cls(email=email, name=info.get("name", ""), avatar_url=info.get("picture", ""))


class GoogleOAuthService:
    @staticmethod
    def is_configured() -> bool:
        return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)

    @staticmethod
    def callback_url() -> str:
        return settings.SITE_URL + reverse("accounts:google-callback")

    @staticmethod
    def issue_state(session) -> str:
        state = secrets.token_urlsafe(24)
        session[STATE_KEY] = state
        return state

    @staticmethod
    def consume_state(session, given: str) -> bool:
        saved = session.pop(STATE_KEY, "")
        return bool(saved) and secrets.compare_digest(saved, given)

    @staticmethod
    def authorization_url(state: str) -> str:
        params = {
            "client_id":     settings.GOOGLE_CLIENT_ID,
            "redirect_uri":  GoogleOAuthService.callback_url(),
            "response_type": "code",
            "scope":         "openid email profile",
            "state":         state,
            "access_type":   "online",
            "prompt":        "select_account",
        }
        return AUTH_URL + "?" + urllib.parse.urlencode(params)

    @staticmethod
    def fetch_userinfo(code: str) -> dict | None:
        """Exchange the callback code for the user's profile; None if Google can't be reached."""
        body = urllib.parse.urlencode({
            "code":          code,
            "client_id":     settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri":  GoogleOAuthService.callback_url(),
            "grant_type":    "authorization_code",
        }).encode()
        try:
            token_request = urllib.request.Request(TOKEN_URL, data=body, method="POST")
            with urllib.request.urlopen(token_request, timeout=10) as response:
                access_token = json.loads(response.read()).get("access_token", "")
            info_request = urllib.request.Request(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
            with urllib.request.urlopen(info_request, timeout=10) as response:
                return json.loads(response.read())
        except (OSError, ValueError):
            return None
