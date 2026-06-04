"""The ``alarm run`` watch-loop — a thin shell over ``core`` + ``store``.

This is the one place that reads the real wall clock and emits the bell. It
contains orchestration and presentation only; every decision (is an alarm due?
what does a response mean? when is the next fire?) is delegated to ``core``.
Because the logic underneath is pure and unit-tested, this module is validated
by manual demo rather than unit tests.

Loop, each ~1s tick:
    now    = current time
    alarms = store.load()
    for each enabled alarm due now:
        ring it (banner + bell + blocking d / s [n] prompt)
        record last_fired; apply dismiss/snooze; persist
    print a heartbeat (next alarm + countdown)
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta

from . import core
from .model import Alarm
from .store import Store

TICK_SECONDS = 1
BELL = "\a"


def _now() -> datetime:
    """Wall-clock now, to the second. Isolated so the rest stays testable."""
    return datetime.now().replace(microsecond=0)


def _format_delta(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    if total < 0:
        return "now"
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _heartbeat(alarms: list[Alarm], now: datetime) -> str:
    """One-line status: the soonest upcoming alarm and the countdown to it."""
    upcoming: list[tuple[datetime, Alarm]] = []
    for a in alarms:
        nxt = core.next_fire_time(a, now)
        if nxt is not None:
            upcoming.append((nxt, a))
    if not upcoming:
        return "no upcoming alarms — waiting (Ctrl-C to quit)"
    nxt, a = min(upcoming, key=lambda pair: pair[0])
    label = a.label or "(no label)"
    return f"next: {label} at {a.time} (in {_format_delta(nxt - now)})"


def ring(alarm: Alarm, input_fn=input, output=sys.stdout) -> core.Action:
    """Ring until the user responds with a valid dismiss/snooze.

    Prints a banner, then loops: beep + blocking prompt. Invalid/empty input
    re-beeps and re-prompts (so the alarm can't be left in a limbo state).
    ``input_fn``/``output`` are injectable to keep this drivable in a harness.
    """
    label = alarm.label or "(no label)"
    banner = (
        "\n"
        "  ┌─────────────────────────────────────┐\n"
        f"  │  ⏰  ALARM — {label:<24.24} │\n"
        f"  │      {alarm.time:<31} │\n"
        "  └─────────────────────────────────────┘"
    )
    print(banner, file=output)
    while True:
        output.write(BELL)
        output.flush()
        try:
            response = input_fn("  [d]ismiss  /  [s]nooze 5m  (s <n> for n min): ")
        except EOFError:
            # No interactive input available — treat as dismiss so we don't spin.
            return core.Dismiss()
        action = core.parse_response(response)
        if isinstance(action, core.Invalid):
            print("  ? please type 'd' to dismiss or 's' / 's <minutes>' to snooze",
                  file=output)
            continue
        return action


def _apply(alarm: Alarm, action: core.Action, occurrence: datetime, now: datetime) -> None:
    """Mutate the alarm in place to reflect the user's ring response."""
    # Record this occurrence so the same minute can't re-fire (double-fire guard).
    if occurrence is not None:
        alarm.last_fired = occurrence

    if isinstance(action, core.Snooze):
        alarm.snoozed_until = (now + timedelta(minutes=action.minutes)).replace(
            second=0, microsecond=0
        )
    elif isinstance(action, core.Dismiss):
        alarm.snoozed_until = None
        if alarm.is_one_shot:
            alarm.enabled = False  # a one-shot is spent once dismissed


def tick(store: Store, now: datetime, input_fn=input, output=sys.stdout) -> None:
    """Process a single tick: fire any due alarms and persist changes.

    Factored out of the loop so the per-tick behavior is exercisable directly.
    """
    # Reload every tick (not cached) so alarms added/removed from another
    # terminal show up live within ~1s. The file is tiny, so the re-parse cost
    # is negligible; caching would buy nothing and risk a stale view.
    alarms = store.load()
    changed = False
    for alarm in alarms:
        if not alarm.enabled:
            continue
        if core.due(alarm, now):
            occurrence = core.occurrence_target(alarm, now)
            action = ring(alarm, input_fn=input_fn, output=output)
            _apply(alarm, action, occurrence, now)
            changed = True
    if changed:
        store.save(alarms)


def run(store: Store, output=sys.stdout) -> None:
    """Blocking watch-loop. Runs until interrupted (Ctrl-C)."""
    print("alarm clock running — Ctrl-C to stop", file=output)
    try:
        while True:
            now = _now()
            tick(store, now, output=output)
            # Reload for an accurate heartbeat after any firing/persisting.
            output.write("\r\033[K")  # clear the status line
            output.write(_heartbeat(store.load(), now))
            output.flush()
            time.sleep(TICK_SECONDS)
    except KeyboardInterrupt:
        print("\nstopped.", file=output)
