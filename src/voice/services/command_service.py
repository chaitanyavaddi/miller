from dataclasses import dataclass, field
from datetime import date, time, timedelta
from types import SimpleNamespace

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from comments.models import Comment
from comments.service import CommentService
from scheduled.models import ScheduledItem
from scheduled.rhythm import clock_label, once_limit
from scheduled.services import ScheduleService
from todos.models import Todo
from todos.schemas import TITLE_MAX_LENGTH, TodoReopenSchema, TodoTitleSchema
from todos.services import TodoService
from voice.models import VoiceCommand
from voice.schemas import MAX_OPERATIONS, NOTE_MAX, Op, Operation, Place, Target


CONVERSATION_MINUTES = 30
TURNS_KEPT = 10
NOTES_PER_TODO = 3      # the latest notes the model sees under each todo, so it can find "my last note"
NOTES_IN_ALL = 120
EARLIER_DONE = 10       # the done list Miller shows below the todos: the same ones, so "reopen that one" means what the screen shows

OPENED = {Target.ALL_TODOS: "All todos", Target.SETTINGS: "Settings", Target.USAGE: "your voice usage"}


def quote(title: str) -> str:
    return f"“{title if len(title) <= 40 else title[:39].rstrip() + '…'}”"


@dataclass
class Applied:
    """What a command did: a line per change for the user, and how to take each back."""
    lines: list[str] = field(default_factory=list)
    done: list[dict] = field(default_factory=list)
    undo: list[dict] = field(default_factory=list)
    touched: list[int] = field(default_factory=list)   # rows the page should light up as they change
    show: int | None = None                            # the todo whose thread the page should open
    read: bool = False                                 # a note was written, changed or removed: on a phone, bring the thread forward
    go: dict | None = None                             # somewhere to take the page: {"to": "todo", "id": …}, {"to": "settings"}, {"to": "end"}…


