from django.dispatch import Signal

# Sent while a guest is folded into an existing account, before the guest row is deleted.
# Each domain that owns per-user data moves it: receivers get `guest` and `user`.
guest_merged = Signal()
