# 06: Runs open and close on confirmed position

**What to build:** An acquisition run now begins the moment the cup's IN contact confirms
and ends the moment its OUT contact confirms, rather than being inferred from current.
That is what makes a three-second sampling insertion produce a usable run instead of one
that opens a second late and fills its tail with baseline.

Current inference is not retired. It stays in service for hand insertions and as a
cross-check, and where the two disagree the application records the disagreement rather
than picking a winner.

**Blocked by:** 01, 04

**Status:** done

**Read `docs/adr/0003-commanded-and-confirmed-cup-position.md` first**, decisions 1, 4
and 5, and `docs/adr/0002-cup-acquisition-triggered-by-current.md` decision 6, which this
cashes in. `docs/adr/0001-tests-first-and-no-muted-failures.md` is binding.

## Structural requirements

- **`CupAcquisitionStateMachine` does not change in this ticket.** Not one line. If it
  needs to, ticket 01's contract was wrong and this is an escalation, not a workaround.
  A state machine that chose its behaviour by which detector it held would destroy the
  seam ADR 0002 built and ADR 0003 relies on.
- `CupPositionDetector` lives in `rbl/services/cup_acquisition.py` beside `CupDetector`
  and exposes `cup_in_beam` identically. It is pure: no Qt, no clock, timestamps are
  inputs.
- **The position constants are a separate, separately named set.** `CUP_ARM_DEBOUNCE_S`
  and `CUP_RELEASE_INTERVAL_S` continue to own the current-inference path and are
  **never** applied to a commanded insertion. Do not reuse the existing four by lowering
  them — hand insertions still happen and still need that hysteresis, and one set of
  numbers serving two mechanisms is how the next person breaks both. The only
  debounce a confirmed transition gets is the `0.05` s contact debounce from ticket 02.

## The authority rule

Position is authoritative whenever the status contacts are readable and the controller is
in AUTO. Otherwise inference governs, so a hand insertion with the controller in LOCAL —
or while a diagnostic stream profile is running and `FIO_STATE` is absent — still records.

When both are available and they disagree — position confirms IN but current stays below
the arm threshold, or position confirms OUT while current stays above it — that
disagreement is surfaced as a fault. **It is not resolved, and neither source is silently
preferred.** Over one irradiation those rows are the first real evidence anyone will have
about whether `cup_config.py`'s threshold values are right, which is the point.

- [x] `CupPositionDetector` exists, takes ticket 01's reading structure, and exposes
      `cup_in_beam` with the same contract as `CupDetector`
- [x] It imports no Qt and calls no clock
- [x] A run opens on a confirmed IN transition and closes on a confirmed OUT transition,
      subject only to the contact debounce
- [x] `git diff` shows zero changes to `CupAcquisitionStateMachine`
- [x] A test drives `CupAcquisitionStateMachine` through both detectors against the same
      event series and asserts identical run-id sequencing and identical open/close
      transitions
- [x] A test asserts a one-second commanded insertion opens and closes exactly one run —
      the case ticket 01 pinned as impossible on the inference path
- [x] The authority rule is a pure function or a clearly named branch in the state layer,
      tested directly: position readable and AUTO -> position governs; `FIO_STATE`
      absent -> inference governs; not in AUTO -> inference governs
- [x] Disagreement between the two sources is detected and published as a fault in the
      snapshot, carrying both readings; nothing in the code resolves it
- [x] The Faraday Cup tab shows which source is currently authoritative
- [x] Docstrings in `cup_acquisition.py` and `cup_config.py` record why there are two
      constant sets and what breaks if they are merged
- [x] No test in this ticket sleeps
- [x] `ruff check .`, `python scripts/check_tests_first.py`, `python tools/type_gate.py`
      and `pytest --tb=short -q -n auto --dist loadfile` all pass

## Comments

- 2026-09-16: Completed ticket 06.
  - Added `CupPositionDetector` in `rbl/services/cup_acquisition.py`: pure class, no Qt/clock, reacts immediately to `CupPosition.IN` (open) and `CupPosition.OUT` (close); IN_TRANSIT/INDETERMINATE/None leave state unchanged. Contact debounce is already applied upstream by `CupActuationLinkMixin`, so no additional debounce is needed here.
  - Added `position_authoritative(reading: CupReading) -> bool`: pure function returning True when `confirmed_position is not None` and `auto_mode is True`.
  - Added `AuthorityDetector(CupDetector)`: composite subclass running both detectors in parallel, selecting the authoritative source, tracking disagreement. Inherits `CupDetector` for structural type compatibility with `CupAcquisitionStateMachine`.
  - Updated `cup_config.py` docstring with "TWO CONSTANT SETS — DO NOT MERGE" rationale.
  - `FaradayCupTab`: uses `AuthorityDetector` in its state machine; builds richer `CupReading` in `on_cup_state()` including `confirmed_position` and `auto_mode` from the latest actuation state; displays active source in `lbl_source`; shows disagreement as WARN fault in `lbl_fault`.
  - `CupAcquisitionStateMachine` body: zero lines changed.
  - Tests: 26 new test functions in `tests/test_cup_acquisition.py` covering `CupPositionDetector`, `position_authoritative`, `AuthorityDetector`, state machine parity, and the 1-second insertion case.
  - All local gates passed: ruff (clean), check_tests_first (clean), type_gate (139 soft errors, ratchet 139), pytest (1818 passed, 0 failures).