class CommandService:
    @staticmethod
    def todos_for(user) -> list[Todo]:
        """What the model may see and touch: the user's list as it stands today, tasks and meetings."""
        return list(TodoService.ordered_for(user))

    @staticmethod
    def done_earlier(user) -> list[Todo]:
        """Todos done before today, as the screen's done list shows them: not in the order, but reopenable."""
        return list(TodoService.recently_closed(user, EARLIER_DONE))

    @staticmethod
    def history(user, session: str) -> list[dict]:
        """This conversation so far, oldest first: what was said and what it came to. A conversation is one
        run of voice mode; one that went quiet for half an hour starts over."""
        if not session:
            return []
        since = timezone.now() - timedelta(minutes=CONVERSATION_MINUTES)
        recent = VoiceCommand.objects.filter(user=user, session=session, created_at__gte=since).exclude(transcript="")[:TURNS_KEPT]
        return [{"said": c.transcript[:300], "did": c.digest()} for c in reversed(list(recent))]

    @staticmethod
    def notes_for(todos: list[Todo]) -> dict[int, list[tuple[int, str]]]:
        """Each todo's latest few notes (the ones a person wrote), newest first, for the model to point at."""
        rows = (Comment.objects.filter(todo_id__in=[t.id for t in todos], automatic=False).exclude(body="")
                .order_by("-created_at", "-id").values_list("todo_id", "id", "body")[:NOTES_IN_ALL * 3])
        notes, count = {}, 0
        for todo_id, comment_id, body in rows:
            bucket = notes.setdefault(todo_id, [])
            if len(bucket) < NOTES_PER_TODO and count < NOTES_IN_ALL:
                bucket.append((comment_id, " ".join(body.split())[:90]))
                count += 1
        return notes

    @staticmethod
    def vocabulary(todos: list[Todo]) -> str:
        """The titles on the list, for the speech recogniser to spell names by (it takes about 220 tokens)."""
        return ("Todo list: " + "; ".join(t.title for t in todos))[:600]

    @staticmethod
    def validate(todos: list[Todo], operations: list[Operation], done_earlier: list[Todo] | None = None) -> tuple[list[Operation], list[str]]:
        """Only operations on real todos, of the right kind, with sane text, and no more than the cap.
        Returns what passes and a note on each that doesn't."""
        by_id = {t.id: t for t in todos}
        earlier = {t.id: t for t in done_earlier or []}
        wanted = {o.comment_id for o in operations if o.op in (Op.EDIT_UPDATE, Op.DELETE_UPDATE) and o.comment_id}
        notes = {c.id: c for c in Comment.objects.filter(pk__in=wanted, todo_id__in=by_id, automatic=False)} if wanted else {}
        valid, problems, closing, ended = [], [], set(), False
        for operation in operations:
            if len(valid) >= MAX_OPERATIONS:
                problems.append("too many changes at once")
                break
            text = (operation.text or "").strip()
            todo = by_id.get(operation.todo_id) if operation.todo_id is not None else None
            if operation.op == Op.CREATE:
                if not text or len(text) > TITLE_MAX_LENGTH:
                    problems.append("a todo needs a title under 300 characters")
                    continue
            elif operation.op == Op.REOPEN:
                todo = by_id.get(operation.todo_id) or earlier.get(operation.todo_id)
                if todo is None or todo.is_meeting or not todo.closed_at:
                    problems.append("a done todo to reopen")
                    continue
            elif operation.op == Op.SCHEDULE:
                reason = CommandService._schedule_problem(operation, todo, text)
                if reason:
                    problems.append(reason)
                    continue
            elif operation.op == Op.END:
                if ended:
                    continue
                ended = True
            elif operation.op == Op.OPEN:
                if operation.target not in Target.ALL or (operation.target == Target.TODO and todo is None):
                    problems.append("somewhere to open")
                    continue
            elif operation.op in (Op.EDIT_UPDATE, Op.DELETE_UPDATE):
                note = notes.get(operation.comment_id)
                if note is None:
                    problems.append("a note that isn't there")
                    continue
                if operation.op == Op.EDIT_UPDATE and (not text or len(text) > NOTE_MAX):
                    problems.append("a note that isn't empty or too long")
                    continue
                if operation.op == Op.DELETE_UPDATE and note.attachments.exists():
                    problems.append("a note with files: delete it by hand")
                    continue
                todo = by_id[note.todo_id]
            elif todo is None:
                problems.append("a todo that isn't on the list")
                continue
            elif operation.op in (Op.RENAME, Op.CLOSE):
                if todo.is_meeting or todo.closed_at:
                    problems.append(f"{quote(todo.title)} can't be changed that way")
                    continue
                if operation.op == Op.CLOSE and todo.id in closing:
                    continue
                if operation.op == Op.RENAME and (not text or len(text) > TITLE_MAX_LENGTH):
                    problems.append("a title under 300 characters")
                    continue
            elif operation.op == Op.ADD_UPDATE:
                if not text or len(text) > NOTE_MAX:
                    problems.append("an update that isn't empty or too long")
                    continue
            elif operation.op == Op.MOVE:
                reason = CommandService._move_problem(operation, todo, by_id)
                if reason:
                    problems.append(reason)
                    continue
            else:
                continue
            if operation.op == Op.CLOSE:
                closing.add(todo.id)
            valid.append(Operation(operation.op, todo.id if todo else None, text or None, operation.place,
                                   operation.anchor_id, operation.position, operation.comment_id, operation.target, operation.date, operation.time))
        return valid, problems

    @staticmethod
    def _when(operation: Operation) -> tuple[date | None, time | None, bool]:
        """The day and time an operation names, and whether they could be read (a time is optional)."""
        try:
            day = date.fromisoformat(operation.date or "")
            at = time.fromisoformat(operation.time).replace(second=0, microsecond=0, tzinfo=None) if operation.time else None
        except ValueError:
            return None, None, False
        return day, at, True

    @staticmethod
    def _schedule_problem(operation: Operation, todo: Todo | None, text: str) -> str:
        """Why a todo can't be put on that day (and time), or "" if it can."""
        day, at, readable = CommandService._when(operation)
        if not readable or day is None:
            return "a day and a time I could read"
        today = timezone.localdate()
        if operation.todo_id is not None:
            if todo is None:
                return "a todo that isn't on the list"
            if todo.closed_at:
                return f"{quote(todo.title)} is done: reopen it first"
            if todo.is_meeting:
                return f"{quote(todo.title)} can't be scheduled"
        elif not text or len(text) > TITLE_MAX_LENGTH:
            return "a todo needs a title under 300 characters"
        if day < today:
            return "that day has already passed"
        if day > once_limit(today):
            return "a day within the next year"
        if day == today:
            if todo is not None and at is None:
                return f"{quote(todo.title)} is already on today's list"
            if at is not None and ScheduleService.moment(day, at) <= timezone.now():
                return "that time has already passed"
        return ""

    @staticmethod
    def _schedule(user, operation: Operation, result: "Applied") -> Todo | None:
        """Put a todo on a day (and time): an existing one moves there and returns that day, with a reminder if it has a
        time; today with a time is just a reminder; a new one is scheduled (or, for today, simply added)."""
        day, at, _ = CommandService._when(operation)
        today, when = timezone.localdate(), ScheduleService.when_words(date.fromisoformat(operation.date), at)
        if operation.todo_id is not None:
            todo = Todo.objects.get(pk=operation.todo_id, user=user)
            previous = todo.notify_at.isoformat() if todo.notify_at else None
            if day == today:   # already here: only the reminder
                Todo.objects.filter(pk=todo.pk).update(notify_at=ScheduleService.moment(day, at))
                result.lines.append(f"Reminder set for {quote(todo.title)} at {clock_label(at)}")
                result.undo.append({"op": "notify", "id": todo.id, "at": previous})
            else:
                ScheduleService.move(todo, day, at)
                result.lines.append(f"Moved {quote(todo.title)} to {when}")
                result.undo.append({"op": "unmove", "id": todo.id, "notify_at": previous})
            return todo
        if day == today:
            todo = TodoService.create(user, TodoTitleSchema(title=operation.text))
            if at is not None:
                Todo.objects.filter(pk=todo.pk).update(notify_at=ScheduleService.moment(day, at))
            result.lines.append(f"Added {quote(todo.title)}" + (f" · reminder {clock_label(at)}" if at else ""))
            result.undo.append({"op": "delete", "id": todo.id})
            return todo
        item = ScheduledItem.objects.create(user=user, title=operation.text, rhythm=ScheduledItem.Rhythm.ONCE, on_date=day, at_time=at)
        result.lines.append(f"Scheduled {quote(operation.text)} for {when}")
        result.undo.append({"op": "delete_scheduled", "id": item.id})
        return None

    @staticmethod
    def _move_problem(operation: Operation, todo: Todo, by_id: dict) -> str:
        """Why a move can't be done, or "" if it can: a task (done today or not), put somewhere that exists."""
        if todo.is_meeting:
            return f"{quote(todo.title)} can't be moved"
        if operation.place in (Place.BEFORE, Place.AFTER):
            if operation.anchor_id not in by_id or operation.anchor_id == todo.id:
                return "a todo to put it next to"
        elif operation.place == Place.POSITION:
            if operation.position is None or operation.position < 1:
                return "a place in the list"
        elif operation.place not in (Place.TOP, Place.BOTTOM):
            return "somewhere to put it"
        return ""

    @staticmethod
    @transaction.atomic
    def apply(user, operations: list[Operation]) -> Applied:
        """Do each operation through the same services the screen uses, so positions and Undo stay right."""
        result = Applied()
        for operation in operations:
            todo, extra = None, {}
            if operation.op == Op.CREATE:
                todo = TodoService.create(user, TodoTitleSchema(title=operation.text))
                result.lines.append(f"Added {quote(todo.title)}")
                result.undo.append({"op": "delete", "id": todo.id})
            elif operation.op == Op.END:
                result.lines.append("Closing voice mode")
                result.go = {"to": "end"}
            elif operation.op == Op.OPEN:
                if operation.target == Target.TODO:
                    opened = Todo.objects.get(pk=operation.todo_id, user=user)
                    result.lines.append(f"Opened {quote(opened.title)}")
                    result.go = {"to": Target.TODO, "id": opened.id}
                else:
                    result.lines.append("Closed the panel" if operation.target == Target.CLOSE_PANEL else f"Opened {OPENED[operation.target]}")
                    result.go = {"to": operation.target}
            elif operation.op == Op.SCHEDULE:
                todo = CommandService._schedule(user, operation, result)
            elif operation.op in (Op.EDIT_UPDATE, Op.DELETE_UPDATE):
                note = Comment.objects.select_related("todo").get(pk=operation.comment_id, todo__user=user)
                todo, extra = note.todo, {"comment_id": note.id}
                if operation.op == Op.EDIT_UPDATE:
                    result.undo.append({"op": "comment_body", "id": note.id, "body": note.body})
                    note.body = operation.text
                    note.save(update_fields=["body", "updated_at"])
                    result.lines.append(f"Updated a note on {quote(todo.title)}")
                else:
                    result.undo.append({"op": "restore_comment", "todo_id": todo.id, "body": note.body, "at": note.created_at.isoformat()})
                    note.delete()
                    result.lines.append(f"Deleted a note on {quote(todo.title)}")
                result.show, result.read = todo.id, True   # the thread opens on it, so the change is seen where it happened
            else:
                todo = Todo.objects.get(pk=operation.todo_id, user=user)
                if operation.op == Op.CLOSE:
                    removed = TodoService.close(todo)
                    result.lines.append(f"Closed {quote(todo.title)}")
                    result.undo.append({"op": "reopen", "id": todo.id, "index": removed.index})
                elif operation.op == Op.RENAME:
                    old = todo.title
                    TodoService.rename(todo, TodoTitleSchema(title=operation.text))
                    result.lines.append(f"Renamed {quote(old)} to {quote(operation.text)}")
                    result.undo.append({"op": "rename", "id": todo.id, "title": old})
                elif operation.op == Op.ADD_UPDATE:
                    comment = CommentService.add(todo, SimpleNamespace(body=operation.text, files=[]))
                    result.lines.append(f"Noted on {quote(todo.title)}")
                    result.undo.append({"op": "delete_comment", "id": comment.id})
                    result.show, result.read, extra = todo.id, True, {"comment_id": comment.id}   # the thread opens on it, the new note at the foot
                elif operation.op == Op.REOPEN:
                    was = todo.closed_at
                    TodoService.reopen(todo, TodoReopenSchema(index=TodoService.ordered_for(user).count() + 1, select=False))
                    result.lines.append(f"Reopened {quote(todo.title)}")
                    result.undo.append({"op": "restore_closed", "id": todo.id, "at": was.isoformat()})
                elif operation.op == Op.MOVE:
                    before, now = CommandService._move(user, todo, operation)
                    result.lines.append(f"Moved {quote(todo.title)} to #{now}")
                    result.undo.append({"op": "reorder", "ids": before})
                    result.show = todo.id   # as with a drag: the placed todo becomes the one you're reading
            if todo is not None and operation.op != Op.OPEN:
                result.touched.append(todo.id)
            # What was done, with the ids it made or used (a created todo's, a note's): the next command may mean them.
            result.done.append({**operation.as_dict(), **({"todo_id": todo.id} if todo is not None else {}), **extra})
        result.undo.reverse()
        return result

    @staticmethod
    def _move(user, todo: Todo, operation: Operation) -> tuple[list[int], int]:
        """Put `todo` where the operation says, in the user's whole order (todos and meetings, done ones too,
        as the list numbers them). Returns the order before, for Undo, and the todo's new number."""
        before = list(TodoService.ordered_for(user).values_list("id", flat=True))
        order = [i for i in before if i != todo.id]
        if operation.place == Place.TOP:
            index = 0
        elif operation.place == Place.BOTTOM:
            index = len(order)
        elif operation.place == Place.POSITION:
            index = min(max(operation.position - 1, 0), len(order))
        else:
            index = order.index(operation.anchor_id) + (1 if operation.place == Place.AFTER else 0)
        order.insert(index, todo.id)
        TodoService.reorder(user, SimpleNamespace(ids=order))
        return before, index + 1

    @staticmethod
    @transaction.atomic
    def undo(command: VoiceCommand) -> bool:
        """Put back what a command did, newest change first. False if it was already undone."""
        if command.undone_at or not command.undo:
            return False
        user = command.user
        for step in command.undo:
            kind = step["op"]
            if kind == "delete_comment":
                Comment.objects.filter(pk=step["id"], todo__user=user).delete()
                continue
            if kind == "reorder":
                TodoService.reorder(user, SimpleNamespace(ids=step["ids"]))
                continue
            if kind == "delete_scheduled":
                ScheduledItem.objects.filter(pk=step["id"], user=user).delete()
                continue
            if kind == "comment_body":
                Comment.objects.filter(pk=step["id"], todo__user=user).update(body=step["body"], updated_at=timezone.now())
                continue
            if kind == "restore_comment":
                todo = Todo.objects.filter(pk=step["todo_id"], user=user).first()
                if todo is not None:
                    restored = Comment.objects.create(todo=todo, body=step["body"])
                    Comment.objects.filter(pk=restored.pk).update(created_at=step["at"])   # back where it was in the thread
                continue
            todo = Todo.objects.filter(pk=step["id"], user=user).first()
            if todo is None:
                continue
            if kind == "delete":
                if todo.deleted_at is None:
                    TodoService.delete(todo)
            elif kind == "reopen":
                TodoService.reopen(todo, TodoReopenSchema(index=step["index"], select=False))
            elif kind == "restore_closed":
                TodoService.restore_closed(todo, parse_datetime(step["at"]))
            elif kind == "unmove":
                ScheduleService.unmove(todo)
                Todo.objects.filter(pk=todo.pk).update(notify_at=parse_datetime(step["notify_at"]) if step.get("notify_at") else None)
            elif kind == "notify":
                Todo.objects.filter(pk=todo.pk).update(notify_at=parse_datetime(step["at"]) if step.get("at") else None)
            elif kind == "rename":
                TodoService.rename(todo, TodoTitleSchema(title=step["title"]))
        command.undone_at = timezone.now()
        command.save(update_fields=["undone_at"])
        return True
