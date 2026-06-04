"""Persistence — the only module that touches the filesystem for alarm state.

Owns the on-disk JSON array of alarms: load, save, id assignment, add, remove.
Writes are **atomic** (serialize to a temp file in the same directory, then
``os.replace`` into place) so a crash mid-write can never corrupt the existing
schedule.

The store is parameterized by ``path`` rather than hard-coding the home
directory, which keeps it testable in isolation: tests point it at a temp file.
``default_path()`` supplies the real location for normal use.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from .model import Alarm

DEFAULT_DIR = Path.home() / ".alarm_clock"
DEFAULT_FILE = DEFAULT_DIR / "alarms.json"
ENV_VAR = "ALARM_CLOCK_FILE"


class CorruptStoreError(Exception):
    """The alarm file exists but is not a readable list of alarms.

    Raised by ``load`` instead of leaking a low-level ``JSONDecodeError`` /
    ``KeyError`` / ``TypeError``, so callers can surface one clear message
    rather than a traceback. The atomic write protects our own writes; this
    guards the read path against a half-written file from elsewhere, a manual
    edit, or disk corruption.
    """


def default_path() -> Path:
    """Where alarms are stored. Overridable via ``$ALARM_CLOCK_FILE`` — handy
    for relocating state and for keeping tests hermetic."""
    override = os.environ.get(ENV_VAR)
    return Path(override) if override else DEFAULT_FILE


class Store:
    def __init__(self, path: Path | str = DEFAULT_FILE):
        self.path = Path(path)

    # -- read --------------------------------------------------------------

    def load(self) -> list[Alarm]:
        """Load all alarms. Returns an empty list if the file doesn't exist.

        A malformed file (bad JSON, not an array, or a record missing required
        fields) raises :class:`CorruptStoreError` rather than a raw decode
        error, so a single bad byte can't crash every command with a traceback.
        """
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as fh:
            try:
                data = json.load(fh)
            except json.JSONDecodeError as e:
                raise CorruptStoreError(
                    f"alarm file at {self.path} is not valid JSON: {e}"
                ) from e
        if not isinstance(data, list):
            raise CorruptStoreError(
                f"alarm file at {self.path} should contain a list of alarms, "
                f"got {type(data).__name__}"
            )
        try:
            return [Alarm.from_dict(d) for d in data]
        except (KeyError, TypeError, AttributeError) as e:
            raise CorruptStoreError(
                f"alarm file at {self.path} has a malformed alarm record: {e}"
            ) from e

    # -- write -------------------------------------------------------------

    def save(self, alarms: list[Alarm]) -> None:
        """Persist alarms atomically (temp file + ``os.replace``)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([a.to_dict() for a in alarms], indent=2)
        # Write to a temp file in the same directory so os.replace is atomic
        # (a cross-filesystem rename would not be).
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".alarms-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            # Leave the existing file untouched; clean up the temp file.
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise

    # -- mutations ---------------------------------------------------------

    def _next_id(self, alarms: list[Alarm]) -> int:
        return (max((a.id for a in alarms), default=0)) + 1

    def add(
        self,
        time: str,
        label: str = "",
        days: Optional[set[int]] = None,
        when: Optional[datetime] = None,
    ) -> Alarm:
        """Create an alarm, assign the next id, persist, and return it."""
        alarms = self.load()
        alarm = Alarm(
            id=self._next_id(alarms),
            time=time,
            label=label,
            days=days or set(),
            when=when,
        )
        alarms.append(alarm)
        self.save(alarms)
        return alarm

    def remove(self, alarm_id: int) -> bool:
        """Remove by id. Returns ``False`` if no such id existed."""
        alarms = self.load()
        remaining = [a for a in alarms if a.id != alarm_id]
        if len(remaining) == len(alarms):
            return False
        self.save(remaining)
        return True

    def get(self, alarm_id: int) -> Optional[Alarm]:
        for a in self.load():
            if a.id == alarm_id:
                return a
        return None
