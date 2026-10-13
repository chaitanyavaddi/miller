from dataclasses import dataclass, field
from decimal import Decimal

TITLE_MAX = 300
NOTE_MAX = 2000
MAX_OPERATIONS = 8


class Op:
    CREATE     = "create"
    RENAME     = "rename"
    CLOSE      = "close"
    ADD_UPDATE = "add_update"
    MOVE       = "move"
    REOPEN     = "reopen"          # a done todo, not done again
    OPEN       = "open"            # go somewhere on screen
    EDIT_UPDATE   = "edit_update"      # change a note on a thread
    DELETE_UPDATE = "delete_update"    # remove one
    END        = "end"             # they're finished: close voice mode
    ALL = (CREATE, RENAME, CLOSE, ADD_UPDATE, MOVE, REOPEN, OPEN, EDIT_UPDATE, DELETE_UPDATE, END)


class Target:
    """Where `open` goes: a todo's thread, or one of the panels."""
    TODO, ALL_TODOS, SETTINGS, USAGE, CLOSE_PANEL = "todo", "all_todos", "settings", "usage", "close_panel"
    ALL = (TODO, ALL_TODOS, SETTINGS, USAGE, CLOSE_PANEL)


class Place:
    """Where a todo is moved to: an end of the list, next to another todo, or an exact number."""
    TOP, BOTTOM, BEFORE, AFTER, POSITION = "top", "bottom", "before", "after", "position"
    ALL = (TOP, BOTTOM, BEFORE, AFTER, POSITION)


@dataclass
class Operation:
    op: str
    todo_id: int | None = None
    text: str | None = None
    place: str | None = None       # move only: a Place
    anchor_id: int | None = None   # move only: the todo it goes before or after
    position: int | None = None    # move only: the number it ends up at, as the list shows it
    comment_id: int | None = None  # edit_update and delete_update: the note
    target: str | None = None      # open only: a Target

    def as_dict(self) -> dict:
        data = {"op": self.op, "todo_id": self.todo_id, "text": self.text}
        if self.op == Op.MOVE:
            data.update(place=self.place, anchor_id=self.anchor_id, position=self.position)
        elif self.op in (Op.EDIT_UPDATE, Op.DELETE_UPDATE):
            data["comment_id"] = self.comment_id
        elif self.op == Op.OPEN:
            data["target"] = self.target
        return data


@dataclass
class Decision:
    """What the model made of a command: operations to do, or a line to say back (a question, usually),
    and what the answer cost."""
    operations: list[Operation] = field(default_factory=list)
    reply: str = ""
    model: str = ""
    cost: Decimal = Decimal(0)   # USD, as OpenRouter reports it
    tokens_in: int = 0
    tokens_out: int = 0
