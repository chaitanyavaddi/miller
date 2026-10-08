from django.urls import path

from board.views import (
    BoardView,
    CommentCreateView,
    ReviewDeleteView,
    ReviewThreadView,
    ReviewView,
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
]
