"""Tests for the pure core logic.

These assert *external behavior* — given an alarm and a chosen ``now``, does it
fire / not fire, and what is the next fire time — never internal state. Because
``now`` is an argument, every edge case (weekend skip, midnight wrap, stale
snooze, double-fire) is exercised directly, with no sleeping and no clock mock.

Reference dates used (verified weekdays):
    2026-06-03 is a Wednesday  (weekday() == 2)
    2026-06-06 is a Saturday   (weekday() == 5)
    2026-06-08 is a Monday     (weekday() == 0)
"""

import unittest
from datetime import datetime

from alarm import core
from alarm.model import Alarm

WEEKDAYS = {0, 1, 2, 3, 4}
DAILY = {0, 1, 2, 3, 4, 5, 6}


def recurring(time, days, **kw):
    return Alarm(id=1, time=time, days=set(days), **kw)


def one_shot(when, **kw):
    return Alarm(id=1, time=when.strftime("%H:%M"), days=set(), when=when, **kw)


class TestParseHHMM(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(core.parse_hhmm("07:30"), (7, 30))
        self.assertEqual(core.parse_hhmm("00:00"), (0, 0))
        self.assertEqual(core.parse_hhmm("23:59"), (23, 59))

    def test_invalid(self):
        for bad in ["25:00", "07:60", "7:5:5", "abc", "0730", "", "12"]:
            with self.assertRaises(ValueError, msg=bad):
                core.parse_hhmm(bad)


class TestNextOccurrence(unittest.TestCase):
    def test_later_today(self):
        now = datetime(2026, 6, 3, 6, 0)
        self.assertEqual(core.next_occurrence_of("07:30", now),
                         datetime(2026, 6, 3, 7, 30))

    def test_rolls_to_tomorrow_when_passed(self):
        now = datetime(2026, 6, 3, 8, 0)
        self.assertEqual(core.next_occurrence_of("07:30", now),
                         datetime(2026, 6, 4, 7, 30))


class TestNextFireTime(unittest.TestCase):
    def test_weekdays_skips_weekend(self):
        # Saturday 06:00 -> a weekdays alarm next fires Monday.
        now = datetime(2026, 6, 6, 6, 0)  # Saturday
        self.assertEqual(now.weekday(), 5)
        a = recurring("07:30", WEEKDAYS)
        self.assertEqual(core.next_fire_time(a, now), datetime(2026, 6, 8, 7, 30))

    def test_daily_rolls_to_tomorrow(self):
        now = datetime(2026, 6, 3, 9, 0)  # past 07:30 today
        a = recurring("07:30", DAILY)
        self.assertEqual(core.next_fire_time(a, now), datetime(2026, 6, 4, 7, 30))

    def test_midnight_wrap(self):
        # 23:59 daily, now 23:59:30 -> today's already in progress, next is tomorrow.
        now = datetime(2026, 6, 3, 23, 59, 30)
        a = recurring("23:59", DAILY)
        self.assertEqual(core.next_fire_time(a, now), datetime(2026, 6, 4, 23, 59))

    def test_one_shot_future(self):
        when = datetime(2026, 6, 10, 6, 0)
        a = one_shot(when)
        self.assertEqual(core.next_fire_time(a, datetime(2026, 6, 3, 6, 0)), when)

    def test_one_shot_spent_returns_none(self):
        when = datetime(2026, 6, 1, 6, 0)
        a = one_shot(when)
        self.assertIsNone(core.next_fire_time(a, datetime(2026, 6, 3, 6, 0)))

    def test_disabled_returns_none(self):
        a = recurring("07:30", DAILY, enabled=False)
        self.assertIsNone(core.next_fire_time(a, datetime(2026, 6, 3, 6, 0)))


class TestDue(unittest.TestCase):
    def test_due_within_window(self):
        a = recurring("07:30", DAILY)
        # now is 07:30:20 — target 07:30:00 is within [07:30:00, now].
        self.assertTrue(core.due(a, datetime(2026, 6, 3, 7, 30, 20)))

    def test_not_due_before_minute(self):
        a = recurring("07:30", DAILY)
        self.assertFalse(core.due(a, datetime(2026, 6, 3, 7, 29, 59)))

    def test_not_due_after_minute(self):
        # 07:31:00 — the 07:30 occurrence has aged out of the window.
        a = recurring("07:30", DAILY)
        self.assertFalse(core.due(a, datetime(2026, 6, 3, 7, 31, 0)))

    def test_weekdays_not_due_on_saturday(self):
        a = recurring("07:30", WEEKDAYS)
        now = datetime(2026, 6, 6, 7, 30, 10)  # Saturday
        self.assertFalse(core.due(a, now))

    def test_double_fire_guard(self):
        # Already fired this exact occurrence -> not due again, even within window.
        occ = datetime(2026, 6, 3, 7, 30)
        a = recurring("07:30", DAILY, last_fired=occ)
        self.assertFalse(core.due(a, datetime(2026, 6, 3, 7, 30, 20)))
        # ...but a different occurrence (next day) still fires.
        self.assertTrue(core.due(a, datetime(2026, 6, 4, 7, 30, 5)))

    def test_disabled_not_due(self):
        a = recurring("07:30", DAILY, enabled=False)
        self.assertFalse(core.due(a, datetime(2026, 6, 3, 7, 30, 5)))


class TestSnooze(unittest.TestCase):
    def test_snooze_fires_within_window(self):
        snooze = datetime(2026, 6, 3, 7, 35)
        a = recurring("07:30", DAILY, snoozed_until=snooze)
        self.assertTrue(core.due(a, datetime(2026, 6, 3, 7, 35, 15)))

    def test_snooze_not_yet_due(self):
        snooze = datetime(2026, 6, 3, 7, 35)
        a = recurring("07:30", DAILY, snoozed_until=snooze)
        self.assertFalse(core.due(a, datetime(2026, 6, 3, 7, 33, 0)))

    def test_stale_snooze_ages_out(self):
        # Crash, restart much later: snooze target is older than the window.
        snooze = datetime(2026, 6, 3, 7, 35)
        a = recurring("07:30", DAILY, snoozed_until=snooze)
        self.assertFalse(core.due(a, datetime(2026, 6, 3, 7, 50, 0)))

    def test_one_shot_snoozable(self):
        when = datetime(2026, 6, 3, 7, 30)
        snooze = datetime(2026, 6, 3, 7, 35)
        a = one_shot(when, snoozed_until=snooze)
        self.assertTrue(core.due(a, datetime(2026, 6, 3, 7, 35, 10)))


class TestParseResponse(unittest.TestCase):
    def test_dismiss(self):
        self.assertIsInstance(core.parse_response("d"), core.Dismiss)
        self.assertIsInstance(core.parse_response("dismiss"), core.Dismiss)

    def test_snooze_default(self):
        a = core.parse_response("s")
        self.assertIsInstance(a, core.Snooze)
        self.assertEqual(a.minutes, core.DEFAULT_SNOOZE_MINUTES)

    def test_snooze_custom(self):
        a = core.parse_response("s 30")
        self.assertIsInstance(a, core.Snooze)
        self.assertEqual(a.minutes, 30)

    def test_invalid(self):
        for bad in ["", "x", "s abc", "s -5", "s 0", "snooze foo"]:
            self.assertIsInstance(core.parse_response(bad), core.Invalid, msg=bad)

    def test_case_and_whitespace_insensitive(self):
        self.assertIsInstance(core.parse_response("  D  "), core.Dismiss)
        a = core.parse_response("  S 10 ")
        self.assertIsInstance(a, core.Snooze)
        self.assertEqual(a.minutes, 10)


if __name__ == "__main__":
    unittest.main()
