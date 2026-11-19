# 140: Switch Map box on the Stepper Motors tab

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 138, 139

**Spec:** `.scratch/slit-homing-release-edge/spec.md`
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/motor_tab.py` (`MotorTab._build_edge_return_box`, `_set_edge_note`,
`_any_homing_active`, `_on_edge_run_clicked`, `_finish_edge_return`, `_start_auto_home`,
`AxisControls._start_homing`, `_log_line`, `_update_approach_speed_enabled` from ticket 138),
`src/rbl/hardware/galil_workers.py` (`SwitchMapWorker`, ticket 139),
`src/rbl/hardware/switch_map.py` (`SwitchMapRow`), `src/rbl/services/switch_map_writer.py`
(`write_switch_map_csv`), `tests/test_edge_return_tab.py` (`_fake_worker`, `tab` fixture,
`_choose_axis`, `_console`: copy this harness).

## What to build

On the Stepper Motors tab only (not the Overview), the operator picks an axis, presses Run, and
watches the switch map happen: a status line in the box updates every step, the Command Console
gets a line each time a switch changes and a summary at the end, and a CSV lands in the homing
folder.

1. A group box `"Switch Map — Where the Switches Change"` below the Edge-Return Test box, with
   `cbo_switch_axis` (items `"A — X+"`, `"B — X-"`, `"C — Y+"`, `"D — Y-"`, data = axis letter),
   `btn_switch_run` (`"Run switch map"`, `"Cancel switch map"` while running),
   `lbl_switch_live` (the live status line, empty until a run starts) and a wrapped
   `lbl_switch_note` whose standing text is
   `"Steps one axis in through the home switch and the reverse limit, one count at a time, then back out, and records where each switch trips and releases. Never sets zero."`
2. Pressing Run with no connected Galil, or while any homing run, the edge-return test or another
   switch map is going, sets `lbl_switch_note` to
   `"Switch map needs a connected Galil and no homing or edge-return test in progress."` and
   starts nothing. Otherwise construct `SwitchMapWorker(self.galil, axis, self)`, connect
   `progress` to the console (`_log_line`), `row` to the live line and `done` to the finish
   handler, log `f"# Switch map on {axis}"`, lock that axis's panel
   (`set_motion_controls_enabled(False)`), and disable `btn_auto_home_seq`, `btn_edge_run` and
   `spn_approach_speed`. Pressing the button while running calls the worker's `cancel()`.
3. Each `row` sets `lbl_switch_live` to
   `f"step {row.step} — {row.position_counts} cts — F{f} R{r} H{h}"` with `●` for tripped and `○`
   for clear.
4. On `done(ok, msg, rows)`: on failure log `"✗ " + msg`; if `rows` is not empty, write
   `write_switch_map_csv(axis, rows)` and log `f"# Switch map CSV: {path}"`; unlock everything
   locked in step 2, restore the button text, and show `msg` in `lbl_switch_note` (fault colour on
   failure).
5. While a switch map runs, the auto-home button, every axis's Home / Seek + Home, and the
   edge-return Run refuse to start (extend `_any_homing_active`, or the checks they already make,
   to include a running switch map).

## Acceptance criteria

- [x] Test `test_switch_map_box_shows_each_step_live` in `tests/test_switch_map_tab.py` (new
      file): with `rbl.gui.motor_tab.SwitchMapWorker` patched with a fake whose `start()` emits one
      `row` of `SwitchMapRow(3, "in", -3, True, True, False)`, choosing axis B and pressing Run
      leaves `lbl_switch_live.text() == "step 3 — -3 cts — F○ R● H●"`.
- [x] Test `test_switch_map_writes_its_csv_and_logs_it` in `tests/test_switch_map_tab.py`: the fake
      emits `progress("B: in — home trips at -3 cts")` then `done(True, "B: in — home trips at -3, ...", rows)`
      with two rows; exactly one `switch_map_B_*.csv` exists in the homing folder (monkeypatched to
      `tmp_path`), and the console contains `"B: in — home trips at -3 cts"` and `"# Switch map CSV:"`.
- [x] Test `test_switch_map_refuses_while_homing` in `tests/test_switch_map_tab.py`: with axis A's
      panel reporting `homing_active()`, pressing Run constructs no worker and `lbl_switch_note`
      shows the refusal sentence.
- [x] Test `test_homing_refuses_while_a_switch_map_runs` in `tests/test_switch_map_tab.py`: with a
      fake worker that never emits `done` and reports `isRunning()` True, after pressing Run,
      clicking `btn_auto_home_seq` constructs no `AutoHomeAllWorker` (patched) and pressing
      `btn_edge_run` constructs no `EdgeReturnWorker` (patched).
- [x] Test `test_switch_map_unlocks_after_a_failure` in `tests/test_switch_map_tab.py`: the fake
      emits `done(False, "B: switch map cancelled", [])`; the console contains
      `"✗ B: switch map cancelled"`, `btn_switch_run.text() == "Run switch map"`, axis B's Move
      button and `btn_auto_home_seq` are enabled, and no CSV was written.

**What tests may fake:** `SwitchMapWorker`, `AutoHomeAllWorker`, `EdgeReturnWorker` (fake classes
patched into `rbl.gui.motor_tab`), the Galil (`MagicMock(connected=True)`), `paths.HOMING_DIR`
(to `tmp_path`). `MotorTab`, `Beamline`, `SwitchMapRow` and `write_switch_map_csv` are real.

**Out of scope:** plots of the map. Running several axes in one press. The Overview tab.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments

- **2026-11-19:** Added the "Switch Map — Where the Switches Change" group box to `MotorTab`
  (`src/rbl/gui/motor_tab.py`) below the Edge-Return Test box, wired `SwitchMapWorker`, live step
  status (`lbl_switch_live`), console logging, `write_switch_map_csv`, and mutual exclusion/locking
  with homing, auto-home, edge-return, and `spn_approach_speed`. Covered by all 5 tests in
  `tests/test_switch_map_tab.py`. Bench verification on the real slits remains in ticket 142.
