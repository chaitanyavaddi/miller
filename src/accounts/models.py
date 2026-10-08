from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.db import models

GUEST_EMAIL_DOMAIN = "guest.invalid"


class User(AbstractBaseUser, PermissionsMixin):
    """Signed in with Google, or a guest held by a session cookie until they do. Never a password."""
    email      = models.EmailField(unique=True)
    name       = models.CharField(max_length=200, blank=True)
    avatar_url = models.URLField(max_length=500, blank=True)
    is_active  = models.BooleanField(default=True)
    is_guest   = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"

    def __str__(self):
        return self.email

    @property
    def display_name(self):
        if self.is_guest:
            return f"Guest {self.email.removeprefix('guest-')[:6]}"
        return self.name or self.email

    @property
    def initial(self):
        return "G" if self.is_guest else self.display_name[:1].upper()
