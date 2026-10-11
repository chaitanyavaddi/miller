from datetime import date, time, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.services import IdentityService
from comments.models import Comment
from scheduled import rhythm
from scheduled.models import ScheduledItem
from scheduled.services import ProductionService
from todos.models import Todo
from todos.schemas import TodoTitleSchema
from todos.services import TodoService

Rhythm = ScheduledItem.Rhythm
MONDAY = date(2026, 10, 12)


class RhythmTests(TestCase):
    def item(self, **fields):
        return ScheduledItem(**fields)

    def test_weekly_falls_on_its_days_only(self):
        item = self.item(rhythm=Rhythm.WEEKLY, weekdays="0,1,2,3,4")
        week = [MONDAY + timedelta(days=n) for n in range(7)]
        self.assertEqual([rhythm.falls_on(item, day) for day in week], [True] * 5 + [False] * 2)

    def test_monthly_on_the_31st_falls_on_a_shorter_months_last_day(self):
        item = self.item(rhythm=Rhythm.MONTHLY, month_day=31)
        self.assertTrue(rhythm.falls_on(item, date(2026, 11, 30)))
        self.assertTrue(rhythm.falls_on(item, date(2027, 2, 28)))
        self.assertFalse(rhythm.falls_on(item, date(2026, 11, 29)))

    def test_labels_read_short_and_give_the_date(self):
        today = MONDAY
        self.assertEqual(rhythm.label(self.item(rhythm=Rhythm.WEEKLY, weekdays="0,1,2,3,4,5,6"), today), "every day")
        self.assertEqual(rhythm.label(self.item(rhythm=Rhythm.WEEKLY, weekdays="0,3"), today), "every mo, th")
        self.assertEqual(rhythm.label(self.item(rhythm=Rhythm.MONTHLY, month_day=1), today), "monthly · 1st")
        self.assertEqual(rhythm.label(self.item(rhythm=Rhythm.ONCE, on_date=MONDAY + timedelta(days=3)), today), "once · 15 oct")
        self.assertEqual(rhythm.label(self.item(rhythm=Rhythm.ONCE, on_date=MONDAY + timedelta(days=11)), today), "once · 23 oct")


