import rules


@rules.predicate
def is_attachment_owner(user, attachment):
    return attachment is not None and attachment.comment.todo.user_id == user.id


rules.add_perm("comments.view_attachment", is_attachment_owner)
