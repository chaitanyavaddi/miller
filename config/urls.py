from django.urls import include, path

urlpatterns = [
    path("", include("board.urls")),
    path("", include("accounts.urls")),
    path("", include("comments.urls")),
]
