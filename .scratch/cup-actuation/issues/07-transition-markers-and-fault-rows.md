# 07: Position transitions and faults in the record

**What to build:** Every confirmed cup movement and every fault lands in the session file
as it happens, so a specimen's interrupted exposure is reconstructible afterwards and a
fault that occurred at 3 a.m. during an unattended run is still there in the morning.

**Blocked by:** 05, 06

**Status:** done

**Read `docs/adr/0003-commanded-and-confirmed-cup-position.md` first.**
`docs/adr/0001-tests-first-and-no-muted-failures.md` is binding.

## Structural requirements

- `cup_session_writer.py` today writes sample rows, lifecycle markers, per-run sample
  counts and session metadata. This ticket extends that schema; it does not fork it.
  Every new row goes through the existing `_write_row` path so it inherits the
  flush-after-every-row behaviour, and for the same reason: an eight-hour run that dies
  at hour seven must leave seven hours of usable record.
- **The logging is consistent or it is useless.** A transition marker's timestamp and a
  sample row's timestamp must be the same clock and the same format, so a reader can
  interleave them without guessing. State that in the module docstring.
- One `record_type` value per new row kind, added to the existing vocabulary rather than
  overloading `details`.

## The rows

**Position transition** — every confirmed IN and every confirmed OUT, carrying the
confirmed timestamp and the position transitioned to.

**Fault rows**, one `record_type` each:

- move commanded but not confirmed within the timeout, naming which move
- controller not in AUTO
- impossible status combination (both contacts asserted)
- position-versus-current disagreement, carrying both readings

- [x] `CSV_COLUMNS` is extended, not replaced; existing columns keep their meaning and
      position
- [x] Every confirmed IN and OUT writes a transition row with its confirmed timestamp
- [x] Each of the four fault kinds writes a distinctly typed row carrying enough detail
      to diagnose it without the application running
- [x] A disagreement row carries the confirmed position and the measured current that
      disagreed with it, both, in the same row
- [x] Every new row flushes immediately, like the existing ones
- [x] The CSV comment header describes the new row kinds
- [x] A test writes a transition and a sample in the same session and asserts their
      timestamps are the same clock and the same format
- [x] A test asserts the file survives a simulated mid-run abort with every row written
      before the abort intact and parseable
- [x] A test reads back a file containing all four fault kinds and asserts each is
      distinguishable by `record_type` alone
- [x] The module docstring's `WHY THIS EXISTS` is updated: it currently says the cup is
      inserted manually and carries no position sensor, which is no longer true
- [x] `ruff check .`, `python scripts/check_tests_first.py`, `python tools/type_gate.py`
      and `pytest --tb=short -q -n auto --dist loadfile` all pass

## Comments

### 2026-09-16 — Implementation summary
- Extended `CSV_COLUMNS` in `cup_session_writer.py` by appending `"position"` (preserving positions and meanings of the existing 9 columns).
- Added `write_position_transition`, `write_fault_move_not_confirmed`, `write_fault_controller_not_in_auto`, `write_fault_impossible_status`, and `write_fault_disagreement` to `CupSessionWriter`. Each row flushes immediately and carries distinct `record_type` and diagnostic fields. Disagreement row carries both confirmed position and measured current.
- Documented clock and timing format consistency (`f"{t_host:.6f}"`) in docstring and updated `WHY THIS EXISTS` to cite ADR 0003.
- Described new row kinds in CSV comment header (`# record types: ...`).
- Connected `FaradayCupTab` to write confirmed transitions and faults into `CupSessionWriter` via edge-triggered state tracking.
- Added comprehensive unit and integration tests in `tests/test_cup_session_writer.py` covering position transitions, all four distinct fault kinds, clock format consistency, simulated crash/abort survival, and tab integration.
- All gates passed: `ruff check .` (clean), `python scripts/check_tests_first.py` (passed), `python tools/type_gate.py` (0 hard, 139 soft ratchet), and full test suite `pytest` (1824 passed, 0 failures).
