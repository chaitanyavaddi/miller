from django.dispatch import receiver

from accounts.signals import guest_merged
from voice.models import VoiceCommand


@receiver(guest_merged)
def move_guest_voice(sender, guest, user, **kwargs):
    """What a guest spent on voice stays on the record, under the account they signed in to."""
    VoiceCommand.objects.filter(user=guest).update(user=user)
