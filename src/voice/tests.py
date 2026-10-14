from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.services import GuestService, IdentityService
from comments.models import Attachment, Comment
from scheduled.models import ScheduledItem
from scheduled.services import ScheduleService
from todos.models import Todo
from todos.schemas import TodoTitleSchema
from todos.services import TodoService
from voice.models import VoiceCommand
from voice.schemas import Decision, Op, Operation, Target
from voice.services.command_service import CommandService
from voice.services.decision_service import DecisionService
from voice.services.voice_service import VoiceService


class VoiceBase(TestCase):
    def setUp(self):
        self.user = IdentityService.create_user("v@x.com")
        self.acme, self.invoice = (TodoService.create(self.user, TodoTitleSchema(title=t)) for t in ("Acme follow-up", "Send invoice"))

    def titles(self):
        return list(TodoService.open_for(self.user).values_list("title", flat=True))


class ValidateTests(VoiceBase):
    def validate(self, *operations):
        return CommandService.validate(CommandService.todos_for(self.user), list(operations))

    def test_real_ids_pass_and_invented_ones_are_dropped(self):
        valid, problems = self.validate(Operation(Op.CLOSE, self.acme.id), Operation(Op.CLOSE, 9999))
        self.assertEqual([o.todo_id for o in valid], [self.acme.id])
        self.assertEqual(len(problems), 1)

    def test_someone_elses_todo_is_not_on_the_list(self):
        other = IdentityService.create_user("o@x.com")
        theirs = TodoService.create(other, TodoTitleSchema(title="theirs"))
        valid, _ = self.validate(Operation(Op.CLOSE, theirs.id))
        self.assertEqual(valid, [])

    def test_a_meeting_can_be_noted_on_but_not_closed_or_renamed(self):
        from django.utils import timezone
        self.user.show_meetings = True
        self.user.save()
        meeting = Todo.objects.create(user=self.user, title="Standup", kind=Todo.Kind.MEETING, position=9,
                                      starts_at=timezone.now(), ends_at=timezone.now() + timezone.timedelta(hours=1))
        valid, problems = self.validate(Operation(Op.CLOSE, meeting.id), Operation(Op.RENAME, meeting.id, "x"),
                                        Operation(Op.ADD_UPDATE, meeting.id, "moved to 3"))
        self.assertEqual([o.op for o in valid], [Op.ADD_UPDATE])
        self.assertEqual(len(problems), 2)

    def test_empty_or_overlong_text_is_dropped(self):
        valid, _ = self.validate(Operation(Op.CREATE, None, ""), Operation(Op.CREATE, None, "x" * 301),
                                 Operation(Op.ADD_UPDATE, self.acme.id, " "), Operation(Op.RENAME, self.acme.id, ""))
        self.assertEqual(valid, [])

    def test_the_number_of_changes_is_capped(self):
        valid, problems = self.validate(*[Operation(Op.CREATE, None, f"t{i}") for i in range(12)])
        self.assertEqual(len(valid), 8)
        self.assertEqual(problems, ["too many changes at once"])


class ApplyAndUndoTests(VoiceBase):
    def run_ops(self, *operations):
        valid, _ = CommandService.validate(CommandService.todos_for(self.user), list(operations))
        applied = CommandService.apply(self.user, valid)
        return VoiceCommand.objects.create(user=self.user, applied=applied.done, undo=applied.undo), applied

    def test_each_operation_goes_through_the_todo_services(self):
        _, applied = self.run_ops(Operation(Op.CLOSE, self.acme.id), Operation(Op.CREATE, None, "Call the dentist"),
                                  Operation(Op.RENAME, self.invoice.id, "Send the invoice"), Operation(Op.ADD_UPDATE, self.invoice.id, "waiting on PO"))
        self.assertEqual(self.titles(), ["Send the invoice", "Call the dentist"])
        self.assertTrue(Todo.objects.get(pk=self.acme.id).closed_at)
        self.assertEqual(Comment.objects.get(todo=self.invoice).body, "waiting on PO")
        self.assertEqual(len(applied.lines), 4)

    def test_undo_puts_everything_back(self):
        command, _ = self.run_ops(Operation(Op.CLOSE, self.acme.id), Operation(Op.CREATE, None, "Call the dentist"),
                                  Operation(Op.RENAME, self.invoice.id, "Send the invoice"), Operation(Op.ADD_UPDATE, self.invoice.id, "waiting on PO"))
        self.assertTrue(CommandService.undo(command))
        self.assertEqual(self.titles(), ["Acme follow-up", "Send invoice"])
        self.assertFalse(Comment.objects.exists())
        self.assertFalse(CommandService.undo(command))  # only once


class RetryTests(TestCase):
    def response(self, body):
        from io import BytesIO
        import json as _json
        class Response(BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return Response(_json.dumps(body).encode())

    @override_settings(OPENROUTER_API_KEY="o")
    def test_a_busy_moment_upstream_is_tried_once_more_and_a_second_failure_is_an_error(self):
        import urllib.error
        from io import BytesIO
        busy = urllib.error.HTTPError("u", 429, "busy", {}, BytesIO(b"{}"))
        good = self.response({"choices": [{"message": {"content": '{"reply": "", "operations": []}'}}], "usage": {"cost": 0.0001}})
        with patch("voice.services.decision_service.urllib.request.urlopen", side_effect=[busy, good]) as call, patch("voice.services.decision_service.time.sleep"):
            decision, _ = DecisionService.decide("hi", [])
        self.assertEqual((call.call_count, decision.cost), (2, Decimal("0.0001")))
        from voice.services.decision_service import DecisionError
        with patch("voice.services.decision_service.urllib.request.urlopen", side_effect=[busy, busy]), patch("voice.services.decision_service.time.sleep"):
            with self.assertRaises(DecisionError):
                DecisionService.decide("hi", [])


class ParseTests(TestCase):
    def test_off_shape_operations_are_dropped(self):
        decision = DecisionService.parse({"reply": " ", "operations": [
            {"op": "close", "todo_id": 3, "text": None}, {"op": "explode", "todo_id": 1, "text": None}, "junk",
            {"op": "create", "todo_id": True, "text": " Buy milk "}]})
        self.assertEqual([(o.op, o.todo_id, o.text) for o in decision.operations], [("close", 3, None), ("create", None, "Buy milk")])


class ConversationTests(VoiceBase):
    def command(self, said, session="s1", **fields):
        return VoiceCommand.objects.create(user=self.user, session=session, transcript=said, **fields)

    def test_history_is_this_conversation_oldest_first_with_what_each_came_to(self):
        self.command("add a todo to call the dentist", applied=[{"op": "create", "todo_id": 31, "text": "Call the dentist"}], outcome="applied")
        self.command("close the budget one", reply="Which budget todo?", outcome="asked")
        self.command("something else", session="other", outcome="nothing")
        history = CommandService.history(self.user, "s1")
        self.assertEqual(history, [
            {"said": "add a todo to call the dentist", "did": 'create #31 "Call the dentist"'},
            {"said": "close the budget one", "did": 'asked: "Which budget todo?"'},
        ])

    def test_history_forgets_a_conversation_that_went_quiet_and_never_crosses_users(self):
        old = self.command("long ago", outcome="nothing")
        VoiceCommand.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(minutes=45))
        other = IdentityService.create_user("o@x.com")
        VoiceCommand.objects.create(user=other, session="s1", transcript="theirs", outcome="nothing")
        self.assertEqual(CommandService.history(self.user, "s1"), [])
        self.assertEqual(CommandService.history(self.user, ""), [])

    def test_the_prompt_carries_the_conversation_and_the_list(self):
        todos = CommandService.todos_for(self.user)
        history = [{"said": "add call the dentist", "did": 'create #31 "Call the dentist"'}]
        body = DecisionService.build_body("make it Thursday at 3", todos, history, "m")
        prompt = body["messages"][1]["content"]
        self.assertIn('They said: "add call the dentist"\nYou: create #31 "Call the dentist"', prompt)
        self.assertIn(f"#{self.acme.id} [task] (number 1) Acme follow-up", prompt)
        self.assertIn(f"#{self.invoice.id} [task] (number 2) Send invoice", prompt)
        self.assertTrue(prompt.endswith('They now say: "make it Thursday at 3"'))
        self.assertNotIn("Conversation so far", DecisionService.build_body("hi", todos, [], "m")["messages"][1]["content"])

    def test_a_created_todos_id_is_recorded_for_the_next_command(self):
        valid, _ = CommandService.validate(CommandService.todos_for(self.user), [Operation(Op.CREATE, None, "Call the dentist")])
        applied = CommandService.apply(self.user, valid)
        self.assertEqual(applied.done[0]["todo_id"], Todo.objects.get(title="Call the dentist").id)