class ProductionTests(TestCase):
    """Run the producer as if it were a given day."""

    def setUp(self):
        self.user = IdentityService.create_user("p@x.com")
        TodoService.create(self.user, TodoTitleSchema(title="already here"))

    def schedule(self, created=MONDAY - timedelta(days=30), **fields):
        item = ScheduledItem.objects.create(user=self.user, title=fields.pop("title", "stretch"), **fields)
        ScheduledItem.objects.filter(pk=item.pk).update(created_at=timezone.make_aware(timezone.datetime.combine(created, time(8))))
        return ScheduledItem.objects.get(pk=item.pk)

    def produce_on(self, day):
        with patch("scheduled.services.production_service.timezone.localdate", return_value=day):
            return ProductionService.produce_due(self.user)

    def produced(self):
        return Todo.objects.filter(user=self.user, scheduled_item__isnull=False).count()

    def test_a_mon_to_fri_item_lands_at_the_top_on_those_days_only(self):
        self.schedule(rhythm=Rhythm.WEEKLY, weekdays="0,1,2,3,4")
        for offset in range(7):
            day = MONDAY + timedelta(days=offset)
            Todo.objects.filter(user=self.user, scheduled_item__isnull=False).update(closed_at=timezone.now())  # done each day
            made = self.produce_on(day)
            self.assertEqual(bool(made), day.weekday() < 5, day)
        first = Todo.objects.filter(user=self.user, title="stretch").first()
        self.assertEqual(first.position, 1)

    def test_an_open_copy_from_yesterday_isnt_made_twice(self):
        self.schedule(rhythm=Rhythm.WEEKLY, weekdays="0,1,2,3,4,5,6")
        self.produce_on(MONDAY)
        self.produce_on(MONDAY + timedelta(days=1))
        self.assertEqual(self.produced(), 1)

    def test_the_same_day_never_produces_twice(self):
        self.schedule(rhythm=Rhythm.WEEKLY, weekdays="0,1,2,3,4,5,6")
        self.produce_on(MONDAY)
        Todo.objects.filter(title="stretch").update(closed_at=timezone.now())
        self.assertEqual(self.produce_on(MONDAY), [])

    def test_missed_days_produce_exactly_one(self):
        self.schedule(rhythm=Rhythm.WEEKLY, weekdays="0,1,2,3,4,5,6")
        self.produce_on(MONDAY)
        Todo.objects.filter(title="stretch").update(closed_at=timezone.now())
        made = self.produce_on(MONDAY + timedelta(days=5))  # the app was closed four days
        self.assertEqual(len(made), 1)
        self.assertEqual(self.produced(), 2)

    def test_a_once_item_lands_on_its_date_then_leaves_scheduled(self):
        thursday = MONDAY + timedelta(days=3)
        item = self.schedule(rhythm=Rhythm.ONCE, on_date=thursday, title="call the bank")
        self.assertEqual(self.produce_on(MONDAY), [])
        [todo] = self.produce_on(thursday)
        self.assertEqual((todo.title, todo.position), ("call the bank", 1))
        self.assertFalse(ScheduledItem.objects.filter(pk=item.pk).exists())
        self.assertTrue(Comment.objects.filter(todo=todo, automatic=True, body__startswith="From Scheduled · once").exists())

    def test_a_monthly_item_produces_on_its_day(self):
        self.schedule(created=date(2026, 10, 1), rhythm=Rhythm.MONTHLY, month_day=23)
        self.assertEqual(self.produce_on(date(2026, 10, 22)), [])
        self.assertEqual(len(self.produce_on(date(2026, 10, 23))), 1)

    def test_an_untitled_item_produces_nothing(self):
        self.schedule(rhythm=Rhythm.WEEKLY, weekdays="0,1,2,3,4,5,6", title="")
        self.assertEqual(self.produce_on(MONDAY), [])

    def test_a_weekly_item_made_after_its_day_waits_for_the_next_one(self):
        self.schedule(created=MONDAY + timedelta(days=2), rhythm=Rhythm.WEEKLY, weekdays="0")  # made Wed, for Mondays
        self.assertEqual(self.produce_on(MONDAY + timedelta(days=3)), [])
        self.assertEqual(len(self.produce_on(MONDAY + timedelta(days=7))), 1)


