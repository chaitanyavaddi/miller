import rules


@rules.predicate
def is_member(user):
    """Signed in with a real account: a guest's list lives in one browser, so no calendars."""
    return user.is_authenticated and not user.is_guest


@rules.predicate
def is_connection_owner(user, connection):
    return connection is not None and connection.user_id == user.id


@rules.predicate
def is_source_owner(user, source):
    return source is not None and source.connection.user_id == user.id


rules.add_perm("calendars.connect_calendar",  is_member)
rules.add_perm("calendars.delete_connection", is_connection_owner)
rules.add_perm("calendars.change_source",     is_source_owner)
