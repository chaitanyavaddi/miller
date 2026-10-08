from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie

from accounts.mixins import GuestSessionMixin
from board.mixins import BoardResponseMixin
from board.services import BoardService


@method_decorator(ensure_csrf_cookie, name="dispatch")
class BoardView(GuestSessionMixin, BoardResponseMixin, View):
    def get(self, request):
        return render(request, "board/index.html", BoardService.workspace(request.user, self.selected_id()))
