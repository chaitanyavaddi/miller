import rules


@rules.predicate
def is_todo_owner(user, todo):
    return todo is not None and todo.user_id == user.id


rules.add_perm("todos.view_todo",   is_todo_owner)
rules.add_perm("todos.change_todo", is_todo_owner)
rules.add_perm("todos.delete_todo", is_todo_owner)
