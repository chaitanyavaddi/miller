import json
import time
from decimal import Decimal
import urllib.error
import urllib.request

from django.conf import settings

from voice.schemas import Decision, Op, Operation, Place, Target

URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT = 20
RETRY_CODES = (429, 500, 502, 503, 529)
RETRY_AFTER = 0.7  # seconds

SYSTEM = """You turn what a person said into changes on their todo list. Reply only with the JSON asked for.

Operations:
- create: add a todo. todo_id null, text = a short clear title (their words, tidied; no "add a todo to").
- rename: change a todo's title. todo_id = that todo, text = the new title.
- close: mark a todo done. todo_id = that todo, text null.
- add_update: write a note on a todo's thread (a status, a fact, something that happened). todo_id = that todo, text = the note in their words.
- move: change where a todo sits in the list. todo_id = the todo to move, and place says where: "top" or "bottom" (the ends), "before" or "after" (then anchor_id = the todo it goes just before or after), or "position" (then position = the number it should end up at, as the list shows its numbers). Set the other move fields to null. "Put X above Y" is before Y; "below Y" is after Y; "first" is top; "last" is bottom.
- reopen: mark a done todo as not done again. todo_id = that todo: one marked done in the list, or one under "Done earlier". A todo done today is simply open again where it stands; an older one comes back at the end of the list.
- schedule: put a todo on another day, with a time of day if they gave one. For a todo already on the list: todo_id = it, text null. For a new one ("schedule a call with mum for Friday"): todo_id null, text = its title. date = the day as YYYY-MM-DD, worked out from today's date (given below): "tomorrow", "Friday" (the next one), "next Monday", "in three days", "the 20th" (the next 20th). time = HH:MM in 24 hours when they said a time ("3pm" is 15:00, "half past nine" 09:30), else null. With no time the todo simply comes onto the list that day; with a time it also gets a reminder (a bell) at that time. Today's date with a time on a todo already listed sets just the reminder ("remind me about it at 4 today"). A todo that is done can be scheduled or reminded too: it is reopened first.
- open: take them somewhere on screen. target = "todo" (then todo_id = that todo: its thread opens), "all_todos" (the panel of every past and scheduled todo), "settings", "usage" (the settings page that shows what voice has cost: use it for any question about spending, cost, billing, balance or how much they have used) or "close_panel" (shut whatever panel is open). "Open", "show me", "go to", "pull up" all mean this.
- edit_update: change a note on a thread. comment_id = that note (its [number] under the todo), text = the complete new text of the note.
- delete_update: remove a note. comment_id = that note.
- end: they are finished and want voice mode closed: "thanks", "thank you", "that's all", "we're done", "goodbye", "stop", "close voice mode", "turn off voice", "stop listening". Only when it is the point of what they said. A "thanks" tacked onto a request is politeness: do the request and do not add end. ("Close this panel" or "close the window" is open with close_panel, not end.)

Rules:
- What they said was typed by speech recognition, so it may have errors: "Toto" for "todo", "Acme Wan" for "Acme one", a name spelled oddly. Read it for what they meant, and match the closest todo when it is reasonably clear. Ask only when two or more todos fit equally well.
- Use only the todo ids listed. Match by meaning ("the acme one" is "Acme follow-up"). Never invent an id.
- Meetings can only get add_update, edit_update, delete_update and open. Closing, renaming, moving or reopening a meeting is not possible, though a task can be moved next to one.
- A done todo that is still in the list (marked "done") can be moved like any other. Todos under "Done earlier" are not in the order: they can only be reopened (and then moved).
- Notes are listed under their todo, newest first, as [number] "text". Use only those numbers. "The thread open on screen now" is where "this one", "my last note", "delete that comment" point when they don't name a todo. If several notes could be meant and nothing settles it, ask which instead of guessing.
- One spoken command can hold several operations, in the order said.
- If it is unclear which todo they mean, or there is nothing to do, return no operations and put one short question or remark in "reply" (under 15 words, plain, no greeting).
- When you return operations, "reply" is an empty string.

This is one running conversation, and "Conversation so far" shows what was said and done in it. Read each new sentence as a continuation of it:
- "it", "that one", "the same", "also", "and", "actually", "instead", "make it…", "no, …" refer to what was just done or asked. A follow-up that adds detail to a todo just created or touched (a day, a time, a person, a number, a correction) is a rename or add_update on THAT todo, never a new todo.
- If you asked a question, their next sentence answers it: apply the answer to what you asked about.
- Create a todo only when they clearly name a new thing to do.
- Todos made earlier in the conversation are on the list (see their ids); use those ids."""

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["reply", "operations"],
    "properties": {
        "reply": {"type": "string"},
        "operations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["op", "todo_id", "text", "place", "anchor_id", "position", "comment_id", "target", "date", "time"],
            "properties": {
                "op": {"type": "string", "enum": list(Op.ALL)},
                "todo_id": {"type": ["integer", "null"]},
                "text": {"type": ["string", "null"]},
                "place": {"anyOf": [{"type": "string", "enum": list(Place.ALL)}, {"type": "null"}]},
                "anchor_id": {"type": ["integer", "null"]},
                "position": {"type": ["integer", "null"]},
                "comment_id": {"type": ["integer", "null"]},
                "target": {"anyOf": [{"type": "string", "enum": list(Target.ALL)}, {"type": "null"}]},
                "date": {"type": ["string", "null"]},
                "time": {"type": ["string", "null"]},
            },
        }},
    },
}


