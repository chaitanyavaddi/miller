from django.core.management.base import BaseCommand
from django.utils import timezone

from calendars.models import CalendarConnection


class Command(BaseCommand):
    help = "Show each connected calendar: its status, last sync, and whether its push channel is live (and until when)."

    def handle(self, *args, **options):
        now = timezone.now()
        connections = CalendarConnection.objects.select_related("user").prefetch_related("sources").order_by("user__email", "email")
        if not connections:
            self.stdout.write("No calendars connected.")
        for connection in connections:
            synced = timezone.localtime(connection.synced_at).strftime("%b %d %H:%M") if connection.synced_at else "never"
            self.stdout.write(f"{connection.email} ({connection.get_provider_display()}) · {connection.status} · synced {synced} · user {connection.user.email}")
            for source in connection.sources.filter(enabled=True):
                if not source.channel_id:
                    push = "no push channel (needs https SITE_URL; the hourly upkeep keeps it fresh meanwhile)"
                elif source.channel_expires_at and source.channel_expires_at < now:
                    push = "push channel EXPIRED: is the worker running?"
                else:
                    left = source.channel_expires_at - now if source.channel_expires_at else None
                    push = f"push live, {left.days}d {left.seconds // 3600}h left" if left else "push live"
                self.stdout.write(f"    {source.name}: {push}")