class CostTests(VoiceBase):
    def test_openrouters_reported_cost_and_tokens_are_read(self):
        result = {"usage": {"cost": 0.0002664, "prompt_tokens": 76, "completion_tokens": 203}}
        self.assertEqual(DecisionService.usage(result), (Decimal("0.0002664"), 76, 203))
        self.assertEqual(DecisionService.usage({}), (Decimal(0), 0, 0))
        self.assertEqual(DecisionService.usage({"usage": {"cost": None}}), (Decimal(0), 0, 0))

    def test_speech_is_priced_by_the_second_with_groqs_minimum(self):
        self.assertEqual(VoiceService.speech_cost(3), VoiceService.speech_cost(10))    # billed at least 10 seconds
        self.assertEqual(VoiceService.speech_cost(10), Decimal("0.000308"))             # 10 s at $0.111 an hour
        self.assertGreater(VoiceService.speech_cost(60), VoiceService.speech_cost(10))
        self.assertEqual(VoiceService.speech_cost(0), 0)

    def test_spend_adds_up_per_user(self):
        VoiceCommand.objects.create(user=self.user, llm_cost=Decimal("0.0003"), stt_cost=Decimal("0.0001"))
        VoiceCommand.objects.create(user=self.user, llm_cost=Decimal("0.0002"), stt_cost=Decimal("0.0001"))
        other = IdentityService.create_user("o@x.com")
        VoiceCommand.objects.create(user=other, llm_cost=Decimal("1"))
        self.assertEqual(VoiceService.spent(self.user), Decimal("0.0007"))

    def test_a_guest_and_a_member_each_have_their_own_dollar(self):
        guest = GuestService.create_guest()
        VoiceCommand.objects.create(user=guest, llm_cost=Decimal("0.99"))
        VoiceCommand.objects.create(user=self.user, llm_cost=Decimal("0.5"))
        self.assertFalse(VoiceService.over_budget(guest))
        self.assertFalse(VoiceService.over_budget(self.user))
        VoiceCommand.objects.create(user=guest, stt_cost=Decimal("0.01"))
        VoiceCommand.objects.create(user=self.user, llm_cost=Decimal("0.5"))
        self.assertTrue(VoiceService.over_budget(guest))
        self.assertTrue(VoiceService.over_budget(self.user))

    @override_settings(VOICE_MEMBER_BUDGET_USD="5", VOICE_GUEST_BUDGET_USD="0.25")
    def test_the_limits_are_settings(self):
        self.assertEqual(VoiceService.limit(self.user), Decimal("5"))
        self.assertEqual(VoiceService.limit(GuestService.create_guest()), Decimal("0.25"))

    def test_usage_reads_spent_against_the_limit(self):
        VoiceCommand.objects.create(user=self.user, llm_cost=Decimal("0.0003"), stt_cost=Decimal("0.0001"))
        VoiceCommand.objects.create(user=self.user, llm_cost=Decimal("0.25"))
        usage = VoiceService.usage(self.user)
        self.assertEqual((usage["spent"], usage["limit"], usage["left"], usage["percent"], usage["commands"], usage["full"]),
                         ("$0.2504", "$1.00", "$0.7496", 25, 2, False))
        self.assertEqual(VoiceService.usage(IdentityService.create_user("n@x.com"))["spent"], "$0.00")

    def test_tiny_amounts_keep_their_digits(self):
        from voice.services.voice_service import money
        self.assertEqual((money(Decimal("0.0124")), money(Decimal("0.5")), money(Decimal(0)), money(Decimal("1")), money(Decimal("0.995300"))),
                         ("$0.0124", "$0.50", "$0.00", "$1.00", "$0.9953"))

    def test_what_a_guest_spent_follows_them_when_they_sign_in(self):
        from accounts.signals import guest_merged
        guest = GuestService.create_guest()
        VoiceCommand.objects.create(user=guest, llm_cost=Decimal("0.2"))
        guest_merged.send(sender=type(guest), guest=guest, user=self.user)
        self.assertEqual(VoiceService.spent(self.user), Decimal("0.2"))


