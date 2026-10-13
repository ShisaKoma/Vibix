from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from .models import Event
from .scheduler import presentation, timed_days


class TimedLayoutTests(SimpleTestCase):
    tz = ZoneInfo("Europe/Paris")

    def event(self, pk, start, end, **kwargs):
        return Event(pk=pk, starts_at=start, ends_at=end, **kwargs)

    def layout(self, events, day, count=1):
        days, _ = presentation(events, day, day + timedelta(days=count), self.tz)
        return timed_days(days, self.tz)

    def test_minutes_and_duration_are_preserved_without_hour_rounding(self):
        day = date(2026, 10, 5)
        start = datetime(2026, 10, 5, 10, 15, tzinfo=self.tz)
        block = self.layout([self.event(1, start, start + timedelta(minutes=90))], day)[0][
            "blocks"
        ][0]
        self.assertEqual((block["offset"], block["duration"]), (615, 90))
        self.assertEqual((block["lane"], block["lanes"]), (0, 1))

    def test_overlap_groups_reuse_lanes_and_touching_intervals_do_not_overlap(self):
        day = date(2026, 10, 5)
        start = datetime(2026, 10, 5, 10, tzinfo=self.tz)
        events = [
            self.event(1, start, start + timedelta(hours=2)),
            self.event(2, start + timedelta(minutes=30), start + timedelta(minutes=90)),
            self.event(3, start + timedelta(minutes=90), start + timedelta(hours=3)),
            self.event(4, start + timedelta(hours=3), start + timedelta(hours=4)),
        ]
        blocks = self.layout(events, day)[0]["blocks"]
        self.assertEqual([block["lane"] for block in blocks], [0, 1, 1, 0])
        self.assertEqual([block["lanes"] for block in blocks], [2, 2, 2, 1])
        reversed_blocks = self.layout(list(reversed(events)), day)[0]["blocks"]
        self.assertEqual(blocks, reversed_blocks)

    def test_cross_midnight_clipping_excludes_all_day_and_exclusive_end(self):
        day = date(2026, 10, 5)
        start = datetime(2026, 10, 5, 23, 30, tzinfo=self.tz)
        events = [
            self.event(1, start, start + timedelta(hours=2)),
            self.event(2, start, start + timedelta(minutes=30)),
            self.event(
                3,
                start.replace(hour=0, minute=0),
                start.replace(hour=0, minute=0) + timedelta(days=2),
                all_day=True,
            ),
        ]
        first, second = self.layout(events, day, count=2)
        self.assertEqual(len(first["blocks"]), 2)
        self.assertEqual(len(second["blocks"]), 1)
        self.assertEqual(len(first["all_day_events"]), 1)
        self.assertEqual(first["blocks"][0]["duration"], 30)
        self.assertTrue(first["blocks"][0]["continues_after"])
        self.assertEqual(second["blocks"][0]["duration"], 90)
        self.assertTrue(second["blocks"][0]["continues_before"])

    def test_dst_days_use_actual_elapsed_minutes_and_show_repeated_hours(self):
        spring = self.layout([], date(2026, 3, 29))[0]
        autumn = self.layout([], date(2026, 10, 25))[0]
        self.assertEqual(spring["minutes"], 1380)
        self.assertEqual(autumn["minutes"], 1500)
        self.assertNotIn("02:00", [slot["label"] for slot in spring["slots"]])
        self.assertEqual([slot["label"] for slot in autumn["slots"]].count("02:00"), 2)
        start = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
        block = self.layout([self.event(1, start, start + timedelta(hours=1))], date(2026, 10, 25))[
            0
        ]["blocks"][0]
        self.assertEqual((block["offset"], block["duration"]), (150, 60))
        self.assertIn("UTC+0200", block["time_label"])
        self.assertIn("UTC+0100", block["time_label"])

    def test_short_visual_markers_do_not_collide(self):
        start = datetime(2026, 10, 5, 10, tzinfo=self.tz)
        blocks = self.layout(
            [
                self.event(1, start, start + timedelta(minutes=1)),
                self.event(2, start + timedelta(minutes=2), start + timedelta(minutes=3)),
            ],
            start.date(),
        )[0]["blocks"]
        self.assertEqual([block["duration"] for block in blocks], [1, 1])
        self.assertEqual([block["lanes"] for block in blocks], [2, 2])
