from django.contrib.auth import login
from django.db import transaction

from accounts.models import User
from accounts.services.google_service import GoogleProfile
from accounts.signals import guest_merged

AUTH_BACKEND = "django.contrib.auth.backends.ModelBackend"


class IdentityService:
    @staticmethod
    def create_user(email: str, **fields) -> User:
        user = User(email=email.strip().lower(), **fields)
        user.set_unusable_password()
        user.save()
        return user

    @staticmethod
    def sign_in(request, user: User) -> None:
        login(request, user, backend=AUTH_BACKEND)

    @staticmethod
    def is_disabled(email: str) -> bool:
        return User.objects.filter(email=email, is_active=False).exists()

    @staticmethod
    @transaction.atomic
    def claim(guest: User | None, profile: GoogleProfile) -> User:
        """The account a Google sign-in lands on, carrying over what the guest session built up.

        A new identity takes over the guest row as-is. An existing one receives the guest's data
        (via `guest_merged`) and the guest row is deleted.
        """
        guest = guest if guest is not None and guest.is_guest else None
        existing = User.objects.filter(email=profile.email).first()

        if existing is None and guest is None:
            return IdentityService.create_user(profile.email, name=profile.name, avatar_url=profile.avatar_url)
        if existing is None:
            return IdentityService._promote(guest, profile)

        if guest is not None and guest.pk != existing.pk:
            guest_merged.send(sender=User, guest=guest, user=existing)
            guest.delete()
        if profile.name and not existing.name:
            existing.name = profile.name
            existing.save(update_fields=["name", "updated_at"])
        return existing

    @staticmethod
    def _promote(guest: User, profile: GoogleProfile) -> User:
        guest.email      = profile.email
        guest.is_guest   = False
        guest.name       = profile.name or guest.name
        guest.avatar_url = profile.avatar_url or guest.avatar_url
        guest.save(update_fields=["email", "is_guest", "name", "avatar_url", "updated_at"])
        return guest