class MoveTests(VoiceBase):
    def setUp(self):
        super().setUp()
        self.flights, self.budget = (TodoService.create(self.user, TodoTitleSchema(title=t)) for t in ("Book flights", "Budget draft"))
        # the list: 1 Acme, 2 Send invoice, 3 Book flights, 4 Budget draft

    def order(self):
        return list(TodoService.ordered_for(self.user).values_list("title", flat=True))

    def move(self, todo, place, anchor=None, position=None):
        valid, problems = CommandService.validate(CommandService.todos_for(self.user), [Operation(Op.MOVE, todo.id, None, place, anchor.id if anchor else None, position)])
        return valid, problems

    def do(self, todo, place, anchor=None, position=None):
        valid, problems = self.move(todo, place, anchor, position)
        self.assertEqual(problems, [])
        return CommandService.apply(self.user, valid)

    def test_to_the_top_and_the_bottom(self):
        self.do(self.budget, "top")
        self.assertEqual(self.order(), ["Budget draft", "Acme follow-up", "Send invoice", "Book flights"])
        self.do(self.acme, "bottom")
        self.assertEqual(self.order(), ["Budget draft", "Send invoice", "Book flights", "Acme follow-up"])

    def test_above_or_below_another_todo_whichever_way_it_travels(self):
        self.do(self.budget, "before", self.invoice)    # up
        self.assertEqual(self.order(), ["Acme follow-up", "Budget draft", "Send invoice", "Book flights"])
        self.do(self.acme, "after", self.flights)       # down
        self.assertEqual(self.order(), ["Budget draft", "Send invoice", "Book flights", "Acme follow-up"])

    def test_to_a_number_as_the_list_shows_it_and_a_number_too_big_means_the_end(self):
        self.do(self.budget, "position", position=2)
        self.assertEqual(self.order(), ["Acme follow-up", "Budget draft", "Send invoice", "Book flights"])
        self.do(self.acme, "position", position=99)
        self.assertEqual(self.order()[-1], "Acme follow-up")

    def test_positions_are_renumbered_and_the_thread_and_row_are_flagged(self):
        applied = self.do(self.budget, "top")
        self.assertEqual(list(TodoService.ordered_for(self.user).values_list("position", flat=True)), [1, 2, 3, 4])
        self.assertEqual((applied.show, applied.touched, applied.lines), (self.budget.id, [self.budget.id], ["Moved “Budget draft” to #1"]))

    def test_undo_restores_the_order(self):
        applied = self.do(self.budget, "top")
        command = VoiceCommand.objects.create(user=self.user, applied=applied.done, undo=applied.undo)
        CommandService.undo(command)
        self.assertEqual(self.order(), ["Acme follow-up", "Send invoice", "Book flights", "Budget draft"])

    def test_bad_moves_are_dropped(self):
        for args in (("before", None), ("before", self.budget), ("position", None), (None, None), ("sideways", None)):
            valid, problems = self.move(self.budget, *args)
            self.assertEqual((valid, len(problems)), ([], 1), args)
        _, problems = self.move(self.budget, "position", position=0)
        self.assertEqual(len(problems), 1)
        self.assertEqual(self.move(self.acme, "top")[0][0].op, Op.MOVE)   # (a done todo can be moved: see DoneTodoTests)

    def test_a_meeting_is_not_moved_but_a_task_can_go_next_to_one(self):
        from django.utils import timezone
        self.user.show_meetings = True
        self.user.save()
        meeting = Todo.objects.create(user=self.user, title="Standup", kind=Todo.Kind.MEETING, position=9,
                                      starts_at=timezone.now(), ends_at=timezone.now() + timezone.timedelta(hours=1))
        self.assertEqual(self.move(meeting, "top")[0], [])
        valid, _ = self.move(self.budget, "after", meeting)
        self.assertEqual(len(valid), 1)

    def test_the_model_s_move_fields_are_read_and_junk_is_dropped(self):
        decision = DecisionService.parse({"reply": "", "operations": [
            {"op": "move", "todo_id": 4, "text": None, "place": "before", "anchor_id": 2, "position": None},
            {"op": "move", "todo_id": 4, "text": None, "place": "diagonal", "anchor_id": "x", "position": 1.5}]})
        first, second = decision.operations
        self.assertEqual((first.place, first.anchor_id, first.position), ("before", 2, None))
        self.assertEqual((second.place, second.anchor_id, second.position), (None, None, None))

    def test_a_move_is_written_into_the_next_prompts_history(self):
        command = VoiceCommand.objects.create(user=self.user, outcome="applied", transcript="put the invoice first",
            applied=[{"op": "move", "todo_id": 7, "text": None, "place": "top", "anchor_id": None, "position": None},
                     {"op": "move", "todo_id": 8, "text": None, "place": "before", "anchor_id": 3, "position": None},
                     {"op": "move", "todo_id": 9, "text": None, "place": "position", "anchor_id": None, "position": 4}])
        self.assertEqual(command.digest(), "move #7 to top; move #8 to before #3; move #9 to position 4")


class ShowTheThreadTests(VoiceBase):
    def test_a_note_opens_that_todos_thread(self):
        valid, _ = CommandService.validate(CommandService.todos_for(self.user), [Operation(Op.ADD_UPDATE, self.invoice.id, "waiting on PO")])
        applied = CommandService.apply(self.user, valid)
        self.assertEqual((applied.show, applied.touched), (self.invoice.id, [self.invoice.id]))

    def test_on_a_phone_a_note_brings_the_thread_forward_and_a_move_brings_the_list(self):
        for operation, thread_forward in ((Operation(Op.ADD_UPDATE, self.invoice.id, "waiting"), True),
                                          (Operation(Op.MOVE, self.invoice.id, None, "top"), False),
                                          (Operation(Op.CLOSE, self.acme.id), False)):
            valid, _ = CommandService.validate(CommandService.todos_for(self.user), [operation])
            self.assertEqual(CommandService.apply(self.user, valid).read, thread_forward, operation.op)

    def test_closing_or_renaming_lights_the_row_but_does_not_change_the_thread(self):
        valid, _ = CommandService.validate(CommandService.todos_for(self.user), [Operation(Op.CLOSE, self.acme.id), Operation(Op.CREATE, None, "New one")])
        applied = CommandService.apply(self.user, valid)
        self.assertIsNone(applied.show)
        self.assertEqual(len(applied.touched), 2)


