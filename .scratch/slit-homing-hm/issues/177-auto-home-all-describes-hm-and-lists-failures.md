# 177: Auto-Home All describes HM homing, and its failure dialog lists every failed axis

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 176

**Spec:** `.scratch/slit-homing-hm/spec.md` (the Auto-Home All half of "Stepper Motors tab"; user stories 26-29)
**Binding:** `docs/adr/0010-slits-homed-with-hm-as-nec-specifies.md`, `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/motor_tab.py` (`MotorTab._AUTO_HOME_STANDING`, `_build_auto_home_box` and the `btn_auto_home_seq` tooltip, `_confirm_auto_home`, `_start_auto_home`, `_on_auto_axis_finished`, `_on_auto_home_done`), `src/rbl/hardware/galil_workers.py` (`AutoHomeAllWorker`: its `axis_finished` messages already begin with the axis letter), `src/rbl/config/hardware_config.py` (`HOME_EDGE_AGREEMENT_COUNTS`), `tests/test_auto_home.py` (`TestMotorTabAutoHome`, its `tab` fixture and test_a_declined_confirmation_starts_nothing, for driving `_confirm_auto_home` and faking the worker).

## What to build

After 176 the Auto-Home All texts still describe release-edge homing, and when a run fails the operator has to scroll the console to learn why.

1. `MotorTab._AUTO_HOME_STANDING` becomes exactly: `Homes all four slits, one at a time, with HM at 900 cps: twice per axis, zeroed at the second stop if the two agree within 4 counts. Stops at the first axis that fails. Start it and work on another tab.`
2. The `_start_auto_home` confirmation body becomes exactly: `Each axis will be moved off its switch if needed, homed with HM at 900 cps twice, and zeroed at the second stop if the two stops agree within 4 counts — A, then B, then C, then D.\n\nOnly one axis moves at a time. Make sure the slits are clear to travel.\n\nStart?` (title unchanged). Build 900 and 4 from `SC.HOMING_SPEED_COUNTS_PER_SEC` and `SC.HOME_STOP_AGREEMENT_COUNTS`.
3. The `btn_auto_home_seq` tooltip becomes `f"Homes A, then B, then C, then D with HM at {SC.HOMING_SPEED_COUNTS_PER_SEC} cps, twice per axis, zeroing at the second stop if the two agree within {SC.HOME_STOP_AGREEMENT_COUNTS} counts. Stops at the first axis that fails."`.
4. Failure list: `MotorTab` keeps `self._auto_failures: list[str]`, cleared in `_start_auto_home` once the run is confirmed. `_on_auto_axis_finished` appends `msg` when `ok` is False. Do this first in the method, before its early return when there is no panel. `_on_auto_home_done` with `ok` False shows `QMessageBox.warning(self, "Auto-home did not complete", body)`, where `body` is the summary, a blank line, then the collected messages one per line (`summary + "\n\n" + "\n".join(self._auto_failures)`). With no collected messages (a cancelled run), the body is the summary alone.
5. Remove `HOME_EDGE_AGREEMENT_COUNTS` from `hardware_config.py`. After steps 2-3, nothing references it.

## Acceptance criteria

- [ ] Test `test_auto_home_confirmation_describes_hm_homing` in `tests/test_auto_home.py` (`TestMotorTabAutoHome`'s `tab` fixture, Galil connected): with `tab._confirm_auto_home` replaced by a recorder that returns False, clicking `btn_auto_home_seq` passes exactly the body in step 2, and nothing starts.
- [ ] Test `test_auto_home_note_and_tooltip_describe_hm_homing` in `tests/test_auto_home.py`: `tab.lbl_auto_home.text()` equals the text in step 1 exactly, and `btn_auto_home_seq.toolTip()` contains `HM at 900 cps` and `within 4 counts`.
- [ ] Test `test_auto_home_failure_dialog_lists_each_failed_axis` in `tests/test_auto_home.py`: confirmation answered yes, `AutoHomeAllWorker` replaced in `rbl.gui.motor_tab` by a fake `QObject` with the same four signals and `start`/`cancel`/`isRunning`, and `QMessageBox.warning` replaced by a recorder. Clicking `btn_auto_home_seq`, then emitting `axis_finished("A", False, "A: first reason")`, `axis_finished("B", True, "B: homed")`, `axis_finished("C", False, "C: second reason")` and `done(False, "S")`, shows the body `S\n\nA: first reason\nC: second reason`. A second run whose only failure is `"D: third reason"` shows `S2\n\nD: third reason`, so the list is cleared between runs.
- [ ] (no test: a removal; `ruff` and the type gate fail on a leftover reference) `HOME_EDGE_AGREEMENT_COUNTS` no longer exists, and nothing references it.
- [ ] (no test: CI's full suite) No test is deleted. The other `TestMotorTabAutoHome` tests, including the banned-phrase test 176 rewrote, pass unchanged.

**What tests may fake:** the worker (a fake with the real signal shapes), the confirmation dialog through `_confirm_auto_home`, and `QMessageBox.warning`. The tab and the button clicks are real.

**Out of scope:** continuing past a failed axis (decided against); the per-axis Home button (176); the edge-return box (178, 179).

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_auto_home.py
```

Run pytest on the touched test files only (implement-ticket-parallel, section 4). CI runs the full suite on the PR.

## Comments
