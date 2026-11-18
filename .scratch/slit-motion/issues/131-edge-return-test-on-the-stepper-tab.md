# 131: Edge-return test on the Stepper Motors tab

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 125, 129, 130

**Spec:** `.scratch/slit-motion/spec.md`
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/motor_tab.py` (`MotorTab._build_auto_home_box` for the group-box
layout and standing note, `_start_auto_home` / `_on_auto_home_done` for start/cancel/lock and
the console, `_lock_axes_for_auto_home`, `_auto_worker_running`, `_parallel_worker_running`,
`AxisControls.homing_active`, `set_motion_controls_enabled`), `src/rbl/hardware/edge_return.py`
(ticket 129), `src/rbl/hardware/galil_workers.py` (`EdgeReturnWorker`, ticket 130),
`src/rbl/config/paths.py` (how per-subsystem data dirs are declared),
`src/rbl/services/calibration_writer.py` (a CSV writer in this codebase),
`tests/test_auto_home.py` (`TestMotorTabAutoHome`, its `tab` fixture).

## What to build

One place on the Stepper Motors tab where the operator runs the edge-return test on one axis at
each of the three home pass speeds and gets the answer in the console and in a CSV.

1. `paths.py`: `HOMING_DIR: Path = DATA_DIR / "homing"` with the other per-subsystem dirs.
2. New `src/rbl/services/edge_return_writer.py`:
   `write_edge_return_csv(axis_letter: str, runs: list[tuple[int, list[int]]], directory: Path | None = None, now: datetime | None = None) -> Path`.
   `directory=None` means `paths.HOMING_DIR`, read at call time (`paths.HOMING_DIR`, not a copied
   constant). Creates the directory. File name `f"edge_return_{axis_letter}_{now:%Y%m%d_%H%M%S}.csv"`.
   Header `axis,slit,pass_speed_cps,cycle,edge_counts,delta_from_first_counts`, then one row per
   reading, `cycle` counted from 1 within each speed and `delta_from_first_counts` relative to that
   speed's first reading. Flushes and closes before returning.
3. `MotorTab`: a group box titled `"Edge-Return Test — Homing Repeatability"` below the auto-home box
   containing: `cbo_edge_axis` (items `"A — X+"`, `"B — X-"`, `"C — Y+"`, `"D — Y-"`),
   `spn_edge_cycles` (`QuietDoubleSpinBox`, decimals 0, 2–100, default `SC.EDGE_RETURN_DEFAULT_CYCLES`),
   `spn_edge_out` (decimals 0, 500–20000 counts, default `SC.EDGE_RETURN_DEFAULT_OUT_COUNTS`),
   `btn_edge_run` (`"Run edge-return test"`, becomes `"Cancel edge-return test"` while running), and
   a wrapped note label `lbl_edge_note` whose standing text is
   `"Moves the axis out and back to the home switch, cycle after cycle, at each home pass speed. Never sets zero. A spread of a few counts is switch jitter; jumps of about 8 counts are lost steps."`
4. Pressing Run: if the Galil is not connected, or any homing run is going (either all-axes worker
   or any panel's `homing_active()`), set `lbl_edge_note` to
   `"Edge-return test needs a connected Galil and no homing in progress."` and start nothing.
   Otherwise: lock that axis's panel with `set_motion_controls_enabled(False)`, disable both
   auto-home buttons and the three pass-speed boxes, log `f"# Edge-return test on {axis} at {p1}, {p2}, {p3} cps"`,
   and run one `EdgeReturnWorker(self.galil, axis, speed, cycles, out_counts, self)` per speed in
   `self.beamline.home_pass_speeds()` order, the next starting from the previous one's `done`.
   After each successful speed, log `summary_line(analyse_edge_returns(readings), axis, speed)`.
   A failed speed logs `"✗ " + msg` and ends the chain. At the end (success, failure or cancel),
   if at least one speed produced two or more readings, write the CSV with those runs and log
   `f"# Edge-return CSV: {path}"`; then unlock everything and restore the button text.
5. Pressing the button while running calls the current worker's `cancel()`.

## Acceptance criteria

- [ ] Test `test_edge_return_csv_rows` in `tests/test_edge_return_writer.py` (new file):
      `write_edge_return_csv("C", [(225, [100, 102]), (58, [101])], directory=tmp_path, now=datetime(2026, 11, 17, 9, 5, 7))`
      writes `edge_return_C_20261117_090507.csv` whose rows after the header are
      `C,Y+,225,1,100,0` / `C,Y+,225,2,102,2` / `C,Y+,58,1,101,0`.
- [ ] Test `test_edge_return_runs_every_pass_speed_in_order` in `tests/test_edge_return_tab.py`
      (new file): a real `Beamline` and `MotorTab` with `beamline.galil = MagicMock(connected=True)`,
      `set_home_pass_speeds((300, 120, 40))`; patch `rbl.gui.motor_tab.EdgeReturnWorker` with a fake
      class whose `start()` immediately emits `done(True, "ok", [1000, 1001, 1000])`; choose axis B and
      press Run; the fake was constructed three times with speeds `300, 120, 40` in that order and
      axis `"B"`; the console contains `"B [X-] @ 300 cps:"` and `"B [X-] @ 40 cps:"`.
- [ ] Test `test_edge_return_writes_one_csv_for_the_whole_run` in `tests/test_edge_return_tab.py`:
      the same run with `rbl.config.paths.HOMING_DIR` monkeypatched to `tmp_path`; exactly one
      `edge_return_B_*.csv` exists in `tmp_path` with 9 data rows, and the console contains
      `"# Edge-return CSV:"`.
- [ ] Test `test_a_failed_speed_ends_the_edge_return_run` in `tests/test_edge_return_tab.py`: the
      fake's second construction emits `done(False, "B: cycle 2/10 did not stop on the home switch (stop code 3)", [1000])`;
      only two constructions happen, the console contains that message after `"✗ "`, and afterwards
      `btn_edge_run.text() == "Run edge-return test"` and axis B's Move button is enabled again.
- [ ] Test `test_edge_return_refuses_while_homing` in `tests/test_edge_return_tab.py`: with axis A's
      panel reporting `homing_active()` (set its external status), pressing Run constructs no worker
      and `lbl_edge_note.text()` is the refusal sentence above.

**What tests may fake:** `EdgeReturnWorker` (a fake class emitting `done`), the Galil (`MagicMock`),
`paths.HOMING_DIR` (to `tmp_path`). `Beamline`, `MotorTab`, `analyse_edge_returns`, `summary_line`
and `write_edge_return_csv` are real.

**Out of scope:** plots. Running several axes in one go. Changing config defaults from the results
(the developer does that after the bench ticket, 134).

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