class DoneTodoTests(VoiceBase):
    """Reopening done todos, and putting them in a new order."""

    def setUp(self):
        super().setUp()
        self.flights = TodoService.create(self.user, TodoTitleSchema(title="Book flights"))
        # an older one: closed yesterday, so it sits in the done list below the todos, not in the order
        self.old = TodoService.create(self.user, TodoTitleSchema(title="Check Dubai flights"))
        TodoService.close(self.old)
        self.yesterday = timezone.now() - timedelta(days=1)
        Todo.objects.filter(pk=self.old.pk).update(closed_at=self.yesterday)
        TodoService.close(self.acme)   # done today: still in its place

    def run_ops(self, *operations):
        valid, problems = CommandService.validate(CommandService.todos_for(self.user), list(operations), CommandService.done_earlier(self.user))
        return valid, problems, (CommandService.apply(self.user, valid) if valid else None)

    def order(self):
        return list(TodoService.ordered_for(self.user).values_list("title", flat=True))

    def test_a_todo_done_today_reopens_where_it_stands_and_undo_closes_it_again(self):
        before = self.order()
        _, problems, applied = self.run_ops(Operation(Op.REOPEN, self.acme.id))
        self.assertEqual((problems, applied.lines, applied.touched), ([], ["Reopened “Acme follow-up”"], [self.acme.id]))
        self.assertIsNone(Todo.objects.get(pk=self.acme.id).closed_at)
        self.assertEqual(self.order(), before)
        CommandService.undo(VoiceCommand.objects.create(user=self.user, applied=applied.done, undo=applied.undo))
        self.assertIsNotNone(Todo.objects.get(pk=self.acme.id).closed_at)

    def test_an_older_done_todo_comes_back_at_the_end_and_undo_sends_it_back_to_the_done_list(self):
        self.assertNotIn("Check Dubai flights", self.order())
        _, _, applied = self.run_ops(Operation(Op.REOPEN, self.old.id))
        self.assertEqual(self.order()[-1], "Check Dubai flights")
        self.assertEqual(list(TodoService.ordered_for(self.user).values_list("position", flat=True)), list(range(1, 5)))
        CommandService.undo(VoiceCommand.objects.create(user=self.user, applied=applied.done, undo=applied.undo))
        back = Todo.objects.get(pk=self.old.pk)
        self.assertEqual(back.closed_at, self.yesterday)
        self.assertNotIn("Check Dubai flights", self.order())
        self.assertEqual(list(TodoService.recently_closed(self.user, 10)), [back])

    def test_only_a_done_task_can_be_reopened(self):
        for operation in (Operation(Op.REOPEN, self.invoice.id), Operation(Op.REOPEN, 9999), Operation(Op.REOPEN)):
            valid, problems, _ = self.run_ops(operation)
            self.assertEqual((valid, len(problems)), ([], 1))

    def test_a_done_todo_in_the_list_can_be_moved_like_any_other(self):
        _, problems, applied = self.run_ops(Operation(Op.MOVE, self.acme.id, None, "bottom"))
        self.assertEqual(problems, [])
        self.assertEqual(self.order()[-1], "Acme follow-up")
        _, _, _ = self.run_ops(Operation(Op.MOVE, self.acme.id, None, "before", self.invoice.id))
        self.assertEqual(self.order()[:2], ["Acme follow-up", "Send invoice"])
        self.assertIsNotNone(Todo.objects.get(pk=self.acme.id).closed_at)   # still done: it only changed places

    def test_an_older_done_todo_is_not_in_the_order_so_it_cannot_be_moved_until_reopened(self):
        valid, problems, _ = self.run_ops(Operation(Op.MOVE, self.old.id, None, "top"))
        self.assertEqual((valid, len(problems)), ([], 1))

    def test_the_model_is_shown_the_done_list_and_which_in_the_order_are_done(self):
        todos = CommandService.todos_for(self.user)
        prompt = DecisionService.build_body("reopen it", todos, [], "m", done_earlier=CommandService.done_earlier(self.user))["messages"][1]["content"]
        self.assertIn(f"#{self.acme.id} [task, done] (number 1) Acme follow-up", prompt)
        self.assertIn(f"Done earlier (only reopen or schedule apply):\n#{self.old.id} Check Dubai flights", prompt)
        self.assertNotIn("Done earlier", DecisionService.build_body("hi", todos, [], "m")["messages"][1]["content"])


class NavigationTests(VoiceBase):
    def run_ops(self, *operations):
        valid, problems = CommandService.validate(CommandService.todos_for(self.user), list(operations))
        return valid, problems, (CommandService.apply(self.user, valid) if valid else None)

    def test_opening_a_todo_goes_to_its_thread_and_changes_nothing(self):
        valid, problems, applied = self.run_ops(Operation(Op.OPEN, self.invoice.id, target=Target.TODO))
        self.assertEqual(problems, [])
        self.assertEqual((applied.go, applied.lines, applied.undo, applied.touched), ({"to": "todo", "id": self.invoice.id}, ["Opened “Send invoice”"], [], []))

    def test_the_panels_open_by_name(self):
        for target, line in ((Target.ALL_TODOS, "Opened All todos"), (Target.SETTINGS, "Opened Settings"),
                             (Target.USAGE, "Opened your voice usage"), (Target.CLOSE_PANEL, "Closed the panel")):
            _, _, applied = self.run_ops(Operation(Op.OPEN, target=target))
            self.assertEqual((applied.go, applied.lines), ({"to": target}, [line]))

    def test_opening_nowhere_or_a_todo_that_isnt_there_is_dropped(self):
        for operation in (Operation(Op.OPEN, target="the moon"), Operation(Op.OPEN, target=None), Operation(Op.OPEN, 9999, target=Target.TODO), Operation(Op.OPEN, target=Target.TODO)):
            valid, problems, _ = self.run_ops(operation)
            self.assertEqual((valid, len(problems)), ([], 1))

    def test_saying_youre_done_ends_voice_mode_once(self):
        valid, _, applied = self.run_ops(Operation(Op.END), Operation(Op.END))
        self.assertEqual((len(valid), applied.go, applied.undo), (1, {"to": "end"}, []))

    def test_a_change_and_then_goodbye_does_both(self):
        _, _, applied = self.run_ops(Operation(Op.CLOSE, self.acme.id), Operation(Op.END))
        self.assertEqual((applied.go, len(applied.undo)), ({"to": "end"}, 1))

    def test_the_digest_remembers_where_it_went(self):
        _, _, applied = self.run_ops(Operation(Op.OPEN, self.invoice.id, target=Target.TODO), Operation(Op.OPEN, target=Target.SETTINGS))
        command = VoiceCommand.objects.create(user=self.user, outcome="applied", applied=applied.done)
        self.assertEqual(command.digest(), f"open #{self.invoice.id} todo; open settings")


