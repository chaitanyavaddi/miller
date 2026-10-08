from django.urls import path

from accounts.views import GoogleCallbackView, GoogleStartView, LoginView, LogoutView

app_name = "accounts"

urlpatterns = [
    path("login/",                LoginView.as_view(),          name="login"),
    path("logout/",               LogoutView.as_view(),         name="logout"),
    path("auth/google/",          GoogleStartView.as_view(),    name="google-start"),
    path("auth/google/callback/", GoogleCallbackView.as_view(), name="google-callback"),
]
