from django.db.models.signals import post_delete
from django.dispatch import receiver

from comments.models import Attachment


@receiver(post_delete, sender=Attachment)
def remove_attachment_file(sender, instance, **kwargs):
    """The row is gone (directly or with its todo), so the file on disk goes too."""
    if instance.file:
        instance.file.delete(save=False)
