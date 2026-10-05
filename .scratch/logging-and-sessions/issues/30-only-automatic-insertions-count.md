# 30: Only automatic insertions count toward the dose

**Status:** ready-for-agent

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

- [ ] With a cup log open, a manual insertion (Insert clicked) writes a summary row with
      `origin=manual`, `counted_in_dose=false`, and `lbl_running_q` text is unchanged
      from before it.
- [ ] An automatic insertion (cycle started, scheduler tick produces `CycleInsert`) writes
      `origin=automatic`, `counted_in_dose=true`, and the charge increases.
- [ ] Automatic, then a 10 s manual insertion, then automatic: the second automatic row's
      `beam_on_seconds` is 10 s less than the same sequence without the manual insertion
      (run both sequences in the test).
- [ ] A Force Start run is recorded `origin=forced`, `counted_in_dose=false`.
- [ ] The tab's module docstring states the counting rule and cites the ADR 0003
      amendment.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
