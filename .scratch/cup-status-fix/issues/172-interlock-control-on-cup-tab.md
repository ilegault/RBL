# 172: Interlock control and state on the Faraday Cup tab

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 170, 171

**Spec:** `.scratch/cup-status-fix/spec.md` (D7)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0003-commanded-and-confirmed-cup-position.md`
**Glossary:** `CONTEXT.md` — **Interlock**, **Automatic cup insertion**, **Manual insertion**
**Read first:** `_on_insert_clicked`, `_start_move` and `on_cup_actuation_state` in `src/rbl/gui/faraday_cup_tab.py`; the `act_box` construction (search `QGroupBox("Cup Actuation")`) and where `btn_insert` / `btn_retract` get enabled; `release_cup_interlock` / `close_cup_interlock` on `Beamline` (ticket 171); test_actuation_confirming_move_updates_both_indicators and `TestSamplingCycleTab.make_tab_and_feed` in `tests/test_faraday_cup_tab.py`.

## What to build

The operator can see whether the interlock is closed and release or re-close it, so the
bench can confirm its effect on the front panel. Blocked by 170 because both edit the
"Cup Actuation" box.

- Under the Insert/Retract row in `act_box`: `self.lbl_interlock` and `self.btn_interlock`.
- From each `CupActuationState`: when not `connected`, the label reads `Interlock: —` and the
  button is disabled. When `interlock_closed`, the label reads
  `Interlock: closed — the cup can go OUT` and the button `Release interlock`; otherwise
  `Interlock: open — the cup is held IN, front panel included` and `Close interlock`
  (em dashes U+2014).
- The button is also disabled while automatic cup insertion runs (`self.cycle.is_armed`) and
  while a move is in flight.
- Clicking `Release interlock`: if the confirmed position is not IN, first do what
  `_on_insert_clicked` does to track an operator insertion (`cycle.notify_manual_insert`,
  `_start_move(CupPosition.IN)`, `_manual_pending = True`), because releasing drives the cup
  IN; then call `beamline.release_cup_interlock()`. Clicking `Close interlock` calls
  `beamline.close_cup_interlock()`.

## Acceptance criteria

- [x] Test `test_interlock_button_releases_and_label_reads_open`, in
      `tests/test_faraday_cup_tab.py`: a `Beamline` with `lj.handle = 1`, a
      `CupActuationFeed`, `beamline._on_labjack_connect_cup()`, word 4 sent; the label reads
      the closed text and the button `Release interlock`; after a click the label reads the
      open text and the button `Close interlock`.
- [x] Test `test_close_interlock_button_restores_closed`, in `tests/test_faraday_cup_tab.py`:
      same setup, two clicks; the label reads the closed text again.
- [x] Test `test_interlock_controls_disabled_without_t7`, in `tests/test_faraday_cup_tab.py`:
      a `Beamline()` with no handle and one snapshot with `connected=False`; the label reads
      `Interlock: —` and the button is disabled.
- [x] Test `test_interlock_button_disabled_during_automatic_insertion`, in
      `tests/test_faraday_cup_tab.py`, built as `make_tab_and_feed` builds its rig: after a
      snapshot with `connected=True, interlock_closed=True` and clicking `btn_cycle_arm`, the
      button is disabled; after `btn_cycle_stop` and confirming the stop as the existing
      stop tests do, it is enabled.
- [x] Test `test_release_with_cup_out_is_tracked_as_an_insertion`, in
      `tests/test_faraday_cup_tab.py`: with the cup confirmed OUT (word 8) and the interlock
      closed, click the button, then send word 12 and word 4; `lbl_commanded` and
      `lbl_confirmed` contain "IN" and `lbl_fault` text does not contain "Move Fault".

**What tests may fake:** the LabJack handle as the existing actuation tests do. The tab,
`Beamline`, decode and debounce are real.

**Out of scope:** an interlock feedback contact; changing when the app closes or releases it
on connect, disconnect or shutdown; the Overview panel (ticket 173).

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_faraday_cup_tab.py
```

Run pytest on the touched test files only (implement-ticket-parallel, section 4). CI runs the full suite on the PR.

## Comments

2026-11-20: Implemented `lbl_interlock` and `btn_interlock` in `FaradayCupTab`'s Cup Actuation box (`src/rbl/gui/faraday_cup_tab.py`), driven by `CupActuationState.interlock_closed`, `CycleState`, and `_move_in_flight`. Releasing the interlock while the cup is not IN tracks an operator insertion before calling `beamline.release_cup_interlock()`. Covered by all five named tests in `tests/test_faraday_cup_tab.py` (`test_interlock_button_releases_and_label_reads_open`, `test_close_interlock_button_restores_closed`, `test_interlock_controls_disabled_without_t7`, `test_interlock_button_disabled_during_automatic_insertion`, `test_release_with_cup_out_is_tracked_as_an_insertion`). Bench verification of physical interlock behaviour remains in ticket 174.
