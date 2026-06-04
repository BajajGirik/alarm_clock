"""The Alarm domain entity and its (de)serialization.

This module owns *representation only* — no behavior, no I/O. Both `core`
(which reasons over alarms) and `store` (which persists them) depend on this
type, so keeping serialization here avoids coupling the domain type to the
filesystem.

Recurrence is modeled as ``days`` — the set of weekdays an alarm fires on
(Mon=0 .. Sun=6, matching ``datetime.weekday()``). An **empty set means a
one-shot** alarm, which instead carries a concrete ``when`` datetime. This
general representation means a future ``weekly:<days>`` recurrence needs no
change here — only the CLI parser that produces the day set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Alarm:
    """A single alarm.

    Fields
    ------
    id:            stable integer identifier, assigned by the store
    time:          "HH:MM" — the clock time the alarm fires at
    label:         human description, shown when it rings
    days:          weekday set (Mon=0..Sun=6); **empty = one-shot**
    when:          concrete fire datetime for a one-shot; ``None`` for recurring
    enabled:       disabled alarms are kept on disk but skipped by the fire rule
    snoozed_until: an override fire datetime set when snoozed; persisted so a
                   snooze survives a restart; ``None`` normally
    last_fired:    the occurrence datetime most recently rung — the guard that
                   makes an alarm fire once per occurrence, not once per tick
    """

    id: int
    time: str
    label: str = ""
    days: set[int] = field(default_factory=set)
    when: Optional[datetime] = None
    enabled: bool = True
    snoozed_until: Optional[datetime] = None
    last_fired: Optional[datetime] = None

    @property
    def is_one_shot(self) -> bool:
        return not self.days

    @property
    def repeat_label(self) -> str:
        """Friendly description of the recurrence, for display."""
        if self.is_one_shot:
            return "once"
        if self.days == {0, 1, 2, 3, 4, 5, 6}:
            return "daily"
        if self.days == {0, 1, 2, 3, 4}:
            return "weekdays"
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        return ",".join(names[d] for d in sorted(self.days))

    # -- serialization -----------------------------------------------------
    # Sets and datetimes are not JSON-native, so convert at this boundary:
    # days -> sorted list, datetimes -> ISO strings (or null).

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "time": self.time,
            "label": self.label,
            "days": sorted(self.days),
            "when": self.when.isoformat() if self.when else None,
            "enabled": self.enabled,
            "snoozed_until": (
                self.snoozed_until.isoformat() if self.snoozed_until else None
            ),
            "last_fired": self.last_fired.isoformat() if self.last_fired else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Alarm":
        def parse(value: Optional[str]) -> Optional[datetime]:
            return datetime.fromisoformat(value) if value else None

        return cls(
            id=d["id"],
            time=d["time"],
            label=d.get("label", ""),
            days=set(d.get("days", [])),
            when=parse(d.get("when")),
            enabled=d.get("enabled", True),
            snoozed_until=parse(d.get("snoozed_until")),
            last_fired=parse(d.get("last_fired")),
        )
