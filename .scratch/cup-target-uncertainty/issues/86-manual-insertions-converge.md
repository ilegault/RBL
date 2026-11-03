# 86: Manual insertions can retract on convergence, optionally with no time limit

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 85

**Spec:** `.scratch/cup-target-uncertainty/spec.md`
**Binding:** `docs/adr/0003-commanded-and-confirmed-cup-position.md` (amendment D, and C1), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/faraday_cup_tab.py` (`_on_insert_clicked`, `_on_force_start_clicked`, `_resolve_insertion_origin`, `_start_move`, `_close_insertion`); ADR 0003 amendment C1 and D2/D5.

## What to build

Manual insertions always show the live uncertainty (ticket 83). This ticket lets them end
themselves, for bench testing.

1. Settings: `manual_retract_on_converge` (bool, default False) and
   `manual_no_time_limit` (bool, default False), validated and saved like the others.
2. Two checkboxes under the dwell mode toggle: `"Retract manual insertions when
   converged"` (enabled only in target-uncertainty mode) and `"No time limit on manual
   insertions"` (enabled only when the first is checked). Never locked; changes apply
   immediately and write a settings-change marker.
3. For an insertion whose origin is `manual` or `forced`, in target mode with the first
   checkbox on: on first convergence, command cup OUT via `_start_move(CupPosition.OUT)`;
   if not converged by `max_dwell_s` after run open and the no-limit box is off, command
   cup OUT then with `end_reason = "max_dwell"`. With no limit on, only convergence
   retracts it. The tab owns this timing for manual insertions (the scheduler stays out
   of manual runs).
4. Display only, never a command, when actuation is not connected or the controller is
   not in AUTO, or the origin is `uncommanded` (hand insertion at the controller).
5. Manual insertions stay uncounted (`counted_in_dose == False`) whatever ends them;
   the summary row records `end_reason` (`converged`, `max_dwell` or `operator`) and
   `converged_after_s`, with `max_dwell_s` blank when no limit applied.

## Acceptance criteria

- [ ] Checkbox off: an Insert-button insertion fed a converging current is never
      commanded OUT by the tab; the readout still reaches a percentage.
- [ ] Checkbox on: the same insertion is commanded OUT on convergence; its row has
      `end_reason == "converged"`, `counted_in_dose == False`.
- [ ] Checkbox on, no limit off, noisy feed: commanded OUT at `max_dwell_s`,
      `end_reason == "max_dwell"`. No limit on: still in after `3 * max_dwell_s`, then
      commanded OUT when the feed converges, with `max_dwell_s` blank in the row.
- [ ] Checkbox on but controller reported in LOCAL (via `CupActuationFeed`): no OUT
      command is issued on convergence.
- [ ] The second checkbox is disabled while the first is unchecked, and the first is
      disabled in fixed-dwell mode.

May fake: instrument and actuation feeds, modal dialogs, the clock. Must be real: the tab,
settings store on a temp file, cup log writer on `tmp_path`.

## Gate

Run all four, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
