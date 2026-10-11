from django.urls import path

from board.views import (
    BoardView,
    BoardWorkspaceView,
    CommentCreateView,
    ReviewDeleteView,
    ReviewThreadView,
    ReviewView,
    SettingsCalendarDisconnectView,
    SettingsMeetingsView,
    SettingsTimezoneView,
    SettingsView,
    TodoCloseView,
    TodoCreateView,
    TodoDeleteView,
    TodoDetailView,
    TodoListView,
    TodoRenameView,
    TodoReopenView,
    TodoReorderView,
)

app_name = "board"

urlpatterns = [
    path("",                          BoardView.as_view(),         name="index"),
    path("board/",                    BoardWorkspaceView.as_view(), name="workspace"),
    path("todos/",                    TodoListView.as_view(),      name="todo-list"),
    path("todos/new/",                TodoCreateView.as_view(),    name="todo-create"),
    path("todos/reorder/",            TodoReorderView.as_view(),   name="todo-reorder"),
    path("todos/<int:pk>/",           TodoDetailView.as_view(),    name="todo-detail"),
    path("todos/<int:pk>/rename/",    TodoRenameView.as_view(),    name="todo-rename"),
    path("todos/<int:pk>/close/",     TodoCloseView.as_view(),     name="todo-close"),
    path("todos/<int:pk>/delete/",    TodoDeleteView.as_view(),    name="todo-delete"),
    path("todos/<int:pk>/reopen/",    TodoReopenView.as_view(),    name="todo-reopen"),
    path("todos/<int:pk>/comments/new/", CommentCreateView.as_view(), name="comment-create"),
    path("review/",                   ReviewView.as_view(),        name="review"),
    path("review/<int:pk>/",          ReviewThreadView.as_view(),  name="review-thread"),
    path("review/<int:pk>/delete/",   ReviewDeleteView.as_view(),  name="review-delete"),
    path("settings/",                 SettingsView.as_view(),      name="settings"),
    path("settings/timezone/",        SettingsTimezoneView.as_view(), name="settings-timezone"),
    path("settings/meetings/",        SettingsMeetingsView.as_view(), name="settings-meetings"),
    path("settings/calendars/<int:pk>/disconnect/", SettingsCalendarDisconnectView.as_view(), name="settings-disconnect"),
]
