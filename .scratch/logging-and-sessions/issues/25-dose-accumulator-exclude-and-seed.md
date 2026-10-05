# 25: The dose accumulator can leave time out and pick up earlier totals

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/logging-and-sessions/spec.md` (section C4, the `DoseAccumulator` part)
**Binding:** `docs/adr/0003-commanded-and-confirmed-cup-position.md` (decision 7 and the 2026-10-05 amendment, C2, C4, C5), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/hardware/dose_model.py`, `DoseAccumulator` (all of it, especially
`record_insertion`, where the hold interval is `t_in - self._last_out_t` and is credited
at `self._last_current_a`), `tests/test_dose_model.py`.

## What to build

Two pure additions, no Qt (the module's own test `test_no_pyside6_in_hardware_layer`
must keep passing). This ticket touches only `dose_model.py` and its tests;
cup-settings ticket 06 explicitly leaves `dose_model.py` alone, so there is no conflict.

- `@dataclass(frozen=True) class DoseTotals` in `dose_model.py`: `total_charge_c`,
  `total_beam_on_s`, `insertion_count`, `last_out_t`, `last_current_a`, `source_path: str`.
  It lives here, not in `rbl/services/`, because `hardware` must not import upward.
- `DoseAccumulator.exclude_interval(start_t, end_t) -> None`: records a span the
  specimen was not irradiated. At the next `record_insertion`, the part of all recorded
  spans that overlaps `[last_out_t, t_in]` is removed from that hold interval. Overlapping
  spans are merged before subtracting (no double count). Spans outside that interval have
  no effect. Recorded spans are cleared after each `record_insertion`. `reset()` clears
  them.
- `DoseAccumulator.seed(totals: DoseTotals) -> None`: sets total charge, total beam-on,
  insertion count, `last_out_t` and `last_current_a`, so the next insertion's hold
  interval runs from the earlier log's last withdrawal.

Tests may fake: nothing. These are pure functions; use plain floats.

## Acceptance criteria

- [x] Automatic insertions at t=0-10 (1 nA) and t=100-110, with
      `exclude_interval(40, 50)` between them: `last_beam_on_s == 80.0` and total charge
      is `1e-9 * 80`. The same sequence without the exclusion gives 90 s, proving the
      subtraction.
- [x] Exclusions (40, 50) and (45, 60) subtract 20 s, not 25.
- [x] An exclusion at (200, 210), outside the hold interval, leaves the result identical
      to no exclusion.
- [x] Seeded continuity: accumulator A records insertions 1, 2, 3; accumulator B is seeded
      from A's state after insertion 2 (a `DoseTotals` built from A's public properties)
      and records insertion 3. B's total charge, total beam-on and insertion count equal
      A's exactly.
- [x] `reset()` after `exclude_interval` and `seed` returns every public property to its
      fresh value. All existing tests in `tests/test_dose_model.py` pass unchanged.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments

2026-10-05: Added `DoseTotals`, `DoseAccumulator.exclude_interval` and `seed` in
`src/rbl/hardware/dose_model.py` (pure, no Qt, docstrings updated). `DoseTotals.last_out_t`
and `last_current_a` are `float | None` so a log with no insertion can be represented.
Tests in `tests/test_dose_model.py`: criterion 1 (`test_exclude_interval_removes_manual_time...`,
`test_without_exclusion...`), 2 (`test_overlapping_exclusions...`), 3
(`test_exclusion_outside_hold_interval...`), 4 (`test_seeded_accumulator_matches_continuous_one`),
5 (`test_reset_clears_exclusions_and_seed`; existing tests unchanged). Plus clipping,
clearing-after-insertion, full-cover and empty-span cases. No bench verification needed.
