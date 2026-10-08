from django.contrib.auth.mixins import LoginRequiredMixin

from accounts.services import GuestService
from utils.view_helper import redirect_to


class SignedInRequiredMixin(LoginRequiredMixin):
    """Every visitor has an account, a guest one at least. A request without one goes to the board,
    which starts a guest session."""

    def handle_no_permission(self):
        return redirect_to(self.request, "board:index")


class GuestSessionMixin:
    """Lets anyone in: a visitor without an account gets a guest one, keyed by the session cookie."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            GuestService.start_session(request)
        return super().dispatch(request, *args, **kwargs)
