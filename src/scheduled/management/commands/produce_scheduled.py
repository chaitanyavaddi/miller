from django.core.management.base import BaseCommand

from scheduled.services import ProductionService


class Command(BaseCommand):
    help = "Put what's scheduled for today on each user's list, in their own time zone. Run every 15 minutes."

    def handle(self, *args, **options):
        self.stdout.write(f"produced {ProductionService.produce_all()}")
