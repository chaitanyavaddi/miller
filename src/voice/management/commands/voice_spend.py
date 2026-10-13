from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Count, DecimalField, F, Sum
from django.utils import timezone

from voice.models import VoiceCommand


class Command(BaseCommand):
    help = "What voice mode has cost, per user (model + speech), most first. --days N for just the last N days."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=0)

    def handle(self, *args, **options):
        commands = VoiceCommand.objects.all()
        if options["days"]:
            commands = commands.filter(created_at__gte=timezone.now() - timedelta(days=options["days"]))
        rows = commands.values("user__email", "user__is_guest").annotate(
            n=Count("id"), llm=Sum("llm_cost"), stt=Sum("stt_cost"),
            total=Sum(F("llm_cost") + F("stt_cost"), output_field=DecimalField(max_digits=12, decimal_places=6)),
        ).order_by("-total")
        grand = Decimal(0)
        self.stdout.write(f"{'user':44s} {'commands':>8s} {'model':>10s} {'speech':>10s} {'total':>10s}")
        for row in rows:
            name = ("guest " + row["user__email"][6:14]) if row["user__is_guest"] else row["user__email"]
            grand += row["total"] or 0
            self.stdout.write(f"{name:44s} {row['n']:8d} {row['llm'] or 0:10.4f} {row['stt'] or 0:10.4f} {row['total'] or 0:10.4f}")
        self.stdout.write(f"{'all':44s} {'':8s} {'':10s} {'':10s} {grand:10.4f}  (USD)")
