# 28: The Faraday Cup tab writes only while a cup log is open

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 27, cup-settings/16

**Spec:** `.scratch/logging-and-sessions/spec.md` (section C2, and C1's last paragraph)
**Binding:** `docs/adr/0002-cup-acquisition-triggered-by-current.md` (2026-10-05 amendment, B1-B4; decision 3), `docs/adr/0003-commanded-and-confirmed-cup-position.md`, `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/faraday_cup_tab.py`: `__init__` (where
`CupSessionWriter()` is built), every `self.session_writer.` call (about 28),
`_check_arm_preconditions`, `_on_cycle_arm_clicked`, `_on_cycle_stop_clicked`,
`_update_cycle_view`, `shutdown`. `src/rbl/gui/app.py` (`MainWindow.__init__`, the
Faraday Cup tab factory lambda). `tests/test_faraday_cup_tab.py` and `tests/payloads.py`.

## What to build

No cup file exists unless an operator opened a cup log.

- `FaradayCupTab.__init__` takes `cup_log: CupLog | None = None` in place of
  `session_writer`; `None` builds a fresh, closed `CupLog`. The tab never constructs a
  `CupSessionWriter`. Every write goes through `self.cup_log.writer` and is skipped when it
  is `None`.
- `cup_log.opened` resets the dose accumulator and refreshes the view. `cup_log.closed`
  stops automatic cup insertion through the same path as `_on_cycle_stop_clicked`
  (withdraw, keep the saved boundary).
- With no cup log, a label beside the current readout reads `Not logging` in
  `theme.WARN`, and automatic insertion cannot start: `_check_arm_preconditions` returns
  `Start a session or a cup test log to use automatic cup insertion.` as its **first**
  check. Manual Insert/Retract stay enabled.
- `MainWindow.__init__` builds one `CupLog` (attribute `self.cup_log`) and passes it to the
  tab factory. Its default test root is `FARADAY_CUP_DIR`.

Tests may fake: nothing below the tab except instrument payloads (`tests/payloads.py`
`CupFeed`, `CupActuationFeed`). The `CupLog` must be real with `test_root=tmp_path`.
The 8 existing call sites passing `session_writer=` in `tests/test_faraday_cup_tab.py`
and `tests/test_cup_session_writer.py` are rewritten in place under the same names to
pass a `CupLog` that has had `open_test(...)` called, so they keep asserting on a real
file. No test function is deleted.

## Acceptance criteria

- [ ] A tab built with a closed `CupLog(test_root=tmp_path)` and fed a manual insertion
      (Insert clicked, confirmed IN, current above the run start current, confirmed OUT)
      leaves `tmp_path` empty, and the `Not logging` label is visible.
- [ ] With no cup log, clicking the automatic-insertion start button leaves the scheduler
      not armed and shows the reason text above, even when the dose chain is fully
      configured.
- [ ] After `cup_log.open_test(...)`, the same manual insertion writes run and summary
      rows to that file and the `Not logging` label is hidden.
- [ ] With automatic insertion running and the cup IN, `cup_log.close()` commands the cup
      out (the tab's commanded position becomes OUT) and the scheduler is no longer armed.
- [ ] `grep -n "CupSessionWriter(" src/rbl/gui/faraday_cup_tab.py` returns nothing, and
      every previously existing Faraday-tab test passes after the in-place rewrite.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
