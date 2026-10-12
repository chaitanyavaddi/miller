import rules


@rules.predicate
def is_recording_owner(user, recording):
    return recording is not None and recording.user_id == user.id


rules.add_perm("recordings.change_recording", is_recording_owner)
