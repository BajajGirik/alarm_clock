"""End-to-end smoke tests for the CLI.

These drive the program the way a user (or a shell script) would — as a
subprocess — and assert on output and exit codes. They validate the *wiring*
(argparse -> validation -> store), not the time logic, which is covered by
test_core. State is isolated per test via the ALARM_CLOCK_FILE env override.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.alarm_file = Path(self._tmp.name) / "alarms.json"

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args):
        env = dict(os.environ, ALARM_CLOCK_FILE=str(self.alarm_file))
        return subprocess.run(
            [sys.executable, "-m", "alarm", *args],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
        )


class TestAdd(CliTestCase):
    def test_add_recurring(self):
        r = self.run_cli("add", "07:30", "--repeat", "weekdays", "--label", "Standup")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("added alarm 1", r.stdout)
        self.assertIn("weekdays", r.stdout)

    def test_add_one_shot_default_date(self):
        r = self.run_cli("add", "07:30")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("once", r.stdout)

    def test_add_with_explicit_future_date(self):
        future = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        r = self.run_cli("add", "06:00", "--date", future)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("added alarm 1", r.stdout)

    def test_reject_invalid_time(self):
        r = self.run_cli("add", "25:99")
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid time", r.stdout)

    def test_reject_past_date(self):
        r = self.run_cli("add", "06:00", "--date", "2020-01-01")
        self.assertEqual(r.returncode, 2)
        self.assertIn("past", r.stdout)

    def test_reject_date_with_recurring(self):
        future = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        r = self.run_cli("add", "06:00", "--date", future, "--repeat", "daily")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--date cannot be combined", r.stdout)

    def test_reject_malformed_date(self):
        r = self.run_cli("add", "06:00", "--date", "06-10-2026")
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid date", r.stdout)


class TestListAndRemove(CliTestCase):
    def test_list_empty(self):
        r = self.run_cli("list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no alarms", r.stdout)

    def test_add_list_remove_cycle(self):
        self.run_cli("add", "07:30", "--repeat", "daily", "--label", "Wake")
        self.run_cli("add", "12:00", "--label", "Lunch")

        r = self.run_cli("list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Wake", r.stdout)
        self.assertIn("Lunch", r.stdout)
        self.assertIn("07:30", r.stdout)

        r = self.run_cli("remove", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("removed alarm 1", r.stdout)

        r = self.run_cli("list")
        self.assertNotIn("Wake", r.stdout)
        self.assertIn("Lunch", r.stdout)

    def test_remove_absent(self):
        r = self.run_cli("remove", "99")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no alarm with id 99", r.stdout)


class TestCorruptFile(CliTestCase):
    def test_list_on_corrupt_file_reports_cleanly(self):
        self.alarm_file.parent.mkdir(parents=True, exist_ok=True)
        self.alarm_file.write_text("{not json")
        r = self.run_cli("list")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not valid JSON", r.stdout)
        self.assertIn("fix or remove the file", r.stdout)
        # A clean message, not a Python traceback.
        self.assertNotIn("Traceback", r.stderr)


class TestNoCommand(CliTestCase):
    def test_no_args_errors(self):
        r = self.run_cli()
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