class ScheduledModalTests(TestCase):
    """The modal edits on screen and saves only on Save: every change at once, or none."""

    def setUp(self):
        self.user = IdentityService.create_user("v@x.com", timezone="UTC")
        self.client.force_login(self.user)
        self.tomorrow = timezone.localdate() + timedelta(days=1)

    def save(self, *rows):
        """Post Save with these rows (dicts of row_* fields), as the modal does."""
        fields = ("row_id", "row_removed", "row_title", "row_rhythm", "row_on_date", "row_weekdays", "row_month_day")
        data = {name: [str(row.get(name, "")) for row in rows] for name in fields}
        return self.client.post(reverse("scheduled:save"), data)

    def test_the_nav_opens_a_modal_that_saves_only_on_save(self):
        self.assertContains(self.client.get(reverse("board:index")), f'hx-get="{reverse("board:review")}"')  # the nav opens on Past
        modal = self.client.get(reverse("scheduled:index"))
        self.assertContains(modal, 'id="timeline-title">All todos</span>')    # All todos, on its Scheduled tab
        self.assertContains(modal, 'class="timeline__tab is-on" aria-current="page" data-timeline-tab')
        self.assertContains(modal, f'hx-get="{reverse("board:review")}"')    # the Past tab
        self.assertContains(modal, 'aria-label="Close"')
        self.assertContains(modal, f'hx-post="{reverse("scheduled:save")}"')
        self.assertNotContains(modal, "hx-post=\"/scheduled/1")  # nothing posts on its own

    def test_save_adds_updates_and_removes_together(self):
        keep = ScheduledItem.objects.create(user=self.user, title="stretch", rhythm=Rhythm.MONTHLY, month_day=1)
        drop = ScheduledItem.objects.create(user=self.user, title="old", rhythm=Rhythm.MONTHLY, month_day=2)
        response = self.save(
            {"row_title": "water plants", "row_rhythm": "weekly", "row_weekdays": "1,4"},
            {"row_id": keep.id, "row_title": "stretch properly", "row_rhythm": "once", "row_on_date": self.tomorrow.isoformat()},
            {"row_id": drop.id, "row_removed": "1", "row_title": "old", "row_rhythm": "monthly", "row_month_day": "2"},
        )
        self.assertIn("overlay-close", response["HX-Trigger"])
        self.assertIn("board-changed", response["HX-Trigger"])
        keep.refresh_from_db()
        self.assertEqual((keep.title, keep.rhythm, keep.on_date), ("stretch properly", Rhythm.ONCE, self.tomorrow))
        self.assertTrue(ScheduledItem.objects.filter(title="water plants", rhythm=Rhythm.WEEKLY, weekdays="1,4").exists())
        self.assertFalse(ScheduledItem.objects.filter(pk=drop.pk).exists())

    def test_anything_incomplete_saves_nothing_and_says_why(self):
        keep = ScheduledItem.objects.create(user=self.user, title="stretch", rhythm=Rhythm.MONTHLY, month_day=1)
        response = self.save(
            {"row_id": keep.id, "row_title": "renamed", "row_rhythm": "monthly", "row_month_day": "1"},
            {"row_title": "", "row_rhythm": "once", "row_on_date": self.tomorrow.isoformat()},     # no title
        )
        self.assertContains(response, "Every scheduled todo needs a title")
        self.assertEqual(response["HX-Retarget"], "#overlay")
        keep.refresh_from_db()
        self.assertEqual(keep.title, "stretch")                                            # not even the valid change
        self.assertEqual(ScheduledItem.objects.count(), 1)
        response = self.save({"row_title": "gym", "row_rhythm": "weekly", "row_weekdays": ""})
        self.assertContains(response, "pick at least one day")
        response = self.save({"row_title": "far", "row_rhythm": "once", "row_on_date": (rhythm.once_limit(timezone.localdate()) + timedelta(days=1)).isoformat()})
        self.assertContains(response, "within a year")

    def test_someone_elses_items_cant_be_touched(self):
        other = ScheduledItem.objects.create(user=IdentityService.create_user("o@x.com"), title="theirs", rhythm=Rhythm.MONTHLY, month_day=1)
        self.assertEqual(self.save({"row_id": other.id, "row_title": "mine", "row_rhythm": "monthly", "row_month_day": "1"}).status_code, 404)
        self.assertEqual(self.save({"row_id": other.id, "row_removed": "1"}).status_code, 404)
        other.refresh_from_db()
        self.assertEqual(other.title, "theirs")

    def test_rows_carry_their_state_and_once_offers_a_year_of_months(self):
        ScheduledItem.objects.create(user=self.user, title="dentist", rhythm=Rhythm.ONCE, on_date=date(2026, 10, 17))
        html = self.client.get(reverse("scheduled:index")).content.decode()
        self.assertIn('data-rhythm="once" data-on-date="2026-10-17"', html)
        self.assertIn("17th October, 2026", html)
        self.assertEqual(html.count("data-month-pane"), 12)      # one shared calendar: this month and the next eleven
        self.assertNotIn("{#", html)                              # no template comment leaks onto the page
        self.assertEqual([r for r in ("once", "daily", "weekly", "monthly") if f'data-pick-rhythm="{r}"' in html], ["once", "weekly", "monthly"])

    def test_the_board_marks_todays_produced_todos(self):
        ScheduledItem.objects.create(user=self.user, title="stretch", rhythm=Rhythm.WEEKLY, weekdays="0,1,2,3,4,5,6")
        ScheduledItem.objects.create(user=self.user, title="dentist", rhythm=Rhythm.ONCE, on_date=timezone.localdate())
        page = self.client.get(reverse("board:index")).content.decode()
        self.assertIn('title="From Scheduled · every day"', page)
        self.assertIn('title="From Scheduled">', page)
