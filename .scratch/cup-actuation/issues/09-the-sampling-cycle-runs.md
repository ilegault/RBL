# 09: The sampling cycle runs

**What to build:** An operator sets a period and a dwell, starts the cycle, and walks
away. Every period the cup inserts, holds for the dwell, and retracts. Each insertion
produces one acquisition run. Manual insert and retract still work and take precedence
immediately.

This ticket makes the cycle move. Making it safe to leave alone for eight hours is
ticket 10.

**Blocked by:** 05, 06

**Status:** ready-for-agent

**Read `docs/adr/0003-commanded-and-confirmed-cup-position.md` first.**
`docs/adr/0001-tests-first-and-no-muted-failures.md` is binding.

## Structural requirements

- **The scheduler is a pure object in `rbl/services/`. It receives the current time as
  an argument and returns the action to take. It does not import Qt and it does not call
  the clock.** This is a hard requirement, not a preference: an eight-hour cycle whose
  behaviour depends on a real clock cannot be tested, and a cycle that has never been
  tested across a fault is a cycle that will fail silently across a fault. An
  implementer who buries a `time.monotonic()` inside it has removed the only way this
  feature is ever verified.
- **Disarmed by default.** Arming is an explicit operator action, never a side effect of
  connecting, of opening the tab, or of a previous session's saved state.
- **A manual insert or retract takes precedence immediately** — not at the next tick, not
  after the current dwell. The application never stands between the operator and the cup.
- **A scheduled insertion that falls due while a manual run is open is skipped and
  recorded as skipped, not queued.** A queue means the cup moves at a moment nobody
  asked for, some time after the operator has stopped paying attention to why.
- **The cup spends as little time in the beam as possible.** The dwell is the whole of
  the insertion; nothing else lengthens it.

New configuration in `rbl/config/cup_config.py`: cycle period, `300.0` s default; cycle
dwell, `3.0` s default. Period and dwell are operator-editable from the tab using
`gui/widgets/inputs.py`, never a bare spin box — the plain widget re-interprets its text
on every keystroke and mangles typed values.

- [ ] A pure scheduler object in `rbl/services/` that takes a timestamp and returns the
      action to take
- [ ] It imports no Qt and calls no clock; `grep` for `time.` and `PySide6` in the new
      module returns nothing
- [ ] The cycle is disarmed on construction and after every application start
- [ ] Armed, it commands insert at each period boundary, retract after the dwell, and
      repeats
- [ ] Each insertion produces exactly one acquisition run through ticket 06's
      position-confirmed boundaries
- [ ] Period and dwell are editable from the tab via `gui/widgets/inputs.py` and take
      effect at the next period boundary, not mid-insertion
- [ ] Stop is available at any time and retracts the cup if it is in
- [ ] A manual insert or retract overrides the cycle immediately
- [ ] A scheduled insertion falling due during a manual run is skipped and the skip is
      recorded; it is not queued
- [ ] The tab shows cycle state and time to next insertion
- [ ] Scheduler tests fed an explicit timestamp series cover: a full eight-hour cycle at
      the default 300 s period, asserting the insertion count and the time of each; a
      manual insert during an armed cycle; a scheduled insertion falling due during a
      manual run, asserting it is skipped and that the following one is not; a period
      changed mid-cycle
- [ ] No test in this ticket sleeps, and the eight-hour test runs in milliseconds
- [ ] `ruff check .`, `python scripts/check_tests_first.py`, `python tools/type_gate.py`
      and `pytest --tb=short -q -n auto --dist loadfile` all pass