class NoteTests(VoiceBase):
    def note(self, todo, body, **fields):
        return Comment.objects.create(todo=todo, body=body, **fields)

    def run_ops(self, *operations):
        valid, problems = CommandService.validate(CommandService.todos_for(self.user), list(operations))
        return valid, problems, (CommandService.apply(self.user, valid) if valid else None)

    def test_the_model_sees_each_todos_latest_notes_newest_first_and_never_miller_s_own(self):
        for n in range(5):
            self.note(self.invoice, f"note {n}")
        self.note(self.invoice, "From Scheduled", automatic=True)
        notes = CommandService.notes_for(CommandService.todos_for(self.user))
        self.assertEqual([body for _, body in notes[self.invoice.id]], ["note 4", "note 3", "note 2"])
        self.assertNotIn(self.acme.id, notes)

    def test_the_prompt_lists_notes_under_their_todo_and_names_the_open_thread(self):
        written = self.note(self.invoice, "Finance said Friday")
        todos = CommandService.todos_for(self.user)
        prompt = DecisionService.build_body("delete that comment", todos, [], "m", CommandService.notes_for(todos), self.invoice.id)["messages"][1]["content"]
        self.assertIn(f'#{self.invoice.id} [task] (number 2) Send invoice\n    notes: [{written.id}] "Finance said Friday"', prompt)
        self.assertIn(f'The thread open on screen now: #{self.invoice.id} "Send invoice"', prompt)

    def test_editing_a_note_changes_its_words_opens_the_thread_and_can_be_undone(self):
        written = self.note(self.invoice, "pays Friday")
        _, problems, applied = self.run_ops(Operation(Op.EDIT_UPDATE, None, "pays Monday", comment_id=written.id))
        self.assertEqual(problems, [])
        self.assertEqual(Comment.objects.get(pk=written.id).body, "pays Monday")
        self.assertEqual((applied.show, applied.touched, applied.lines), (self.invoice.id, [self.invoice.id], ["Updated a note on “Send invoice”"]))
        CommandService.undo(VoiceCommand.objects.create(user=self.user, applied=applied.done, undo=applied.undo))
        self.assertEqual(Comment.objects.get(pk=written.id).body, "pays Friday")

    def test_deleting_a_note_removes_it_and_undo_puts_it_back_where_it_was(self):
        first = self.note(self.invoice, "first")
        written = self.note(self.invoice, "second")
        stamp = Comment.objects.get(pk=written.id).created_at
        _, _, applied = self.run_ops(Operation(Op.DELETE_UPDATE, comment_id=written.id))
        self.assertEqual(list(Comment.objects.filter(todo=self.invoice).values_list("body", flat=True)), ["first"])
        CommandService.undo(VoiceCommand.objects.create(user=self.user, applied=applied.done, undo=applied.undo))
        restored = list(Comment.objects.filter(todo=self.invoice).order_by("created_at", "id"))
        self.assertEqual([c.body for c in restored], ["first", "second"])
        self.assertEqual(restored[1].created_at, stamp)
        self.assertEqual(first.pk, restored[0].pk)

    def test_notes_that_arent_the_users_or_arent_theirs_to_change_are_dropped(self):
        other = IdentityService.create_user("o@x.com")
        theirs = self.note(TodoService.create(other, TodoTitleSchema(title="theirs")), "private")
        miller = self.note(self.acme, "From Scheduled", automatic=True)
        for operation in (Operation(Op.EDIT_UPDATE, comment_id=theirs.id, text="x"), Operation(Op.DELETE_UPDATE, comment_id=theirs.id),
                          Operation(Op.DELETE_UPDATE, comment_id=miller.id), Operation(Op.DELETE_UPDATE, comment_id=9999), Operation(Op.DELETE_UPDATE)):
            valid, problems, _ = self.run_ops(operation)
            self.assertEqual((valid, len(problems)), ([], 1))
        self.assertTrue(Comment.objects.filter(pk=theirs.id).exists())

    def test_a_note_with_files_is_left_for_the_person_to_delete(self):
        from django.core.files.base import ContentFile
        written = self.note(self.invoice, "see attached")
        Attachment.objects.create(comment=written, file=ContentFile(b"x", name="a.txt"), name="a.txt", size=1, content_type="text/plain")
        valid, problems, _ = self.run_ops(Operation(Op.DELETE_UPDATE, comment_id=written.id))
        self.assertEqual(valid, [])
        self.assertIn("files", problems[0])

    def test_an_empty_edit_is_dropped(self):
        written = self.note(self.invoice, "keep me")
        valid, _, _ = self.run_ops(Operation(Op.EDIT_UPDATE, comment_id=written.id, text=" "))
        self.assertEqual(valid, [])

    def test_the_note_ids_go_into_the_next_prompts_history(self):
        valid, _ = CommandService.validate(CommandService.todos_for(self.user), [Operation(Op.ADD_UPDATE, self.invoice.id, "waiting on PO")])
        applied = CommandService.apply(self.user, valid)
        command = VoiceCommand.objects.create(user=self.user, outcome="applied", applied=applied.done)
        note = Comment.objects.get(todo=self.invoice)
        self.assertEqual(command.digest(), f'add_update #{self.invoice.id} note {note.id} "waiting on PO"')


