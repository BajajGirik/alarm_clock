# Alarm Clock CLI — PRD

A Python CLI alarm clock. No web UI, no database. Set, list, remove, and run
alarms; recurring + one-shot; snooze that survives a restart. Standard library
only; core logic is pure and unit-tested. Limitations and the production upgrade
path are stated explicitly at the end.

## Commands

```
alarm add <HH:MM> [--label TEXT] [--repeat once|daily|weekdays] [--date YYYY-MM-DD]
alarm list
alarm remove <id>
alarm run        # blocks, ticks ~1s, rings when due, until Ctrl-C
```

- `--date` implies one-shot; conflicts with `--repeat daily|weekdays`.
- Reject invalid `HH:MM` and **past** one-shot datetimes.

**Examples**
```
alarm add 07:30 --repeat weekdays --label "Standup"   # Mon–Fri at 07:30
alarm add 07:30                                       # one-shot, next 07:30
alarm add 06:00 --date 2026-06-10                     # one-shot, that exact day
alarm add 08:00 --date 2020-01-01   →  error: datetime is in the past
```

## Architecture

Single process, three layers, **no concurrency**:

```
CLI (argparse)  →  Core domain (PURE: no I/O, no clock)  →  Store (JSON, atomic write)
```

`run` is a thin blocking loop that consumes the same core + store as the CLI.
Current time is passed into the pure core as a `now` argument — internal, never
a CLI flag.

**Rejected (the decisions that matter):**
- *Two concurrent threads/services sharing the file* → premature distribution;
  file-locking + cache-coherence cost for zero benefit on a local single-user tool.
- *Self-daemonization* → fiddly, Unix-only, still doesn't survive reboot.
- *TUI / arrow-key menus* → dependency + untestable without a PTY harness.
- *Threaded continuous-nag bell* → reintroduces avoided concurrency.

## Recurrence model

Stored as `days: set[int]` (Mon=0…Sun=6) — general internally, friendly subset
exposed:

| keyword | `days` | meaning |
|---|---|---|
| `once` | `∅` | one-shot; carries a resolved `datetime`; disables after firing |
| `daily` | `{0..6}` | every day |
| `weekdays` | `{0..4}` | Mon–Fri |

`weekly:<days>` is a future **parser-only** change — model already supports it.

**Examples**
- `weekdays` alarm, today is Saturday → next fire is Monday (Sat/Sun skipped).
- `once 07:30` added at 20:00 → fires tomorrow 07:30, then disables.
- `daily 23:59` near midnight → next fire correctly rolls to the next day.

## Firing

One unified rule for normal, recurring, and snoozed alarms:

> **due** iff effective target (`snoozed_until` if set, else next scheduled
> occurrence) falls within the **current fire window** *and* that occurrence
> hasn't already fired.

- **Fire window** = `[start-of-current-minute, now]`. A target older than the
  window simply isn't due — this is how a stale snooze or a late-started loop
  ages out, with no special-casing.
- **Double-fire guard (`last_fired`):** before ringing, compare the occurrence's
  timestamp to `last_fired`; if equal, skip. Set `last_fired` on ring. This makes
  an alarm fire **once per occurrence**, not once per ~1s tick during its due
  minute, and is also what stops a recurring alarm re-ringing if `run` is
  restarted within the same minute.

### Snooze (persisted)
- Default **5 min**; `s <n>` overrides. One-shots snoozable (re-ring, then stay
  disabled after dismiss).
- Stored as `snoozed_until` (ISO) → **survives a crash**. Clears on **dismiss**,
  not on fire (re-snooze moves it forward).

**Crash-restart timeline**
```
07:30:00  daily alarm rings
07:30:04  user snoozes 5m   → snoozed_until = 07:35
07:31     run process crashes (snoozed_until persisted on disk)
07:34     run restarted      → snooze still in the future, pending
07:35     snooze fires  ✓
---
(if instead restarted at 07:50: target is older than the fire window → ages out,
 snooze dropped, alarm reverts to its normal schedule)
```

## Ring UX

Banner + terminal bell, then a **blocking** prompt (re-beep on invalid):
`d` = dismiss · `s` = snooze 5 · `s <n>` = snooze n min. Single-threaded.

## Data model

```json
{ "id": 1, "time": "07:30", "label": "Standup",
  "days": [0,1,2,3,4], "when": null,
  "enabled": true, "snoozed_until": null, "last_fired": null }
```

| field | meaning |
|---|---|
| `id` | stable integer, assigned at add |
| `time` | `HH:MM`; the daily clock time for recurring alarms |
| `days` | weekday set; **empty = one-shot** |
| `when` | ISO; set only for one-shot, else `null` |
| `enabled` | `false` alarms are skipped by the fire rule but kept in the file |
| `snoozed_until` | ISO override target; `null` normally; persisted across restart |
| `last_fired` | ISO of last fired occurrence; the double-fire guard |

JSON array at `~/.alarm_clock/alarms.json`. **Atomic write** (write temp file,
`os.replace`) so a crash mid-write can't corrupt state.

### Run-loop step sequence
```
loop every ~1s:
  now      = current time
  alarms   = store.load()
  for a in alarms where a.enabled:
      if core.due(a, now):
          ring(a)                 # banner + bell + blocking d / s [n] prompt
          a.last_fired = occurrence
          apply dismiss | snooze  # snooze → set snoozed_until
          store.save(alarms)
  print heartbeat (next alarm + countdown)
```

## Validation

Unit-test the **pure core** where bugs live, not the plumbing:

| test | expectation |
|---|---|
| `weekdays` on Saturday | next fire = Monday |
| `daily` past today's time | next fire = tomorrow |
| one-shot in the past | rejected at add |
| one-shot with `--date` | fires that exact day, then disables |
| midnight wrap (`23:59`) | next fire rolls to next day |
| snooze within window | fires |
| **stale snooze** (restart late) | ages out, not fired |
| **double-fire guard** | one ring per occurrence across many ticks |
| `parse_response` | `d`/`s`/`s 30`/invalid map correctly |
| persistence round-trip | add → save → reload equal |

Light subprocess smoke test for the CLI commands.

## Tooling

Python 3, **standard library only**. Tests via `unittest`. README covers usage,
design decisions, rejected alternatives, and how to run tests.

## Limitations & production path

Each limitation below is a deliberate decision to fit the time box, with the
upgrade path mapped — not an oversight.

- **Fires only while `alarm run` is active; does not survive reboot.** This is a
  *clock you run*, not an always-on system alarm. Making it fire unattended /
  across a reboot requires handing the next fire to the **OS scheduler**
  (`launchd` on macOS, `systemd` timer or `cron` on Linux), which wakes a
  short-lived `alarm ring <id>` process. The three-layer architecture isolates
  the firing mechanism behind a seam, so this backend drops in without touching
  the core domain. Scoped out because it is OS-specific and hard to test/demo in
  the time box.
- **Terminal-bell only, no audio files.** Universal and dependency-free. Real
  audio → shell out to `afplay`/`paplay` or a small library, behind the same seam.
- **Local system time only; no timezone/DST handling.** A shipping product needs
  tz-aware logic; out of scope here.
- **Single user, single machine, no networking.**
```
