"""Pure domain logic — the deep module.

Every time/recurrence/snooze decision lives here as a **pure function** of an
``Alarm`` and a supplied ``now``. There is no I/O and no call to
``datetime.now()`` inside this module: ``now`` is always passed in. That is what
makes the tricky behavior (weekday math, midnight wrap, snooze windows, the
fire-once-per-occurrence guarantee) testable by simply choosing a ``now`` — no
sleeping, no clock mocking.

Key concepts
------------
Fire window
    ``[start-of-current-minute, now]``. An alarm is due when its effective
    target datetime falls inside this window. A target older than the window is
    simply not due — this is how a stale snooze, or a loop started a little
    late, ages out with no special-casing.

Effective target
    ``snoozed_until`` if a snooze is pending, otherwise the scheduled occurrence
    for the current minute.

Double-fire guard
    ``due()`` compares the occurrence to ``alarm.last_fired``; equal means it has
    already rung, so it is not due again. This makes an alarm fire once per
    occurrence rather than once per ~1s tick, and prevents a re-ring if the loop
    restarts within the same minute.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Union

from .model import Alarm

DEFAULT_SNOOZE_MINUTES = 5


# ---------------------------------------------------------------------------
# Ring-prompt actions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Dismiss:
    pass


@dataclass(frozen=True)
class Snooze:
    minutes: int


@dataclass(frozen=True)
class Invalid:
    pass


Action = Union[Dismiss, Snooze, Invalid]


def parse_response(text: str) -> Action:
    """Interpret a ring-prompt response.

    ``d`` -> Dismiss; ``s`` -> Snooze(5); ``s <n>`` -> Snooze(n); else Invalid.
    """
    parts = text.strip().lower().split()
    if not parts:
        return Invalid()
    cmd = parts[0]
    if cmd in ("d", "dismiss"):
        return Dismiss()
    if cmd in ("s", "snooze"):
        if len(parts) == 1:
            return Snooze(DEFAULT_SNOOZE_MINUTES)
        try:
            minutes = int(parts[1])
        except ValueError:
            return Invalid()
        if minutes <= 0:
            return Invalid()
        return Snooze(minutes)
    return Invalid()


# ---------------------------------------------------------------------------
# Time parsing helpers (pure)
# ---------------------------------------------------------------------------


def parse_hhmm(text: str) -> tuple[int, int]:
    """Parse ``"HH:MM"`` into ``(hour, minute)``; raise ValueError if invalid."""
    if text.count(":") != 1:
        raise ValueError(f"invalid time {text!r}: expected HH:MM")
    hh, mm = text.split(":")
    if not (hh.isdigit() and mm.isdigit()):
        raise ValueError(f"invalid time {text!r}: expected HH:MM")
    hour, minute = int(hh), int(mm)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"invalid time {text!r}: hour 0-23, minute 0-59")
    return hour, minute


def next_occurrence_of(time_str: str, now: datetime) -> datetime:
    """Next datetime (>= now, ignoring weekday) matching ``HH:MM``.

    Used to resolve a one-shot's default fire time when no date is given.
    Returns today's occurrence if it is still in the future, else tomorrow's.
    """
    hour, minute = parse_hhmm(time_str)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


# ---------------------------------------------------------------------------
# Scheduling (pure)
# ---------------------------------------------------------------------------


def next_fire_time(alarm: Alarm, now: datetime) -> Optional[datetime]:
    """The next datetime this alarm should fire at, given ``now``.

    For display/heartbeat use. Ignores snooze (that is a runtime override
    handled by ``due``).

    one-shot  -> ``alarm.when`` if still in the future, else ``None`` (spent)
    recurring -> soonest future datetime whose ``HH:MM`` matches *and* whose
                 weekday is in ``alarm.days`` (handles midnight / week wrap)
    """
    if not alarm.enabled:
        return None

    if alarm.is_one_shot:
        if alarm.when is not None and alarm.when > now:
            return alarm.when
        return None

    hour, minute = parse_hhmm(alarm.time)
    # Scan up to 8 days out: covers "later today" through the full next week,
    # which is enough for any non-empty weekday set.
    for offset in range(0, 8):
        day = now.date() + timedelta(days=offset)
        candidate = datetime(day.year, day.month, day.day, hour, minute)
        if candidate > now and day.weekday() in alarm.days:
            return candidate
    return None


def _scheduled_occurrence_for_minute(alarm: Alarm, now: datetime) -> Optional[datetime]:
    """The recurrence/one-shot occurrence falling in the *current minute*.

    This is the occurrence the fire window is checked against — distinct from
    ``next_fire_time`` which looks strictly forward. Returns ``None`` if this
    minute is not a scheduled occurrence.
    """
    if alarm.is_one_shot:
        if alarm.when is None:
            return None
        target = alarm.when
    else:
        hour, minute = parse_hhmm(alarm.time)
        if now.weekday() not in alarm.days:
            return None
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target


def _window_start(now: datetime) -> datetime:
    """Start of the current minute — the lower bound of the fire window."""
    return now.replace(second=0, microsecond=0)


def _in_window(target: datetime, now: datetime) -> bool:
    """Is ``target`` within ``[start-of-current-minute, now]``?"""
    return _window_start(now) <= target <= now


def due(alarm: Alarm, now: datetime) -> bool:
    """Should this alarm ring right now?

    Due iff the effective target (snooze override if pending, else this
    minute's scheduled occurrence) falls within the fire window AND that
    occurrence has not already fired (the ``last_fired`` guard).
    """
    if not alarm.enabled:
        return False

    # A pending snooze takes precedence over the regular schedule.
    if alarm.snoozed_until is not None:
        target = alarm.snoozed_until
    else:
        target = _scheduled_occurrence_for_minute(alarm, now)
        if target is None:
            return False

    if not _in_window(target, now):
        return False

    # Double-fire guard: already rung this exact occurrence?
    if alarm.last_fired is not None and alarm.last_fired == target:
        return False

    return True


def occurrence_target(alarm: Alarm, now: datetime) -> Optional[datetime]:
    """The target an active ring corresponds to — recorded in ``last_fired``.

    Mirrors the precedence in ``due``: snooze override first, else the current
    minute's scheduled occurrence.
    """
    if alarm.snoozed_until is not None:
        return alarm.snoozed_until
    return _scheduled_occurrence_for_minute(alarm, now)
