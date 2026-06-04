# Alarm Clock CLI — Technical Spec

Engineering design for the alarm clock CLI. The *what/why* lives in `prd.md`;
this document is the *how* — the modules, their responsibilities, their
interfaces, and the dependencies between them. Signatures below are
**illustrative** (they show the shape of each seam, not final code).

## Design principles

- **Three layers, one process, no concurrency.** `cli → core → store`, with
  `runner` as a thin consumer of `core` + `store`. (Rationale and rejected
  alternatives: `prd.md`.)
- **Push behavior down into a pure, deep core.** All time/recurrence/snooze
  decisions are pure functions of `(alarm, now)` — no I/O, no `datetime.now()`
  inside. This is where the engineering value and the tests concentrate.
- **`now` is an internal argument, never a CLI flag.** Testability comes from
  design, not from test-only surface area.
- **Each module has a small interface that rarely changes.** Adding an
  OS-scheduler firing backend, or a `weekly:<days>` recurrence, should touch one
  module, not ripple across the system.

## Module map

```
            ┌──────────┐
            │   cli    │  argparse; validates input, prints output
            └────┬─────┘
        add/list/remove │ run
            ┌────┴─────┐
            │  runner  │  blocking watch-loop, ring/bell/prompt, heartbeat
            └────┬─────┘
        due()/next_fire() │ load()/save()
        ┌─────────┴──────────┐
        │                    │
   ┌────┴────┐          ┌────┴────┐
   │  core   │  pure    │  store  │  JSON + atomic write
   └────┬────┘          └────┬────┘
        │   Alarm (type)     │
        └────────┬───────────┘
            ┌────┴────┐
            │  model  │  Alarm dataclass + (de)serialization
            └─────────┘
```

Dependency direction points **inward/downward**: `cli` and `runner` depend on
`core`/`store`; `core` and `store` depend on `model`; `model` depends on nothing
but the stdlib. `core` has **no** dependency on `store`, the filesystem, or the
clock — that's what makes it deep and trivially testable.

---

## `model` — the Alarm type

**Responsibility:** define the single domain entity and convert it to/from a
plain dict for persistence. No behavior beyond representation.

**Why it's its own module:** both `core` (reasons over alarms) and `store`
(persists them) need the type, but neither should own serialization. Keeping it
separate avoids coupling the domain type to the filesystem.

**Fields:** `id`, `time` (`HH:MM`), `label`, `days: set[int]` (Mon=0…Sun=6;
**empty = one-shot**), `when` (one-shot only, else null), `enabled`,
`snoozed_until` (null normally), `last_fired` (double-fire guard).

**Illustrative interface**
```
Alarm                      # dataclass holding the fields above
Alarm.to_dict() -> dict
Alarm.from_dict(d) -> Alarm
```

**Use cases**
- `store` serializes/deserializes the JSON array through `to_dict`/`from_dict`.
- `core` and `runner` read fields to make decisions and render output.

---

## `core` — the domain logic (deep module, primary test target)

**Responsibility:** every time/recurrence/snooze/parse decision, as **pure
functions**. Given an alarm and a supplied `now`, answer: when does it next fire,
is it due, and how should a ring-prompt response be interpreted.

**Why it's deep:** rich, subtle behavior (weekday math, midnight wrap, snooze
windows, the single-fire-per-occurrence guarantee) behind a tiny interface that
rarely changes. No I/O and no wall-clock means every edge case is testable by
passing a chosen `now` — no sleeping, no clock-mocking.

**Illustrative interface**
```
next_fire_time(alarm, now) -> datetime | None
    # next datetime the alarm should fire at, given now:
    #   one-shot (days empty)  -> alarm.when if still future, else None
    #   recurring              -> soonest future datetime whose HH:MM matches
    #                             AND whose weekday ∈ days (handles midnight wrap)

due(alarm, now) -> bool
    # effective target = snoozed_until if pending, else next scheduled occurrence
    # due iff target within the current fire window AND occurrence != last_fired

parse_response(text) -> Action
    # 'd' -> Dismiss; 's' -> Snooze(5); 's <n>' -> Snooze(n); else -> Invalid
```

**Key rules owned here**
- **Fire window:** `[start-of-current-minute, now]`. A target older than the
  window is not due — this is how a stale snooze / late-started loop ages out,
  with no special case.
- **Double-fire guard:** `due()` consults `last_fired` so an occurrence fires
  once, not once per ~1s tick, and never re-fires after a same-minute restart.
- **Recurrence generality:** operates on the `days` set, so a future
  `weekly:<days>` needs no change here — only the CLI parser.

**Use cases**
- `runner` calls `due()` each tick and `next_fire_time()` for the heartbeat.
- `runner` calls `parse_response()` to interpret what the user typed at a ring.
- The CLI parser uses `next_fire_time()` semantics to resolve a one-shot's
  default datetime (next occurrence of `HH:MM`).

