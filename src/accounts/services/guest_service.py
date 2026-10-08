import uuid
from datetime import timedelta

from django.utils import timezone

from accounts.models import GUEST_EMAIL_DOMAIN, User
from accounts.services.identity_service import IdentityService


class GuestService:
    @staticmethod
    def create_guest() -> User:
        return IdentityService.create_user(f"guest-{uuid.uuid4().hex}@{GUEST_EMAIL_DOMAIN}", is_guest=True)

    @staticmethod
    def start_session(request) -> User:
        guest = GuestService.create_guest()
        IdentityService.sign_in(request, guest)
        return guest

    @staticmethod
    def prune_abandoned(days: int) -> int:
        """Guests older than `days` who never added a todo: crawlers and bounced visits."""
        cutoff = timezone.now() - timedelta(days=days)
        deleted, _ = User.objects.filter(is_guest=True, created_at__lt=cutoff, todos__isnull=True).delete()
        return deleted
