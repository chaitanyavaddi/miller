import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from collections.abc import Iterator
from datetime import datetime

from django.conf import settings

from calendars.models import CalendarConnection

PLATFORMS = {
    CalendarConnection.Provider.GOOGLE:    "google_calendar",
    CalendarConnection.Provider.MICROSOFT: "microsoft_outlook",
}
WEBHOOK_TOLERANCE_SECONDS = 5 * 60


class RecallError(Exception):
    """Recall could not be reached, or refused the request."""


class RecallService:
    """Recall.ai's Calendar V2 API: it holds each calendar's refresh token, keeps the events in
    sync and tells us (by webhook) when they change."""

    @staticmethod
    def is_configured() -> bool:
        return bool(settings.RECALL_API_KEY)

    @staticmethod
    def create_calendar(provider: str, oauth_client_id: str, oauth_client_secret: str, refresh_token: str) -> dict:
        return RecallService._call("POST", "/api/v2/calendars/", {
            "platform":            PLATFORMS[provider],
            "oauth_client_id":     oauth_client_id,
            "oauth_client_secret": oauth_client_secret,
            "oauth_refresh_token": refresh_token,
        })

    @staticmethod
    def calendar(recall_id: str) -> dict:
        return RecallService._call("GET", f"/api/v2/calendars/{recall_id}/")

    @staticmethod
    def delete_calendar(recall_id: str) -> None:
        RecallService._call("DELETE", f"/api/v2/calendars/{recall_id}/")

    @staticmethod
    def events(recall_id: str, starts_after: datetime, updated_since: datetime | None = None) -> Iterator[dict]:
        """Every event of a calendar starting after `starts_after` (changed since `updated_since`, if
        given), following the pages."""
        query = {"calendar_id": recall_id, "start_time__gte": starts_after.isoformat()}
        if updated_since is not None:
            query["updated_at__gte"] = updated_since.isoformat()
        url = "/api/v2/calendar-events/?" + urllib.parse.urlencode(query)
        while url:
            page = RecallService._call("GET", url)
            yield from page.get("results", [])
            url = page.get("next") or ""

    @staticmethod
    def verify_webhook(headers, body: bytes) -> bool:
        """Recall signs webhooks the Svix way: HMAC-SHA256 over "id.timestamp.body" with the
        endpoint's secret. Without a secret configured nothing is accepted."""
        secret = settings.RECALL_WEBHOOK_SECRET
        message_id = headers.get("webhook-id") or headers.get("svix-id") or ""
        stamp = headers.get("webhook-timestamp") or headers.get("svix-timestamp") or ""
        signatures = headers.get("webhook-signature") or headers.get("svix-signature") or ""
        if not (secret and message_id and stamp.isdigit() and signatures):
            return False
        if abs(time.time() - int(stamp)) > WEBHOOK_TOLERANCE_SECONDS:
            return False
        try:
            key = base64.b64decode(secret.removeprefix("whsec_"))
        except ValueError:
            return False
        signed = f"{message_id}.{stamp}.".encode() + body
        expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
        return any(hmac.compare_digest(expected, part.partition(",")[2]) for part in signatures.split())

    @staticmethod
    def _call(method: str, path_or_url: str, payload: dict | None = None) -> dict:
        url = path_or_url if path_or_url.startswith("http") else settings.RECALL_API_BASE + path_or_url
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Token {settings.RECALL_API_KEY}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read()
            return json.loads(raw) if raw else {}
        except (OSError, ValueError) as error:
            raise RecallError(str(error)) from error