def whole(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class DecisionError(Exception):
    pass


class DecisionService:
    @staticmethod
    def is_configured() -> bool:
        return bool(settings.OPENROUTER_API_KEY)

    @staticmethod
    def build_body(transcript: str, todos: list, history: list[dict] | None, model: str,
                   notes: dict | None = None, open_id: int | None = None, done_earlier: list | None = None, today=None) -> dict:
        def line(number, t):
            row = f'#{t.id} [{"meeting" if t.is_meeting else "task"}{", done" if t.closed_at else ""}] (number {number}) {t.title}'
            written = (notes or {}).get(t.id)
            return row + ("\n    notes: " + " · ".join(f'[{c_id}] "{body}"' for c_id, body in written) if written else "")
        listing = "\n".join(line(number, t) for number, t in enumerate(todos, 1)) or "(the list is empty)"
        if done_earlier:
            listing += "\n\nDone earlier (only reopen or schedule apply):\n" + "\n".join(f"#{t.id} {t.title}" for t in done_earlier)
        if today:
            listing = f"Today is {today:%A} {today.isoformat()}.\n\n" + listing
        opened = next((t for t in todos if t.id == open_id), None)
        if opened:
            listing += f'\n\nThe thread open on screen now: #{opened.id} "{opened.title}"'
        earlier = "".join(f'They said: "{turn["said"]}"\nYou: {turn["did"]}\n' for turn in history or [])
        conversation = f"Conversation so far:\n{earlier}\n" if earlier else ""
        return {
            "model": model, "max_tokens": 600,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Todo list:\n{listing}\n\n{conversation}They now say: \"{transcript}\""},
            ],
            "response_format": {"type": "json_schema", "json_schema": {"name": "todo_changes", "strict": True, "schema": SCHEMA}},
            "provider": {"require_parameters": True},
        }

    @staticmethod
    def decide(transcript: str, todos: list, history: list[dict] | None = None, model: str = "",
               notes: dict | None = None, open_id: int | None = None, done_earlier: list | None = None, today=None) -> tuple[Decision, int]:
        """What to do about `transcript` given the user's list (with each todo's latest notes, and the thread
        open on screen) and the conversation so far (a list of {"said", "did"}, oldest first), and how long
        the model took (ms)."""
        model = model or settings.VOICE_DECISION_MODEL
        body = DecisionService.build_body(transcript, todos, history, model, notes, open_id, done_earlier, today)
        request = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST", headers={
            "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        })
        started = time.monotonic()
        for attempt in (1, 2):
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                    result = json.loads(response.read())
                data = json.loads(result["choices"][0]["message"]["content"])
                break
            except urllib.error.HTTPError as error:
                if attempt == 1 and error.code in RETRY_CODES:   # a busy moment upstream: one more try
                    time.sleep(RETRY_AFTER)
                    continue
                raise DecisionError(error.read().decode(errors="replace")[:300]) from error
            except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
                raise DecisionError(str(error)) from error
        decision = DecisionService.parse(data, model)
        decision.cost, decision.tokens_in, decision.tokens_out = DecisionService.usage(result)
        return decision, int((time.monotonic() - started) * 1000)

    @staticmethod
    def usage(result: dict) -> tuple[Decimal, int, int]:
        """What the call cost (USD, as OpenRouter reports it in `usage.cost`) and its tokens."""
        usage = result.get("usage") if isinstance(result, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        try:
            cost = Decimal(str(usage.get("cost") or 0))
        except ArithmeticError:
            cost = Decimal(0)
        return cost, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)

    @staticmethod
    def parse(data, model: str = "") -> Decision:
        """The model's JSON as a Decision; anything off-shape is dropped, as validation would anyway."""
        if not isinstance(data, dict):
            raise DecisionError("not an object")
        operations = []
        for raw in data.get("operations") or []:
            if not isinstance(raw, dict) or raw.get("op") not in Op.ALL:
                continue
            text = raw.get("text")
            place, target = raw.get("place"), raw.get("target")
            operations.append(Operation(
                op=raw["op"], todo_id=whole(raw.get("todo_id")), text=text.strip() if isinstance(text, str) else None,
                place=place if place in Place.ALL else None, anchor_id=whole(raw.get("anchor_id")), position=whole(raw.get("position")),
                comment_id=whole(raw.get("comment_id")), target=target if target in Target.ALL else None,
                date=raw.get("date") if isinstance(raw.get("date"), str) else None, time=raw.get("time") if isinstance(raw.get("time"), str) else None,
            ))
        reply = data.get("reply")
        return Decision(operations=operations, reply=reply.strip() if isinstance(reply, str) else "", model=model)
