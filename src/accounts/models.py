from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.db import models

GUEST_EMAIL_DOMAIN = "guest.invalid"


class User(AbstractBaseUser, PermissionsMixin):
    """Signed in with Google or a code emailed to the address, or a guest held by a session cookie until they do.
    Never a password."""
    email         = models.EmailField(unique=True)
    name          = models.CharField(max_length=200, blank=True)
    avatar_url    = models.URLField(max_length=500, blank=True)
    is_active     = models.BooleanField(default=True)
    is_guest      = models.BooleanField(default=False)
    timezone      = models.CharField(max_length=64, blank=True)  # blank: not chosen yet, the site default applies
    show_meetings = models.BooleanField(default=False)
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"

    def __str__(self):
        return self.email

    @property
    def is_staff(self) -> bool:
        """Who may use the admin (django.contrib.admin asks for this): the people flagged with `manage.py make_staff`,
        which sets is_superuser. Staff and superuser are the same thing here."""
        return self.is_superuser

    @property
    def display_name(self):
        if self.is_guest:
            return f"Guest {self.email.removeprefix('guest-')[:6]}"
        return self.name or self.email

    @property
    def tzinfo(self) -> ZoneInfo:
        """The zone the user's days are counted in."""
        try:
            return ZoneInfo(self.timezone or settings.TIME_ZONE)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo(settings.TIME_ZONE)

    @property
    def initial(self):
        return "G" if self.is_guest else self.display_name[:1].upper()


class EmailCode(models.Model):
    """A 6-digit code emailed to sign in with: typing it back proves the address is yours. Only its hash is kept."""
    email      = models.EmailField(db_index=True)
    code_hash  = models.CharField(max_length=128)
    attempts   = models.PositiveSmallIntegerField(default=0)
    used_at    = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"code for {self.email}"
