# Alarm Clock CLI

A small, dependency-free command-line alarm clock in Python. Set one-off and
recurring alarms, list and remove them, and run a watcher that rings when an
alarm is due — with a snooze that survives a process restart.

> Built as a 30-minute take-home. The design was deliberately scoped to show
> engineering judgment over feature count. The reasoning behind every decision —
> including what was intentionally left out — lives in [`docs/prd.md`](docs/prd.md)
> (concise) and [`docs/spec.md`](docs/spec.md) (the module design).

## Requirements

- Python 3.10+ (uses `set[int]` / `X | Y` typing). No third-party packages.

## Usage

Run via the module (no install needed):

```bash
python3 -m alarm <command>
```

### Commands

```bash
# Add a recurring alarm (Mon–Fri at 07:30)
python3 -m alarm add 07:30 --repeat weekdays --label "Standup"

# Add a daily alarm
python3 -m alarm add 09:00 --repeat daily --label "Work"

# Add a one-off for the next occurrence of 22:00 (today if ahead, else tomorrow)
python3 -m alarm add 22:00 --label "Wind down"

# Add a one-off pinned to an explicit date
python3 -m alarm add 06:00 --date 2026-12-25 --label "Christmas"

# List all alarms (shows id, time, repeat, enabled, next fire, label)
python3 -m alarm list

# Remove an alarm by id
python3 -m alarm remove 3

# Run the watcher — blocks, ticks once a second, rings when due (Ctrl-C to stop)
python3 -m alarm run
```

When an alarm fires, the watcher shows a banner, sounds the terminal bell, and
prompts:

```
  [d]ismiss  /  [s]nooze 5m  (s <n> for n min):
```

- `d` — dismiss (a one-off alarm is then done; a recurring one re-arms for its
  next occurrence).
- `s` — snooze 5 minutes.
- `s 10` — snooze 10 minutes.

Alarms are stored as JSON at `~/.alarm_clock/alarms.json`. Override the location
with `ALARM_CLOCK_FILE=/path/to/alarms.json` (useful for multiple profiles or
keeping experiments out of your real schedule). Writes are atomic, so a crash
mid-write can't corrupt the file; if the file is corrupted some other way (a bad
hand-edit, a stray process), commands fail with a clear message naming the file
rather than a traceback.

## Design

Single process, three layers, **no concurrency**:

```
cli (argparse)  →  core (pure: no I/O, no clock)  →  store (JSON, atomic write)
                                                  ↘  model (Alarm type)
runner  — the `run` watch-loop; a thin consumer of core + store
```

- **`core`** holds every time/recurrence/snooze decision as **pure functions**
  of `(alarm, now)`. No `datetime.now()` inside — `now` is passed in. This is
  where the real logic lives and where the tests concentrate; edge cases are
  exercised by simply choosing a `now`, with no sleeping or clock-mocking.
- **`store`** owns persistence with a **crash-safe atomic write** (temp file +
  `os.replace`), so a crash mid-write can't corrupt the schedule.
- **`runner`** is orchestration/presentation only — it reads the real clock,
  asks `core` what's due, and rings. Thin enough to validate by demo.

### Key behaviors

- **Recurrence** is modeled internally as a set of weekdays (`days`), with
  `once`/`daily`/`weekdays` as friendly aliases. An empty set means a one-shot
  (which carries a concrete datetime). Adding `weekly:<days>` later is a
  CLI-parser change only — the model and logic already support arbitrary day sets.
- **Persisted snooze.** A snooze is stored on disk, so if the watcher crashes
  and restarts before the snooze is due, it still rings. A snooze that's already
  long past simply ages out (it falls outside the fire window) — the same rule
  every alarm uses, no special-casing.
- **Fires once per occurrence.** A `last_fired` marker prevents an alarm ringing
  repeatedly during the minute it's due, and prevents a re-ring if the watcher
  restarts within that minute.

## Tests

Standard library `unittest` — no install required:

```bash
python3 -m unittest discover -s tests -v
```

53 tests cover the high-risk surface:
- **`core`** — weekday skip, daily roll-over, midnight wrap, one-shot resolution,
  fire-window boundaries, the double-fire guard, snooze-within-window vs.
  stale-snooze-ages-out, snooze as the displayed next-fire time, and ring-prompt
  parsing.
- **`store`** — round-trip fidelity, id assignment, removal, that a failed write
  leaves the previous file intact (atomicity), and that a corrupt/hand-edited
  file surfaces a clean error rather than a traceback.
- **`cli`** — subprocess smoke tests of `add`/`list`/`remove`, the input
  validations (bad time, past date, `--date` with a recurring repeat), and the
  clean corrupt-file message.

The `runner` watch-loop is intentionally not unit-tested — it's a thin shell
over the tested core; its timing/blocking behavior is validated by running it.

## Limitations & production path

These are deliberate scoping choices for the time box, each with the upgrade path
mapped (see [`docs/prd.md`](docs/prd.md) for detail):

- **Fires only while `alarm run` is active; does not survive reboot.** This is a
  *clock you run*, not an always-on system alarm. The production answer is to
  hand the next fire to the **OS scheduler** (`launchd` / `systemd` / `cron`),
  which wakes a short-lived `alarm ring` process. The three-layer seam lets that
  backend drop in without touching the core.
- **The fire window is one minute and does not catch up.** A fire is missed if
  the loop is blocked on a ring prompt, or the machine is asleep, across the
  whole target minute — single-threaded by design, so a ringing alarm pauses the
  watch of all others. The OS-scheduler backend above is also the fix for this:
  a wake-from-sleep timer doesn't depend on a process being unblocked at the
  exact minute. (A persisted snooze survives a *crash*, but a stale snooze ages
  out by the same window rule — it is not a general catch-up mechanism.)
- **Terminal bell only** (no audio files), **local time only** (no timezone/DST),
  single user, single machine.
```
