# 173: Insert and Retract on the Overview's Faraday cup panel

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/cup-status-fix/spec.md`
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0003-commanded-and-confirmed-cup-position.md`
**Glossary:** `CONTEXT.md` — **Manual insertion**, **Commanded position**
**Read first:** `CupPanel` (`_build_ui`, `on_cup_actuation_state`, the "Intent out" section) in `src/rbl/gui/widgets/cup_panel.py`; `_on_insert_clicked` and `_on_retract_clicked` in `src/rbl/gui/faraday_cup_tab.py`; the overview cup-panel wiring after `cup_panel = self.overview_tab.cup_panel` in `src/rbl/gui/app.py`; `_actuation` and test_main_window_wires_the_overview_panel_to_the_faraday_tab in `tests/test_cup_panel.py`.

## What to build

The Overview's Faraday cup panel shows the position but cannot move the cup. After this
ticket it has Insert and Retract buttons. The panel only expresses intent; the Faraday Cup
tab does the move, so a move from either screen gets the same move confirmation, the same
cycle notification and the same **manual insertion** record. A panel that called
`Beamline.command_cup_in` itself would skip all three and log the insertion as uncommanded.

- `CupPanel` gains `insert_requested = Signal()` and `retract_requested = Signal()`, and
  `btn_insert` ("Insert") / `btn_retract` ("Retract") in one row directly above
  `btn_logging`. Both are disabled until a `CupActuationState` with `connected=True`
  arrives, and disabled again on one with `connected=False`.
- `FaradayCupTab` gains public `insert_cup()` and `retract_cup()` holding the current bodies
  of `_on_insert_clicked` / `_on_retract_clicked`; the two click handlers call them.
- `src/rbl/gui/app.py` connects `cup_panel.insert_requested` to
  `faraday_cup_tab.insert_cup` and `retract_requested` to `faraday_cup_tab.retract_cup`,
  next to the existing overview cup-panel connections.

## Acceptance criteria

- [ ] Test `test_panel_move_buttons_follow_the_t7_connection`, in `tests/test_cup_panel.py`:
      a fresh `CupPanel` has both buttons disabled; after `on_cup_actuation_state` with
      `_actuation(1.0, CupPosition.OUT)` both are enabled; after a state with
      `connected=False` both are disabled.
- [ ] Test `test_panel_move_buttons_emit_intent_only`, in `tests/test_cup_panel.py`: with the
      buttons enabled, clicking `btn_insert` emits `insert_requested` once and
      `retract_requested` never; clicking `btn_retract` emits `retract_requested` once.
- [ ] Test `test_overview_insert_moves_the_cup_through_the_faraday_tab`, in
      `tests/test_cup_panel.py`, with a `MainWindow` built as the existing wiring test builds
      it: `win.beamline.command_cup_in` monkeypatched to count calls, and
      `win.beamline.cup_actuation_changed.emit(_actuation(1.0, CupPosition.OUT))`; clicking
      the overview panel's `btn_insert` twice records exactly one call, because the tab
      ignores a second insert while the first move is in flight.
- [ ] Test `test_overview_retract_moves_the_cup_through_the_faraday_tab`, in
      `tests/test_cup_panel.py`: the same with `command_cup_out`, starting from
      `_actuation(1.0, CupPosition.IN)` and `btn_retract`.
- [ ] The Faraday Cup tab's own Insert/Retract tests still pass unchanged. (no test: CI's full suite)

**What tests may fake:** `Beamline.command_cup_in` / `command_cup_out` (no T7). The panel,
the tab and the `MainWindow` wiring are real.

**Out of scope:** automatic cup insertion controls on the Overview; the interlock control
(ticket 172); the LabJack panel (ticket 170).

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_cup_panel.py
```

Run pytest on the touched test files only (implement-ticket-parallel, section 4). CI runs the full suite on the PR.

## Comments
