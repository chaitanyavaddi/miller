import base64
import json
import secrets
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings
from django.urls import reverse

from calendars.models import CalendarConnection

STATE_KEY  = "calendar_oauth_state"
NOTICE_KEY = "calendar_notice"  # how the connect flow tells the settings modal what happened


@dataclass(frozen=True)
class ProviderConfig:
    auth_url: str
    token_url: str
    scope: str
    client_id: str
    client_secret: str
    extra: dict


@dataclass(frozen=True)
class CalendarGrant:
    """What a finished consent gives us: a refresh token Recall can sync with, and whose calendar it is."""
    refresh_token: str
    email: str


class CalendarOAuthService:
    """The consent screens for reading a calendar. Separate from sign-in: it asks for calendar access
    and an offline refresh token, and may be for a different address than the one signed in."""

    @staticmethod
    def config(provider: str) -> ProviderConfig:
        if provider == CalendarConnection.Provider.MICROSOFT:
            return ProviderConfig(
                auth_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
                scope="openid email profile offline_access https://graph.microsoft.com/Calendars.Read",
                client_id=settings.MICROSOFT_CLIENT_ID,
                client_secret=settings.MICROSOFT_CLIENT_SECRET,
                extra={"prompt": "select_account", "response_mode": "query"},
            )
        return ProviderConfig(
            auth_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            scope="openid email https://www.googleapis.com/auth/calendar.events.readonly",
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
            extra={"access_type": "offline", "prompt": "consent select_account", "include_granted_scopes": "true"},
        )

    @staticmethod
    def is_configured(provider: str) -> bool:
        config = CalendarOAuthService.config(provider)
        return bool(config.client_id and config.client_secret)

    @staticmethod
    def callback_url(provider: str) -> str:
        return settings.SITE_URL + reverse("calendars:callback", args=[provider])

    @staticmethod
    def issue_state(session, provider: str) -> str:
        state = secrets.token_urlsafe(24)
        session[STATE_KEY] = f"{provider}:{state}"
        return state

    @staticmethod
    def consume_state(session, provider: str, given: str) -> bool:
        saved = session.pop(STATE_KEY, "")
        return bool(saved) and secrets.compare_digest(saved, f"{provider}:{given}")

    @staticmethod
    def authorization_url(provider: str, state: str, login_hint: str = "") -> str:
        config = CalendarOAuthService.config(provider)
        params = {
            "client_id":     config.client_id,
            "redirect_uri":  CalendarOAuthService.callback_url(provider),
            "response_type": "code",
            "scope":         config.scope,
            "state":         state,
            **config.extra,
        }
        if login_hint:
            params["login_hint"] = login_hint
        return config.auth_url + "?" + urllib.parse.urlencode(params)

    @staticmethod
    def exchange(provider: str, code: str) -> CalendarGrant | None:
        """Trade the callback code for a refresh token; None if the provider can't be reached or gave none."""
        config = CalendarOAuthService.config(provider)
        body = urllib.parse.urlencode({
            "code":          code,
            "client_id":     config.client_id,
            "client_secret": config.client_secret,
            "redirect_uri":  CalendarOAuthService.callback_url(provider),
            "grant_type":    "authorization_code",
        }).encode()
        try:
            request = urllib.request.Request(config.token_url, data=body, method="POST")
            with urllib.request.urlopen(request, timeout=10) as response:
                tokens = json.loads(response.read())
        except (OSError, ValueError):
            return None
        refresh_token = tokens.get("refresh_token", "")
        email = CalendarOAuthService._email(tokens.get("id_token", ""))
        if not refresh_token or not email:
            return None
        return CalendarGrant(refresh_token=refresh_token, email=email)

    @staticmethod
    def _email(id_token: str) -> str:
        """The address in the ID token. It came straight from the provider's token endpoint over
        TLS, so its claims are read, not verified."""
        try:
            payload = id_token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except (IndexError, ValueError):
            return ""
        return (claims.get("email") or claims.get("preferred_username") or "").strip().lower()