---

## `store` — persistence (tested)

**Responsibility:** own the on-disk JSON array of alarms — load, save, id
assignment, add, remove — with a **crash-safe** write. The only module that
touches the filesystem for alarm state.

**Why it's a clean seam:** persistence concerns (atomicity, id allocation, file
location) are isolated from both the domain logic and the CLI. Swapping the
backing format later changes only this module.

**Illustrative interface**
```
load() -> list[Alarm]
save(alarms) -> None          # write temp file, then os.replace (atomic)
add(time, label, days, when) -> Alarm       # assigns next id, persists
remove(id) -> bool            # False if id absent
```

**Key rules owned here**
- **Atomic write:** serialize to a temp file, `os.replace` into place — a crash
  mid-write can never corrupt the existing schedule.
- **File location:** `~/.alarm_clock/alarms.json` (created on first use).
- **Id assignment:** stable, monotonic next-id.
- **Re-read per tick, not cached:** `runner` reloads on every tick rather than
  caching in memory, so alarms added/removed by a separate `alarm` invocation
  appear within ~1s without restarting `run`. At this scale (a small JSON file)
  the re-parse is negligible; caching would trade that for a stale view and
  cache-invalidation complexity, for no measurable gain.

**Use cases**
- `cli` add/list/remove map almost directly onto these calls.
- `runner` calls `load()` each tick and `save()` after a ring mutates state
  (set `last_fired`, set/clear `snoozed_until`, disable a fired one-shot).

---

## `runner` — the watch-loop (manual demo, not unit-tested)

**Responsibility:** the `alarm run` experience. Tick (~1s); load alarms; for each
enabled alarm ask `core.due()`; on a hit, ring (banner + terminal bell + blocking
prompt); apply the parsed action; persist; print a heartbeat. Run until Ctrl-C.

**Why it's a thin shell:** it contains orchestration and presentation, not
decisions — all judgment is delegated to `core`. Intentionally not unit-tested
(its blocking/timing nature is awkward to test and the logic underneath is
already covered); validated by the screen-recording demo.

**Illustrative flow**
```
run():
  loop every ~1s:
    now    = current time
    alarms = store.load()
    for a in alarms where a.enabled:
        if core.due(a, now):
            action = ring(a)                 # banner + bell + blocking prompt
            a.last_fired = occurrence_of(a, now)
            if action is Dismiss:  clear snooze; disable a if one-shot
            if action is Snooze(n): a.snoozed_until = now + n minutes
            store.save(alarms)
    print heartbeat(next alarm + countdown)
```

**Use cases**
- The only place real time is read and the bell is emitted.
- Owns the dismiss/snooze state transitions (using `core`'s decisions).

---

## `cli` — command interface (subprocess smoke test)

**Responsibility:** parse `argparse` subcommands, validate user input, translate
to `store`/`runner` calls, and format output/errors.

**Why it's a thin shell:** it holds no domain logic — it's a translation layer.
Lightly smoke-tested via subprocess to confirm the wiring (output + exit codes).

**Illustrative interface**
```
main(argv) -> exit_code
  add    <HH:MM> [--label] [--repeat once|daily|weekdays] [--date YYYY-MM-DD]
  list
  remove <id>
  run
```

**Key rules owned here**
- Reject invalid `HH:MM`; reject a **past** one-shot datetime; reject `--date`
  combined with `--repeat daily|weekdays`.
- Translate friendly `--repeat` keywords into the `days` set (`once`→∅,
  `daily`→{0..6}, `weekdays`→{0..4}). **This is the only place that changes** to
  later support `weekly:<days>`.
- Resolve a one-shot's default datetime (next occurrence of `HH:MM`) when no
  `--date` is given.

**Use cases**
- `add`/`list`/`remove` → corresponding `store` calls.
- `run` → hands off to `runner.run()`.

---

## Where the engineering value concentrates

| Module | Depth | Tested | Why it matters |
|---|---|---|---|
| **`core`** | Deep | ✅ unit | All the hard, bug-prone logic, pure and isolated |
| **`store`** | Medium | ✅ unit | Crash-safe persistence; clean backend seam |
| **`model`** | Shallow | (via store) | Decouples the type from persistence |
| **`runner`** | Shallow | manual demo | Orchestration/presentation only |
| **`cli`** | Shallow | ✅ smoke | Translation layer; the only recurrence-parser seam |

The seams are chosen so the two most likely future changes each touch exactly
one module: an **OS-scheduler firing backend** replaces `runner`'s loop without
touching `core`/`store`; **`weekly:<days>` recurrence** changes only the `cli`
parser, because `core` already reasons over arbitrary `days` sets.