@override_settings(GROQ_API_KEY="g", OPENROUTER_API_KEY="o")
class VoiceCommandViewTests(VoiceBase):
    def post(self, user=None, **headers):
        self.client.force_login(user or self.user)
        return self.client.post(reverse("voice:command"), data=b"audio", content_type="application/octet-stream", **headers)

    def run_with(self, transcript, decision, user=None, seconds=3.0, **headers):
        with patch("voice.views.RecordingService.listen", return_value=(transcript, seconds)), \
             patch("voice.views.DecisionService.decide", return_value=(decision, 120)) as decide, \
             patch("voice.views.publish") as publish:
            response = self.post(user, HTTP_X_VOICE_TURN="t1", HTTP_X_VOICE_SESSION="s1", **headers)
        self.decide, self.publish = decide, publish
        return response

    def test_a_command_is_applied_logged_announced_and_costed(self):
        decision = Decision([Operation(Op.CLOSE, self.acme.id)], model="m", cost=Decimal("0.0003"), tokens_in=80, tokens_out=40)
        body = self.run_with("close the acme one", decision).json()
        self.assertEqual((body["outcome"], body["reply"], body["heard"]), ("applied", "Closed “Acme follow-up”", "close the acme one"))
        command = VoiceCommand.objects.get()
        self.assertEqual((command.transcript, command.model, command.session), ("close the acme one", "m", "s1"))
        self.assertEqual(command.undo, [{"op": "reopen", "id": self.acme.id, "index": 1}])
        self.assertEqual((command.llm_cost, command.stt_cost, command.tokens_in), (Decimal("0.0003"), Decimal("0.000308"), 80))
        self.assertEqual([call.args[1] for call in self.publish.call_args_list], ["voice-heard", "todos-changed", "voice-result"])

    def test_the_result_and_the_live_event_say_which_thread_to_open_and_which_rows_to_light(self):
        body = self.run_with("note on the invoice", Decision([Operation(Op.ADD_UPDATE, self.invoice.id, "waiting on PO")])).json()
        self.assertEqual((body["show"], body["touched"]), (self.invoice.id, [self.invoice.id]))
        changed = [call for call in self.publish.call_args_list if call.args[1] == "todos-changed"][0]
        self.assertEqual(changed.args[2], {"show": self.invoice.id, "touched": [self.invoice.id], "read": True})

    def test_opening_something_sends_the_page_there_without_an_undo_or_a_list_refresh(self):
        body = self.run_with("open settings", Decision([Operation(Op.OPEN, target=Target.SETTINGS)])).json()
        self.assertEqual((body["outcome"], body["reply"], body["go"], body["undo"]), ("applied", "Opened Settings", {"to": "settings"}, None))
        self.assertNotIn("todos-changed", [call.args[1] for call in self.publish.call_args_list])

    def test_a_goodbye_ends_voice_mode(self):
        body = self.run_with("thank you", Decision([Operation(Op.END)])).json()
        self.assertEqual((body["go"], body["reply"]), ({"to": "end"}, "Closing voice mode"))

    def test_reopening_by_voice_reaches_the_done_list_and_flags_the_row(self):
        old = TodoService.create(self.user, TodoTitleSchema(title="Old one"))
        TodoService.close(old)
        Todo.objects.filter(pk=old.pk).update(closed_at=timezone.now() - timedelta(days=3))
        body = self.run_with("reopen the old one", Decision([Operation(Op.REOPEN, old.id)])).json()
        self.assertEqual((body["outcome"], body["reply"], body["touched"]), ("applied", "Reopened “Old one”", [old.id]))
        self.assertEqual([t.id for t in self.decide.call_args.kwargs["done_earlier"]], [old.id])
        self.assertIsNone(Todo.objects.get(pk=old.pk).closed_at)

    def test_the_thread_on_screen_and_the_notes_reach_the_model(self):
        Comment.objects.create(todo=self.invoice, body="Finance said Friday")
        self.run_with("delete my last comment", Decision(), HTTP_X_VOICE_OPEN=str(self.invoice.id))
        kwargs = self.decide.call_args.kwargs
        self.assertEqual(kwargs["open_id"], self.invoice.id)
        self.assertEqual([body for _, body in kwargs["notes"][self.invoice.id]], ["Finance said Friday"])

    def test_an_iphones_mp4_recording_is_sent_on_as_mp4(self):
        with patch("voice.views.RecordingService.listen", return_value=("close it", 3.0)) as listen, \
             patch("voice.views.DecisionService.decide", return_value=(Decision([Operation(Op.CLOSE, self.acme.id)]), 1)), patch("voice.views.publish"):
            self.post(HTTP_X_VOICE_MIME="audio/mp4;codecs=mp4a.40.2")
            self.assertEqual(listen.call_args.args[2], ".mp4")
            self.post()
            self.assertEqual(listen.call_args.args[2], ".webm")

    def test_every_result_carries_the_users_spend_for_the_status_bar(self):
        body = self.run_with("close it", Decision([Operation(Op.CLOSE, self.acme.id)], cost=Decimal("0.0003"))).json()
        self.assertEqual((body["usage"]["limit"], body["usage"]["commands"]), ("$1.00", 1))

    def test_a_follow_up_is_decided_with_the_conversation_so_far(self):
        self.run_with("add a todo to call the dentist", Decision([Operation(Op.CREATE, None, "Call the dentist")]))
        dentist = Todo.objects.get(title="Call the dentist")
        self.run_with("make it Thursday at 3", Decision([Operation(Op.RENAME, dentist.id, "Call the dentist Thursday at 3")]))
        history = self.decide.call_args.args[2]
        self.assertEqual(history, [{"said": "add a todo to call the dentist", "did": f'create #{dentist.id} "Call the dentist"'}])
        self.assertEqual(Todo.objects.get(pk=dentist.id).title, "Call the dentist Thursday at 3")
        self.assertEqual(Todo.objects.filter(title__icontains="dentist").count(), 1)   # renamed, not added again

    def test_a_question_from_the_model_changes_nothing(self):
        body = self.run_with("close the budget one", Decision(reply="Which budget todo?")).json()
        self.assertEqual((body["outcome"], body["reply"]), ("asked", "Which budget todo?"))
        self.assertEqual(self.titles(), ["Acme follow-up", "Send invoice"])

    def test_an_invented_id_changes_nothing(self):
        self.assertEqual(self.run_with("close it", Decision([Operation(Op.CLOSE, 9999)])).json()["outcome"], "nothing")
        self.assertEqual(self.titles(), ["Acme follow-up", "Send invoice"])

    def test_silence_and_whispers_hallucinations_say_nothing_and_skip_the_model(self):
        for heard in ("", "  ", "Thanks for watching!", "Thank you for watching.", "you"):
            body = self.run_with(heard, Decision()).json()
            self.decide.assert_not_called()
            self.assertEqual((body["outcome"], body["reply"]), ("nothing", ""))
            self.assertEqual([call.args[1] for call in self.publish.call_args_list], ["voice-result"])  # no "heard": nothing to show

    def test_a_guest_can_use_voice_with_no_live_stream_and_the_speech_is_still_costed(self):
        guest = GuestService.create_guest()
        mine = TodoService.create(guest, TodoTitleSchema(title="guest todo"))
        body = self.run_with("close it", Decision([Operation(Op.CLOSE, mine.id)]), user=guest).json()
        self.assertEqual(body["outcome"], "applied")
        self.assertTrue(Todo.objects.get(pk=mine.id).closed_at)
        self.publish.assert_not_called()   # a guest's page holds no stream: the response carries the result
        self.assertGreater(VoiceCommand.objects.get(user=guest).stt_cost, 0)

    def test_a_guest_over_budget_is_stopped_before_any_speech_or_model_call(self):
        guest = GuestService.create_guest()
        VoiceCommand.objects.create(user=guest, llm_cost=Decimal("1.00"))
        with patch("voice.views.RecordingService.listen") as listen, patch("voice.views.DecisionService.decide") as decide:
            self.client.force_login(guest)
            body = self.client.post(reverse("voice:command"), data=b"audio", content_type="application/octet-stream").json()
        listen.assert_not_called()
        decide.assert_not_called()
        self.assertEqual((body["outcome"], body["reply"]), ("limit", "You've used your $1.00 of voice."))
        self.assertEqual(body["usage"]["percent"], 100)

    def test_a_member_over_their_dollar_is_stopped_too(self):
        VoiceCommand.objects.create(user=self.user, llm_cost=Decimal("1.00"))
        with patch("voice.views.RecordingService.listen") as listen, patch("voice.views.DecisionService.decide") as decide:
            self.client.force_login(self.user)
            body = self.client.post(reverse("voice:command"), data=b"audio", content_type="application/octet-stream").json()
        listen.assert_not_called()
        decide.assert_not_called()
        self.assertEqual(body["outcome"], "limit")

    @override_settings(VOICE_MEMBER_BUDGET_USD="100")
    def test_a_raised_limit_lets_them_carry_on(self):
        VoiceCommand.objects.create(user=self.user, llm_cost=Decimal("50"))
        self.assertEqual(self.run_with("close it", Decision([Operation(Op.CLOSE, self.acme.id)])).json()["outcome"], "applied")

    def test_undo_reverses_the_command(self):
        self.run_with("close the acme one", Decision([Operation(Op.CLOSE, self.acme.id)]))
        command = VoiceCommand.objects.get()
        with patch("voice.views.publish"):
            self.client.post(reverse("voice:undo", args=[command.pk]))
        self.assertIsNone(Todo.objects.get(pk=self.acme.id).closed_at)


