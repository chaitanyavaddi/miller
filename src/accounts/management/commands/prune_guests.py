from django.core.management.base import BaseCommand

from accounts.services import GuestService


class Command(BaseCommand):
    help = "Delete guests who never added a todo, once they are older than --days."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7)

    def handle(self, *args, days, **options):
        self.stdout.write(f"deleted {GuestService.prune_abandoned(days)}")
