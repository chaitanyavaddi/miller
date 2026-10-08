from django.dispatch import receiver

from accounts.signals import guest_merged
from todos.services import TodoService


@receiver(guest_merged)
def move_guest_todos(sender, guest, user, **kwargs):
    TodoService.transfer(guest, user)
