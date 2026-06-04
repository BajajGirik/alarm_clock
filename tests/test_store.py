"""Tests for persistence.

Each test points the store at a temp file (not the real home directory), so
they are isolated and side-effect free. They assert observable behavior:
round-trip fidelity, id assignment, removal semantics, and that a failed write
leaves the previous file intact.
"""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from alarm.store import CorruptStoreError, Store


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "alarms.json"
        self.store = Store(self.path)

    def tearDown(self):
        self._tmp.cleanup()


class TestRoundTrip(StoreTestCase):
    def test_empty_when_missing(self):
        self.assertEqual(self.store.load(), [])

    def test_add_then_reload(self):
        self.store.add("07:30", label="Standup", days={0, 1, 2, 3, 4})
        reloaded = Store(self.path).load()  # fresh store, same file
        self.assertEqual(len(reloaded), 1)
        a = reloaded[0]
        self.assertEqual(a.time, "07:30")
        self.assertEqual(a.label, "Standup")
        self.assertEqual(a.days, {0, 1, 2, 3, 4})
        self.assertTrue(a.enabled)

    def test_one_shot_datetime_round_trips(self):
        when = datetime(2026, 6, 10, 6, 0)
        self.store.add("06:00", days=set(), when=when)
        a = Store(self.path).load()[0]
        self.assertEqual(a.when, when)
        self.assertTrue(a.is_one_shot)

    def test_snooze_and_last_fired_round_trip(self):
        self.store.add("07:30", days={0, 1, 2, 3, 4, 5, 6})
        alarms = self.store.load()
        alarms[0].snoozed_until = datetime(2026, 6, 3, 7, 35)
        alarms[0].last_fired = datetime(2026, 6, 3, 7, 30)
        self.store.save(alarms)
        a = Store(self.path).load()[0]
        self.assertEqual(a.snoozed_until, datetime(2026, 6, 3, 7, 35))
        self.assertEqual(a.last_fired, datetime(2026, 6, 3, 7, 30))


class TestIds(StoreTestCase):
    def test_ids_are_assigned_incrementally(self):
        a1 = self.store.add("07:00")
        a2 = self.store.add("08:00")
        self.assertEqual((a1.id, a2.id), (1, 2))

    def test_next_id_after_removal_does_not_reuse_max(self):
        self.store.add("07:00")  # id 1
        self.store.add("08:00")  # id 2
        self.store.remove(2)
        a3 = self.store.add("09:00")
        self.assertEqual(a3.id, 2)  # max remaining (1) + 1


class TestRemove(StoreTestCase):
    def test_remove_existing(self):
        self.store.add("07:00")
        self.assertTrue(self.store.remove(1))
        self.assertEqual(self.store.load(), [])

    def test_remove_absent_returns_false(self):
        self.store.add("07:00")
        self.assertFalse(self.store.remove(99))
        self.assertEqual(len(self.store.load()), 1)


class TestAtomicWrite(StoreTestCase):
    def test_existing_file_intact_when_serialization_fails(self):
        self.store.add("07:00", label="keep me")
        before = self.path.read_text()

        # Force save() to blow up mid-operation by handing it an object that
        # can't serialize. The original file must be left untouched, and no
        # stray temp files should remain.
        class Boom:
            def to_dict(self):
                raise RuntimeError("kaboom")

        with self.assertRaises(RuntimeError):
            self.store.save([Boom()])

        self.assertEqual(self.path.read_text(), before)
        leftovers = list(self.path.parent.glob(".alarms-*.tmp"))
        self.assertEqual(leftovers, [], f"temp files left behind: {leftovers}")

    def test_saved_file_is_valid_json_array(self):
        self.store.add("07:00")
        data = json.loads(self.path.read_text())
        self.assertIsInstance(data, list)
        self.assertEqual(data[0]["time"], "07:00")


class TestCorruptFile(StoreTestCase):
    """A malformed file surfaces CorruptStoreError, not a raw decode error, so
    a single bad byte can't crash every command with a traceback."""

    def test_malformed_json_raises_corrupt(self):
        self.path.write_text("{not json")
        with self.assertRaises(CorruptStoreError):
            self.store.load()

    def test_non_array_raises_corrupt(self):
        self.path.write_text('{"id": 1, "time": "07:30"}')  # object, not a list
        with self.assertRaises(CorruptStoreError):
            self.store.load()

    def test_record_missing_required_field_raises_corrupt(self):
        self.path.write_text('[{"time": "07:30"}]')  # no id
        with self.assertRaises(CorruptStoreError):
            self.store.load()

    def test_empty_file_raises_corrupt(self):
        # A zero-byte file (e.g. an interrupted non-atomic write by something
        # else) is not valid JSON.
        self.path.write_text("")
        with self.assertRaises(CorruptStoreError):
            self.store.load()


if __name__ == "__main__":
    unittest.main()
