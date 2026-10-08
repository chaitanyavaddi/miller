from django.urls import path

from comments.views import AttachmentView

app_name = "comments"

urlpatterns = [
    path("attachments/<int:pk>/", AttachmentView.as_view(), name="attachment"),
]
