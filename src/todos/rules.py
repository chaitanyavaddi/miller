import rules

from todos.models import Todo


@rules.predicate
def is_todo_owner(user, todo):
    return todo is not None and todo.user_id == user.id


@rules.predicate
def is_task(user, todo):
    """A meeting belongs to its calendar: it can be read and commented on, never edited here."""
    return todo is not None and todo.kind == Todo.Kind.TASK


rules.add_perm("todos.view_todo",    is_todo_owner)
rules.add_perm("todos.change_todo",  is_todo_owner & is_task)
rules.add_perm("todos.delete_todo",  is_todo_owner & is_task)
rules.add_perm("todos.comment_todo", is_todo_owner)
