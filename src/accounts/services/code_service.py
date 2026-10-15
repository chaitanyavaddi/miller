import secrets
from datetime import timedelta

from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from accounts.models import EmailCode
from accounts.services.mail_service import MailService

CODE_TTL = timedelta(minutes=15)       # a code works this long
CODE_TRIES = 5                         # wrong guesses before a code stops working
RESEND_AFTER = timedelta(seconds=30)   # the shortest gap between two codes to one address
CODES_PER_HOUR = 5                     # and the most in an hour


class CodeService:
    """Signing in by email: a 6-digit code goes to the address, and typing it back proves it's yours."""

    @staticmethod
    def _hash(email: str, code: str) -> str:
        return salted_hmac("accounts.email-code", f"{email}:{code}").hexdigest()

    @staticmethod
    def send(email: str) -> str:
        """Email a fresh code (any earlier one stops working). Returns why it couldn't, or ""."""
        now = timezone.now()
        recent = EmailCode.objects.filter(email=email, created_at__gte=now - timedelta(hours=1))
        if recent.filter(created_at__gte=now - RESEND_AFTER).exists():
            return "A code was just sent. Wait a few seconds before asking for another."
        if recent.count() >= CODES_PER_HOUR:
            return "Too many codes for this address. Try again in an hour."
        code = f"{secrets.randbelow(1_000_000):06d}"
        EmailCode.objects.filter(email=email, used_at__isnull=True).update(used_at=now)
        EmailCode.objects.create(email=email, code_hash=CodeService._hash(email, code))
        sent = MailService.send(email, f"{code} is your Miller code",
                                f"Your Miller sign-in code is {code}\n\nIt works for 15 minutes. If you didn't ask for it, ignore this email.")
        return "" if sent else "The code couldn't be sent. Try again in a minute."

    @staticmethod
    def check(email: str, code: str) -> str:
        """Use up the code if it's right. Returns why it isn't, or ""."""
        found = EmailCode.objects.filter(email=email, used_at__isnull=True, created_at__gte=timezone.now() - CODE_TTL).first()
        if found is None or found.attempts >= CODE_TRIES:
            return "That code has expired. Send a new one."
        found.attempts += 1
        if not constant_time_compare(found.code_hash, CodeService._hash(email, code.strip())):
            found.save(update_fields=["attempts"])
            left = CODE_TRIES - found.attempts
            return f"That code isn't right. {left} {'try' if left == 1 else 'tries'} left." if left else "That code has expired. Send a new one."
        found.used_at = timezone.now()
        found.save(update_fields=["attempts", "used_at"])
        return ""
