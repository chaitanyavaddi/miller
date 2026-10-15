import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)
RESEND_URL = "https://api.resend.com/emails"


class MailService:
    """Email out, through Resend. Without RESEND_API_KEY nothing is sent: in development the message is logged
    instead (so codes can be read off the console); in production, email sign-in is off."""

    @staticmethod
    def is_configured() -> bool:
        return bool(settings.RESEND_API_KEY) or settings.DEBUG

    @staticmethod
    def send(to: str, subject: str, text: str) -> bool:
        if not settings.RESEND_API_KEY:
            logger.warning("Email not sent (no RESEND_API_KEY) to %s: %s\n%s", to, subject, text)
            return settings.DEBUG
        body = json.dumps({"from": settings.MAIL_FROM, "to": [to], "subject": subject, "text": text}).encode()
        request = urllib.request.Request(RESEND_URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {settings.RESEND_API_KEY}", "Content-Type": "application/json",
            "User-Agent": "Miller/1.0",   # without one, Cloudflare in front of the API refuses the call
        })
        try:
            with urllib.request.urlopen(request, timeout=10):
                return True
        except (urllib.error.URLError, TimeoutError) as error:
            detail = error.read().decode(errors="replace")[:300] if isinstance(error, urllib.error.HTTPError) else str(error)
            logger.error("Resend refused an email to %s: %s", to, detail)
            return False
