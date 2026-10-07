# 35: Amplifier assignment history: initial assignment, swaps and hardware changes

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 34

**Spec:** `.scratch/amplifier-load-and-spikes/spec.md` (Part A)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `CONTEXT.md` (Amplifier, Plate position, Amplifier assignment, Amplifier swap, Hardware change)

**Read first:** `src/rbl/services/trip_history.py` (`append_trip` - copy its append-only JSONL write and tolerant read), `src/rbl/config/paths.py` (`AMPLIFIER_ASSIGNMENTS_STORE` from 34).

## What to build

A new pure module `src/rbl/config/amplifier_assignments.py`. No Qt. It never reads a clock:
every function that needs a time takes it as an argument.

- `record_assignment(mapping: dict[str, str], when: datetime, kind: str, note: str = "")`
  appends one JSON line `{"kind": "initial"|"swap", "when": iso, "mapping": {"X+": serial, ...},
  "note": ...}`. `mapping` must name all four plate positions (`AMP_LABELS`), otherwise
  `ValueError` and nothing is written.
- `record_hardware_change(when: datetime, note: str)` appends `{"kind": "hardware_change", ...}`.
- `assignment_at(when) -> dict | None`: the mapping in force at `when` (the latest
  `initial`/`swap` at or before it), or `None` before any.
- `current_assignment(now) -> dict | None`, `latest_hardware_change(now) -> datetime | None`.
- `history() -> list[dict]` in file order. A corrupt line is skipped with a log warning, never
  raised (as `trip_history` does). Records are never edited or deleted.

Tests may fake: nothing. Use the temp path from 34's fixture and plain datetimes.

## Acceptance criteria

- [x] After an `initial` at day 1 and a `swap` at day 10 that exchanges X+ and Y+, `assignment_at(day 5)['X+']` is the day-1 X+ serial and `assignment_at(day 11)['X+']` is the day-1 Y+ serial.
- [x] `assignment_at` before the first record returns `None`; `record_assignment` with three positions raises `ValueError` and the file's line count is unchanged.
- [x] `latest_hardware_change(now)` returns the later of two recorded changes and ignores one dated after `now`.
- [x] A line of invalid JSON between two valid records: `history()` returns the two valid records and does not raise.
- [x] After three writes the file has exactly three lines and the first line is unchanged byte for byte.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments

2026-10-07: Done. `config/amplifier_assignments.py` (record_assignment, record_hardware_change, assignment_at, current_assignment, latest_hardware_change, history); tests in `tests/test_amplifier_assignments.py`, one per criterion plus malformed mappings, unknown kinds, call-time path lookup and naive/aware times. Writes raise on failure (a swap that was not recorded would corrupt every later answer); only reads are tolerant. The store file location is `paths.AMPLIFIER_ASSIGNMENTS_STORE`, read at call time.
