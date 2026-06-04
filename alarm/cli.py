"""Command-line interface — the translation layer.

Parses subcommands, validates user input, and maps friendly options onto
``store`` / ``runner`` calls. Holds no domain logic: the recurrence keyword ->
``days`` mapping and the time/date validation live here, everything else is
delegated. This is the only module that changes to add a future
``weekly:<days>`` keyword.

Commands:
    alarm add <HH:MM> [--label TEXT] [--repeat once|daily|weekdays] [--date YYYY-MM-DD]
    alarm list
    alarm remove <id>
    alarm run
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from . import core, runner
from .store import ENV_VAR, CorruptStoreError, Store, default_path

# The only place friendly recurrence keywords become weekday sets.
REPEAT_TO_DAYS = {
    "once": set(),
    "daily": {0, 1, 2, 3, 4, 5, 6},
    "weekdays": {0, 1, 2, 3, 4},
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alarm", description="A CLI alarm clock.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="add an alarm")
    p_add.add_argument("time", help="time of day, HH:MM (24-hour)")
    p_add.add_argument("--label", default="", help="a description for the alarm")
    p_add.add_argument(
        "--repeat",
        choices=list(REPEAT_TO_DAYS),
        default="once",
        help="recurrence (default: once)",
    )
    p_add.add_argument(
        "--date",
        help="for a one-shot, an explicit date YYYY-MM-DD (implies --repeat once)",
    )

    sub.add_parser("list", help="list all alarms")

    p_remove = sub.add_parser("remove", help="remove an alarm by id")
    p_remove.add_argument("id", type=int, help="the alarm id (see `alarm list`)")

    sub.add_parser("run", help="run the watch-loop and ring alarms as they come due")

    return parser


def _cmd_add(args, store: Store, now: datetime, out) -> int:
    # Validate the time first — clear error for "25:99" etc.
    try:
        core.parse_hhmm(args.time)
    except ValueError as e:
        print(f"error: {e}", file=out)
        return 2

    days = REPEAT_TO_DAYS[args.repeat]

    # --date only makes sense for a one-shot. Reject combining it with a
    # recurring repeat so the alarm's meaning is never ambiguous.
    if args.date is not None and args.repeat != "once":
        print("error: --date cannot be combined with --repeat "
              f"{args.repeat}; a date implies a one-shot alarm", file=out)
        return 2

    when = None
    if args.repeat == "once":
        if args.date is not None:
            try:
                day = datetime.strptime(args.date, "%Y-%m-%d").date()
            except ValueError:
                print(f"error: invalid date {args.date!r}: expected YYYY-MM-DD",
                      file=out)
                return 2
            hour, minute = core.parse_hhmm(args.time)
            when = datetime(day.year, day.month, day.day, hour, minute)
            if when <= now:
                print("error: that date/time is in the past", file=out)
                return 2
        else:
            # No explicit date: resolve to the next future occurrence of HH:MM.
            when = core.next_occurrence_of(args.time, now)

    alarm = store.add(time=args.time, label=args.label, days=days, when=when)
    print(f"added alarm {alarm.id}: {alarm.time} "
          f"[{alarm.repeat_label}]"
          + (f" — {alarm.label}" if alarm.label else ""), file=out)
    return 0


def _cmd_list(store: Store, now: datetime, out) -> int:
    alarms = store.load()
    if not alarms:
        print("no alarms set.", file=out)
        return 0
    print(f"{'ID':<3} {'TIME':<6} {'REPEAT':<10} {'ON':<4} {'NEXT':<20} LABEL",
          file=out)
    for a in alarms:
        nxt = core.next_fire_time(a, now)
        nxt_str = nxt.strftime("%Y-%m-%d %H:%M") if nxt else "-"
        snoozed = " (snoozed)" if a.snoozed_until else ""
        print(f"{a.id:<3} {a.time:<6} {a.repeat_label:<10} "
              f"{'yes' if a.enabled else 'no':<4} {nxt_str:<20} "
              f"{a.label}{snoozed}", file=out)
    return 0


def _cmd_remove(args, store: Store, out) -> int:
    if store.remove(args.id):
        print(f"removed alarm {args.id}.", file=out)
        return 0
    print(f"error: no alarm with id {args.id}", file=out)
    return 1


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    store = Store(default_path())
    now = datetime.now().replace(microsecond=0)
    out = sys.stdout

    try:
        if args.command == "add":
            return _cmd_add(args, store, now, out)
        if args.command == "list":
            return _cmd_list(store, now, out)
        if args.command == "remove":
            return _cmd_remove(args, store, out)
        if args.command == "run":
            runner.run(store)
            return 0
    except CorruptStoreError as e:
        print(f"error: {e}", file=out)
        print(f"  fix or remove the file, then try again "
              f"(override its location with ${ENV_VAR}).", file=out)
        return 1
    return 2  # unreachable; argparse's required subcommand enforces a branch


if __name__ == "__main__":
    sys.exit(main())
