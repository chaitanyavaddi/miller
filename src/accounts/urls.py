from django.urls import path

from accounts.views import CodeView, EmailStartView, GoogleCallbackView, GoogleStartView, LoginView, LogoutView, ResendCodeView

app_name = "accounts"

urlpatterns = [
    path("login/",                LoginView.as_view(),          name="login"),
    path("login/email/",          EmailStartView.as_view(),     name="email"),
    path("login/code/",           CodeView.as_view(),           name="code"),
    path("login/code/resend/",    ResendCodeView.as_view(),     name="resend-code"),
    path("logout/",               LogoutView.as_view(),         name="logout"),
    path("auth/google/",          GoogleStartView.as_view(),    name="google-start"),
    path("auth/google/callback/", GoogleCallbackView.as_view(), name="google-callback"),
]
