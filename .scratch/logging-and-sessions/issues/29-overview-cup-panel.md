# 29: The Overview cup panel, with Start/Stop for a cup test log

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 28

**Spec:** `.scratch/logging-and-sessions/spec.md` (section C3)
**Binding:** `docs/adr/0002-cup-acquisition-triggered-by-current.md` (2026-10-05 amendment, B1, B3), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/overview_tab.py` (where `RecordingPanel` is built and added
with `DragPanel` near line 575), `src/rbl/gui/widgets/recording_panel.py` (layout and
theme use to copy), `src/rbl/gui/vacuum_tab.py` (`_lbl_log_path` styling),
`src/rbl/gui/faraday_cup_tab.py` (`_update_dose_view`), `CupView` in
`src/rbl/services/cup_log.py`.

## What to build

A cup panel on the Overview tab that shows what the cup is doing and lets the operator
open a cup test log without a session. It renders a snapshot; it computes nothing.

- `FaradayCupTab` gains signal `cup_view_changed = Signal(object)` emitting a `CupView`
  from `_update_dose_view`, on cup log open and close, on automatic insertion start and
  stop, and on run open and close.
- New `src/rbl/gui/widgets/cup_panel.py`, `CupPanel(QWidget)`, built with the `CupLog`.
  Shows cup current and confirmed position (from the beamline cup signals the tab already
  uses), run state, automatic insertion state, charge, fluence and dpa (from `CupView`),
  a `Start Logging` / `Stop Logging` button calling `open_test(datetime.now().astimezone())`
  / `close()`, and a path line: the path in `theme.OK` while logging, `Not logging` in
  `theme.WARN` otherwise. While `cup_log.kind` is `SESSION` the button is disabled with
  tooltip `The session owns the cup log. Stop the session to end it.`
- `OverviewTab` adds it beside `RecordingPanel`; `MainWindow.__init__` connects
  `faraday_cup_tab.cup_view_changed` to it, with a comment saying why (one accumulator,
  AGENTS.md invariant 2).

Tests may fake: instrument payloads. Must be real: `CupLog` (with `test_root=tmp_path`),
`FaradayCupTab`, `CupPanel`.

## Acceptance criteria

- [x] A fresh `CupPanel` shows `Not logging` styled with `theme.WARN`.
- [x] Clicking `Start Logging` creates a file under `tmp_path/YYYY-MM/`, the path line
      shows that path, and the Faraday tab's `Not logging` label is hidden.
- [x] After an automatic or manual insertion closes, the panel's charge text equals the
      Faraday tab's `lbl_running_q` text.
- [x] With a session-kind log open (`open_for_session`), the button is disabled with the
      tooltip above.
- [x] Clicking `Stop Logging` closes the file and the panel returns to `Not logging`.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments

2026-10-08. Added `CupPanel` (`src/rbl/gui/widgets/cup_panel.py`), `FaradayCupTab.cup_view_changed`
(de-duplicated `CupView`, published from log open/close, run and cycle changes, and dose updates),
`OverviewTab.attach_cup_log`, and the `MainWindow` wiring with its comment. All tests are in
`tests/test_cup_panel.py`: fresh panel (criterion 1), Start Logging creates `YYYY-MM/` file and hides the
tab's label (2), charge equals `lbl_running_q` after force start/stop and with nonzero charge (3),
session-kind log disables the button with the tooltip (4), Stop Logging returns to `Not logging` (5),
plus current/position rendering and the real `MainWindow` wiring. Full suite 2404 passed; ruff clean;
mypy error set identical to master. No bench verification needed.