class ScheduleByVoiceTests(VoiceBase):
    """"Move it to Friday", "...at 3pm", "remind me at 4", "schedule a call with mum for the 20th"."""

    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.friday = self.today + timedelta(days=3)

    def schedule(self, todo=None, text=None, day=None, at=None):
        return Operation(Op.SCHEDULE, todo.id if todo else None, text, date=(day or self.friday).isoformat(), time=at)

    def run_ops(self, *operations):
        valid, problems = CommandService.validate(CommandService.todos_for(self.user), list(operations))
        return valid, problems, (CommandService.apply(self.user, valid) if valid else None)

    def undo(self, applied):
        return CommandService.undo(VoiceCommand.objects.create(user=self.user, applied=applied.done, undo=applied.undo))

    def listed(self):
        return [t.title for t in TodoService.listed_for(self.user)]

    def test_a_todo_moves_to_a_day_and_leaves_today_with_no_reminder(self):
        _, problems, applied = self.run_ops(self.schedule(self.invoice))
        self.assertEqual(problems, [])
        self.assertEqual(self.listed(), ["Acme follow-up"])
        item = ScheduledItem.objects.get()
        self.assertEqual((item.todo_id, item.on_date, item.at_time), (self.invoice.id, self.friday, None))
        self.assertEqual(applied.lines, [f"Moved “Send invoice” to {ScheduleService.short_date(self.friday)}"])

    def test_a_time_comes_with_it_and_is_said_back(self):
        _, _, applied = self.run_ops(self.schedule(self.invoice, at="15:00"))
        self.assertEqual(ScheduledItem.objects.get().at_time, time(15, 0))
        self.assertTrue(applied.lines[0].endswith(" · 3pm"))

    def test_undo_brings_it_back_to_its_place(self):
        _, _, applied = self.run_ops(self.schedule(self.invoice, at="15:00"))
        self.assertTrue(self.undo(applied))
        self.assertEqual(self.listed(), ["Acme follow-up", "Send invoice"])
        self.assertFalse(ScheduledItem.objects.exists())
        self.assertFalse(self.invoice.comments.exists())

    def test_today_with_a_time_is_only_a_reminder_and_undo_takes_it_off(self):
        later = (timezone.localtime() + timedelta(hours=2)).replace(second=0, microsecond=0)
        if later.date() != self.today:
            self.skipTest("too near midnight for a time later today")
        _, problems, applied = self.run_ops(self.schedule(self.invoice, day=self.today, at=later.strftime("%H:%M")))
        self.assertEqual(problems, [])
        self.assertEqual(self.listed(), ["Acme follow-up", "Send invoice"])   # it didn't move
        self.assertEqual(Todo.objects.get(pk=self.invoice.id).notify_at, later)
        self.assertEqual(applied.lines[0], f"Reminder set for “Send invoice” at {later.strftime('%-I:%M%p').lower().replace(':00', '')}")
        self.undo(applied)
        self.assertIsNone(Todo.objects.get(pk=self.invoice.id).notify_at)

    def test_today_without_a_time_is_nothing_to_do_and_a_time_gone_by_is_refused(self):
        self.assertEqual(self.run_ops(self.schedule(self.invoice, day=self.today))[0], [])
        with patch("django.utils.timezone.now", return_value=timezone.now().replace(hour=15, minute=0)):
            valid, problems, _ = self.run_ops(self.schedule(self.invoice, day=timezone.localdate(), at="09:00"))
        self.assertEqual((valid, problems), ([], ["that time has already passed"]))

    def test_a_day_that_has_passed_or_is_too_far_or_unreadable_is_refused(self):
        for day, at in (((self.today - timedelta(days=1)).isoformat(), None), ((self.today + timedelta(days=500)).isoformat(), None),
                        ("next friday", None), (None, None), (self.friday.isoformat(), "teatime")):
            operation = Operation(Op.SCHEDULE, self.invoice.id, date=day, time=at)
            valid, problems, _ = self.run_ops(operation)
            self.assertEqual((valid, len(problems)), ([], 1), (day, at))
        self.assertFalse(ScheduledItem.objects.exists())

    def test_a_done_todo_is_reopened_and_scheduled_and_undo_closes_it_again(self):
        TodoService.close(self.acme)
        _, problems, applied = self.run_ops(self.schedule(self.acme, at="15:00"))
        self.assertEqual(problems, [])
        self.assertIsNone(Todo.objects.get(pk=self.acme.id).closed_at)
        self.assertEqual(ScheduledItem.objects.get().todo_id, self.acme.id)
        self.undo(applied)
        self.assertIsNotNone(Todo.objects.get(pk=self.acme.id).closed_at)
        self.assertFalse(ScheduledItem.objects.exists())

    def test_asking_for_a_reminder_on_a_done_todo_reopens_it_and_sets_it(self):
        later = (timezone.localtime() + timedelta(hours=2)).replace(second=0, microsecond=0)
        if later.date() != self.today:
            self.skipTest("too near midnight")
        TodoService.close(self.acme)
        _, problems, applied = self.run_ops(self.schedule(self.acme, day=self.today, at=later.strftime("%H:%M")))
        self.assertEqual(problems, [])
        todo = Todo.objects.get(pk=self.acme.id)
        self.assertEqual((todo.closed_at, todo.notify_at), (None, later))
        self.undo(applied)
        todo.refresh_from_db()
        self.assertIsNotNone(todo.closed_at)
        self.assertIsNone(todo.notify_at)

    def test_only_a_task_that_is_open_and_yours_can_be_moved(self):
        other = TodoService.create(IdentityService.create_user("o@x.com"), TodoTitleSchema(title="theirs"))
        meeting = Todo.objects.create(user=self.user, title="Standup", kind=Todo.Kind.MEETING, position=9,
                                      starts_at=timezone.now(), ends_at=timezone.now() + timedelta(hours=1))
        self.user.show_meetings = True
        self.user.save()
        for operation in (self.schedule(meeting), Operation(Op.SCHEDULE, other.id, date=self.friday.isoformat()),
                          Operation(Op.SCHEDULE, 9999, date=self.friday.isoformat())):
            self.assertEqual(self.run_ops(operation)[0], [], operation)

    def test_a_new_todo_can_be_scheduled_for_a_day_with_a_time(self):
        _, problems, applied = self.run_ops(self.schedule(text="Buy a cake", at="09:30"))
        self.assertEqual(problems, [])
        item = ScheduledItem.objects.get()
        self.assertEqual((item.title, item.on_date, item.at_time, item.todo_id), ("Buy a cake", self.friday, time(9, 30), None))
        self.assertEqual(applied.lines, [f"Scheduled “Buy a cake” for {ScheduleService.short_date(self.friday)} · 9:30am"])
        self.assertNotIn("Buy a cake", self.listed())   # it arrives that day
        self.undo(applied)
        self.assertFalse(ScheduledItem.objects.exists())

    def test_a_new_todo_for_today_is_just_added_with_its_reminder(self):
        later = (timezone.localtime() + timedelta(hours=3)).replace(second=0, microsecond=0)
        if later.date() != self.today:
            self.skipTest("too near midnight for a time later today")
        _, _, applied = self.run_ops(self.schedule(text="Call mum", day=self.today, at=later.strftime("%H:%M")))
        todo = Todo.objects.get(title="Call mum")
        self.assertEqual(todo.notify_at, later)
        self.undo(applied)
        self.assertTrue(Todo.objects.get(pk=todo.pk).deleted_at)

    def test_a_new_one_needs_a_title(self):
        self.assertEqual(self.run_ops(self.schedule(text=" "))[0], [])

    def test_the_model_is_told_the_date_and_given_the_fields(self):
        todos = CommandService.todos_for(self.user)
        today = timezone.localdate()
        prompt = DecisionService.build_body("move it to friday", todos, [], "m", today=today)["messages"][1]["content"]
        self.assertIn(f"Today is {today:%A} {today.isoformat()}.", prompt)
        self.assertIn("schedule", DecisionService.build_body("x", todos, [], "m")["messages"][0]["content"])

    def test_the_models_date_and_time_are_read_and_junk_is_dropped(self):
        decision = DecisionService.parse({"reply": "", "operations": [
            {"op": "schedule", "todo_id": 4, "text": None, "date": "2026-10-16", "time": "15:00"},
            {"op": "schedule", "todo_id": 4, "text": None, "date": 5, "time": ["x"]}]})
        first, second = decision.operations
        self.assertEqual((first.date, first.time, second.date, second.time), ("2026-10-16", "15:00", None, None))

    def test_the_next_prompt_remembers_what_was_scheduled(self):
        _, _, applied = self.run_ops(self.schedule(self.invoice, at="15:00"))
        command = VoiceCommand.objects.create(user=self.user, outcome="applied", applied=applied.done)
        self.assertEqual(command.digest(), f"schedule #{self.invoice.id} on {self.friday.isoformat()} at 15:00")


