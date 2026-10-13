from django.core.management.base import BaseCommand, CommandError

from accounts.models import GUEST_EMAIL_DOMAIN, User
from accounts.services import IdentityService


class Command(BaseCommand):
    help = ("Let an account into the admin (--revoke takes it away). They sign in with Google as usual, then open the admin's address. "
            "If they haven't signed in yet, the account is made now and their first Google sign-in lands on it.")

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--revoke", action="store_true")

    def handle(self, *args, email, revoke, **options):
        email = email.strip().lower()
        if email.endswith(f"@{GUEST_EMAIL_DOMAIN}"):
            raise CommandError("That's a guest, not an account anyone can sign in to.")
        user = User.objects.filter(email=email).first()
        if user is None:
            if revoke:
                raise CommandError(f"No account for {email}.")
            user = IdentityService.create_user(email)
            self.stdout.write(f"Made the account for {email}: their first Google sign-in will land on it.")
        user.is_superuser = not revoke   # staff and superuser are the same thing (accounts.User.is_staff)
        user.save(update_fields=["is_superuser", "updated_at"])
        self.stdout.write(self.style.SUCCESS(f"{email} {'is no longer' if revoke else 'is now'} staff."))
