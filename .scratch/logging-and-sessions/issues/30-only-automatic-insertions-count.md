# 30: Only automatic insertions count toward the dose

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 25, 29

**Spec:** `.scratch/logging-and-sessions/spec.md` (section C4)
**Binding:** `docs/adr/0003-commanded-and-confirmed-cup-position.md` (2026-10-05 amendment, C1, C2), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/faraday_cup_tab.py`: `on_cup_state` (the `RunClosed` branch
that calls `self._accumulator.record_insertion` and `write_insertion_summary`),
`_tick_cycle` (the `CycleInsert` branch), `_on_insert_clicked`, `_on_force_start_clicked`,
`on_cup_actuation_state` (where confirmed IN/OUT transitions are seen).
`src/rbl/hardware/dose_model.py` `exclude_interval`.

## What to build

Beam checks made by hand before irradiation are recorded but never added to the dose.

- The tab marks an insertion **automatic** when the confirmed IN follows a `CycleInsert`
  it acted on in `_tick_cycle`; the mark clears on the confirmed OUT. Origin is otherwise
  `manual` (Insert button), `forced` (Force Start), or `uncommanded` (confirmed IN with no
  command, e.g. LOCAL at the controller).
- On `RunClosed`: automatic, `record_insertion` as today. Not automatic: no
  `record_insertion`; call `exclude_interval(confirmed_in_t, confirmed_out_t)`, falling
  back to the run's open and close times when position feedback is unavailable.
- Every `write_insertion_summary` call passes `origin`, `counted_in_dose` and
  `total_beam_on_s` (ticket 26). A non-counted row carries the running totals unchanged.

Tests may fake: instrument payloads only (`tests/payloads.py`). The accumulator, tab,
`CupLog` and file must be real.

## Acceptance criteria

- [x] With a cup log open, a manual insertion (Insert clicked) writes a summary row with
      `origin=manual`, `counted_in_dose=false`, and `lbl_running_q` text is unchanged
      from before it.
- [x] An automatic insertion (cycle started, scheduler tick produces `CycleInsert`) writes
      `origin=automatic`, `counted_in_dose=true`, and the charge increases.
- [x] Automatic, then a 10 s manual insertion, then automatic: the second automatic row's
      `beam_on_seconds` is 10 s less than the same sequence without the manual insertion
      (run both sequences in the test).
- [x] A Force Start run is recorded `origin=forced`, `counted_in_dose=false`.
- [x] The tab's module docstring states the counting rule and cites the ADR 0003
      amendment.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments

2026-10-09. `FaradayCupTab` now classifies each insertion (`automatic` / `manual` / `forced` /
`uncommanded`) in one place, `_close_insertion`, which replaced the two duplicated
run-close blocks (threshold close and Force Stop). Only `automatic` calls
`record_insertion`; the others call `exclude_interval` (confirmed IN -> OUT, or run
open/close without position feedback) and write a row with `counted_in_dose=false` and the
unchanged running totals (`beam_on_seconds` 0). Module docstring states the rule and cites
the ADR 0003 amendment.

Tests, `tests/test_cup_insertion_origin.py` (real tab, real `CupLog`/file, real
accumulator; only actuation snapshots and picoammeter payloads faked):
- criterion 1: `test_manual_insertion_is_recorded_but_not_counted`
- criterion 2: `test_automatic_insertion_is_counted_and_charge_increases`
- criterion 3: `test_manual_cup_in_time_is_taken_out_of_next_hold_interval` (runs both sequences)
- criterion 4: `test_forced_run_is_recorded_as_forced_and_not_counted`
- extra: uncommanded origin, manual row totals unchanged, no leak of the automatic mark.

Harness change, not a muted test: `tests/test_cup_panel.py::test_charge_text_matches_the_tab_when_charge_is_nonzero`
made charge with Force Start runs, which by this ticket no longer count. It now accrues
charge through two scheduled insertions; its assertions are unchanged.

Bench verification: none needed.