@override_settings(GROQ_API_KEY="g", OPENROUTER_API_KEY="o")
class ScheduleByVoiceViewTests(VoiceBase):
    def test_a_spoken_schedule_moves_the_todo_flags_it_and_is_undoable(self):
        friday = timezone.localdate() + timedelta(days=3)
        decision = Decision([Operation(Op.SCHEDULE, self.invoice.id, date=friday.isoformat(), time="15:00")])
        self.client.force_login(self.user)
        with patch("voice.views.RecordingService.listen", return_value=("move the invoice to friday at three", 3.0)), \
             patch("voice.views.DecisionService.decide", return_value=(decision, 100)) as decide, patch("voice.views.publish"):
            body = self.client.post(reverse("voice:command"), data=b"a", content_type="application/octet-stream").json()
        self.assertEqual(decide.call_args.kwargs["today"], timezone.localdate())
        self.assertEqual((body["outcome"], body["touched"]), ("applied", [self.invoice.id]))
        self.assertTrue(ScheduledItem.objects.filter(todo=self.invoice, at_time=time(15, 0)).exists())
        with patch("voice.views.publish"):
            self.client.post(reverse("voice:undo", args=[body["undo"]]))
        self.assertFalse(ScheduledItem.objects.exists())


@override_settings(GROQ_API_KEY="g", OPENROUTER_API_KEY="o")
class UsageScreenTests(VoiceBase):
    """What a person sees of their spend: a bar in the voice mode footer, and a section in Settings."""

    def page(self, user, url):
        self.client.force_login(user)
        return self.client.get(url).content.decode()

    def test_a_member_sees_their_spend_on_the_board_and_in_settings(self):
        VoiceCommand.objects.create(user=self.user, llm_cost=Decimal("0.0047"))
        board = self.page(self.user, reverse("board:index"))
        self.assertIn("topbar__voice", board)                      # the mic in the top bar, before "All todos"
        self.assertLess(board.index("topbar__voice"), board.index('aria-label="All todos'))
        self.assertIn('id="voice-dock"', board)                    # and the button at the foot, for phones
        self.assertIn('id="voice-stop"', board)                    # stopping is done inside the arch
        self.assertNotIn("voice-usage", board)                     # what it costs is in Settings, not on the bar
        self.assertIn('data-left="$0.9953"', board)
        settings_page = self.page(self.user, reverse("board:settings"))
        self.assertIn("AI usage", settings_page)
        self.assertIn("$0.0047 of $1.00", settings_page)
        self.assertIn("You have $1.00 for now", settings_page)

    def test_both_typing_boxes_have_their_plus_and_no_mic_of_their_own(self):
        board = self.page(self.user, reverse("board:index"))
        self.assertEqual(board.count('class="composer__plus"'), 2)   # Add a todo, Add an update
        self.assertNotIn("composer__mic", board)

    def test_a_guest_sees_it_too_with_their_own_wording(self):
        guest = GuestService.create_guest()
        VoiceCommand.objects.create(user=guest, llm_cost=Decimal("0.25"))
        board = self.page(guest, reverse("board:index"))
        self.assertIn("topbar__voice", board)
        self.assertIn('data-percent="25"', board)
        settings_page = self.page(guest, reverse("board:settings"))
        self.assertIn("A guest gets $1.00 to try it", settings_page)
        self.assertIn("$0.25 of $1.00", settings_page)

    def test_asking_for_usage_opens_that_section(self):
        self.assertIn('data-group="usage" open', self.page(self.user, reverse("board:settings") + "?open=usage"))
        self.assertNotIn('data-group="usage" open', self.page(self.user, reverse("board:settings")))

    @override_settings(GROQ_API_KEY="")
    def test_without_the_keys_there_is_no_voice_and_no_usage_section(self):
        self.assertNotIn("AI usage", self.page(self.user, reverse("board:settings")))
        board = self.page(self.user, reverse("board:index"))
        self.assertNotIn("voice-dock", board)
        self.assertNotIn("topbar__voice", board)

    def test_a_user_at_their_limit_gets_the_bar_marked_full_and_the_message(self):
        VoiceCommand.objects.create(user=self.user, llm_cost=Decimal("1.00"))
        board = self.page(self.user, reverse("board:index"))
        self.assertIn('data-limited="1"', board)
        self.assertIn("used your $1.00 of voice.", board)
        self.assertIn('data-percent="100"', board)
