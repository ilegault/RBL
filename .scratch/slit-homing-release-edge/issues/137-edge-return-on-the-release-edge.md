# 137: The edge-return test measures the release-edge pass

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 135, 136

**Spec:** `.scratch/slit-homing-release-edge/spec.md`
**Binding:** `docs/adr/0009-slits-homed-on-the-release-edge.md`, `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Deletes tests:** tests/test_edge_return_routine.py::test_edge_return_stops_on_a_bad_stop_code, tests/test_edge_return_tab.py::test_edge_return_runs_every_pass_speed_in_order

**Read first:** `src/rbl/hardware/galil_workers.py` (`EdgeReturnRoutine`, `EdgeReturnWorker`,
and `AxisHomeRoutine.approach` / `creep_to_release` from ticket 135), `src/rbl/gui/motor_tab.py`
(`MotorTab._build_edge_return_box`, `_EDGE_NOTE_STANDING`, `_on_edge_run_clicked`,
`_start_next_edge_speed`, `_on_edge_speed_done`, `_finish_edge_return`),
`src/rbl/hardware/edge_return.py` (`analyse_edge_returns`, `summary_line`),
`src/rbl/services/edge_return_writer.py` (`write_edge_return_csv`), `tests/sim_slit.py`
(`SimGalil`, ticket 135), `tests/test_edge_return_routine.py`, `tests/test_edge_return_tab.py`
(`_fake_worker`, `tab` fixture).

## What to build

The edge-return test runs again, and what it measures is exactly the pass homing uses: each cycle
moves the axis out, drives it onto the home switch at the approach speed, steps out one count at
a time to the release point, and records that edge. One run per press, at the operator's approach
speed. It never defines zero.

1. `EdgeReturnRoutine.__init__(self, galil, axis, approach_speed: int, cycles: int, out_counts: int, progress=None, cancelled=None)`
   (the third argument is renamed from `pass_speed_cps`; position unchanged). It builds its
   `AxisHomeRoutine(galil, axis, progress, cancelled, approach_speed=approach_speed)`.
   Each cycle: `set_speed(axis, SC.HOME_SEEK_SPEED_COUNTS_PER_SEC)`,
   `move_relative(axis, out_counts)`, `wait_idle(timeout=60.0)`, then `home.approach(approach_speed)`
   and `home.creep_to_release()`; any failure returns `(False, msg, readings)` with the readings so
   far. The edge is appended and reported as `f"{axis}: cycle {k}/{cycles} — edge at {edge} cts"`.
   Success message `f"{axis}: {cycles} edge returns at {approach_speed} cps"`. No `begin_home`,
   no stop-code check, never `define_zero`. Speeds restored on every exit, as now.
2. `EdgeReturnWorker(galil, axis, approach_speed, cycles, out_counts, parent=None)` passes it on.
3. `MotorTab`: pressing Run starts ONE `EdgeReturnWorker` at `self.beamline.home_approach_speed()`,
   logging `f"# Edge-return test on {axis} at {speed} cps"`. On `done`: success logs
   `summary_line(analyse_edge_returns(readings), axis, speed)`; failure logs `"✗ " + msg`; either
   way, if there are two or more readings, write the CSV with `[(speed, readings)]` and log
   `f"# Edge-return CSV: {path}"`, then unlock as `_finish_edge_return` does now. Remove the
   per-speed chain (`_edge_speeds`, `_start_next_edge_speed`). The CSV format is unchanged.
4. `_EDGE_NOTE_STANDING` becomes
   `"Moves the axis out and back onto the home switch, cycle after cycle, and finds the release edge each time at the approach speed. Never sets zero. A spread of a few counts is switch jitter; jumps of about 8 counts are lost steps."`

Existing tests: `test_edge_return_collects_one_reading_per_cycle`, `test_edge_return_never_defines_zero`,
`test_edge_return_cancel_keeps_the_readings_so_far`, `test_edge_return_worker_emits_done_with_readings`,
`test_edge_return_writes_one_csv_for_the_whole_run` and `test_a_failed_speed_ends_the_edge_return_run`
are rewritten in place under the same names where they fail (routine tests drive `SimGalil`; tab
tests expect one worker and one run). The `tab` fixture in `tests/test_edge_return_tab.py` sets
`beamline.set_home_approach_speed(40)`.

## Acceptance criteria

- [ ] Test `test_edge_return_records_release_edges` in `tests/test_edge_return_routine.py`:
      `SimGalil(trip_at=-3, release_at=6, start_at=2000)`;
      `EdgeReturnRoutine(sim, "A", 58, cycles=3, out_counts=2000).run()` returns
      `(True, msg, [6, 6, 6])` and `sim.slits["A"].zeroed_at == []`.
- [ ] Test `test_edge_return_shows_a_lost_step` in `tests/test_edge_return_routine.py`: the same
      with `slip_on_jog={2: 8}` returns readings `[6, -2, -2]`.
- [ ] Test `test_edge_return_fails_when_the_switch_never_releases` in
      `tests/test_edge_return_routine.py`: `SimGalil(trip_at=-3, release_at=1000)`; `run()` returns
      `(False, msg, [])` with `"did not release within 400 counts"` in `msg`.
- [ ] Test `test_edge_return_runs_once_at_the_approach_speed` in `tests/test_edge_return_tab.py`:
      with the approach speed at 40 and the fake worker emitting `done(True, "ok", [1000, 1001, 1000])`,
      choosing axis B and pressing Run constructs the worker exactly once, with axis `"B"` and speed
      `40`; the console contains `"B [X-] @ 40 cps:"`; exactly one `edge_return_B_*.csv` with 3 data
      rows exists in the homing folder.

**What tests may fake:** the controller with `SimGalil` (routine tests); `EdgeReturnWorker` (tab
tests, the existing fake class), `paths.HOMING_DIR` (to `tmp_path`), `time.sleep`.
`EdgeReturnRoutine`, `AxisHomeRoutine`, `MotorTab`, `Beamline`, the analysis and the CSV writer
are real.

**Out of scope:** removing the pass-speed boxes or the Together button (138). Renaming the CSV's
`pass_speed_cps` column. Plots.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
