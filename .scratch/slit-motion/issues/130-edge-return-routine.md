# 130: Edge-return routine

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 124

**Spec:** `.scratch/slit-motion/spec.md`
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `CONTEXT.md` (**Slit motion**: *home pass*, *edge-return test*),
`src/rbl/hardware/galil_workers.py` (`AxisHomeRoutine`: `back_out_of_home` from ticket 124,
`home_passes` and its stop-code check from ticket 123, `wait_idle`, `_restore_speeds`;
`HomingWorker` for the QThread wrapper shape), `src/rbl/hardware/galil_driver.py`
(`begin_home`, `move_relative`, `set_speed`, `get_position`, `get_stop_code`),
`tests/test_auto_home.py` (`_galil`, `no_sleep`).

## What to build

The routine that collects edge-return readings on one axis at one pass speed. It never defines
zero, so running it does not change where the slits think home is.

1. `hardware_config.py`: `EDGE_RETURN_DEFAULT_CYCLES = 10` and
   `EDGE_RETURN_DEFAULT_OUT_COUNTS = 2000` (≈3.2 mm) next to the homing constants.
2. `galil_workers.py`: class `EdgeReturnRoutine` (not a QThread, same reasons as `AxisHomeRoutine`):
   `__init__(self, galil, axis, pass_speed_cps: int, cycles: int, out_counts: int, progress=None, cancelled=None)`
   and `run(self) -> tuple[bool, str, list[int]]`, never raising (an exception becomes
   `(False, f"{axis}: edge-return error — {e}", readings_so_far)`). Steps:
   - Back out of home first with `AxisHomeRoutine(galil, axis, progress, cancelled).back_out_of_home()`;
     a failure ends the run with its message.
   - For each cycle `k` in `1..cycles`: check cancelled (cancel →
     `(False, f"{axis}: edge-return cancelled after {k-1} cycles", readings)`);
     `set_speed(axis, SC.HOME_SEEK_SPEED_COUNTS_PER_SEC)`; `move_relative(axis, out_counts)`;
     `wait_idle(timeout=60.0)`; `begin_home(axis, pass_speed_cps, fine_speed=pass_speed_cps)`;
     `wait_idle(timeout=120.0)`; `code = get_stop_code(axis)`, and if `code != SC.HOME_STOP_CODE` end with
     `(False, f"{axis}: cycle {k}/{cycles} did not stop on the home switch (stop code {code})", readings)`;
     append `get_position(axis)` to `readings`; progress
     `f"{axis}: cycle {k}/{cycles} — edge at {pos} cts"`. A `wait_idle` timeout ends the run with
     `(False, f"{axis}: timeout on cycle {k}/{cycles}", readings)`.
   - After the last cycle return `(True, f"{axis}: {cycles} edge returns at {pass_speed_cps} cps", readings)`.
   - On every exit path call `AxisHomeRoutine._restore_speeds` for the axis (best-effort).
   - It never calls `define_zero` or `define_zero_multi`.
3. `EdgeReturnWorker(QThread)` with signals `progress = Signal(str)` and
   `done = Signal(bool, str, list)`, `cancel()`, and `run()` driving one `EdgeReturnRoutine`, as
   `HomingWorker` drives `AxisHomeRoutine`.
4. Add an **EDGE-RETURN TEST** paragraph to the module docstring: what it measures, why it never
   zeroes, why every cycle approaches the edge from the same side (out, then one home pass back).

## Acceptance criteria

- [ ] Test `test_edge_return_collects_one_reading_per_cycle` in `tests/test_edge_return_routine.py`
      (new file): a `MagicMock(spec=GalilController)` that is never moving, has clear switches,
      `get_stop_code` returning `SC.HOME_STOP_CODE` and `get_position.side_effect = [1000, 1002, 999]`;
      `EdgeReturnRoutine(g, "A", 58, cycles=3, out_counts=2000).run()` returns
      `(True, "A: 3 edge returns at 58 cps", [1000, 1002, 999])`.
- [ ] Test `test_edge_return_never_defines_zero` in `tests/test_edge_return_routine.py`: after that
      run, `g.define_zero` and `g.define_zero_multi` were never called, `g.move_relative` was called
      three times with `("A", 2000)`, and every `begin_home` call was `("A", 58, fine_speed=58)`.
- [ ] Test `test_edge_return_stops_on_a_bad_stop_code` in `tests/test_edge_return_routine.py`:
      `get_stop_code.side_effect = [10, 3]`; the run returns `False`, a message containing
      `"cycle 2/3 did not stop on the home switch (stop code 3)"`, and readings of length 1.
- [ ] Test `test_edge_return_cancel_keeps_the_readings_so_far` in `tests/test_edge_return_routine.py`:
      a `cancelled` callable that turns true after the first reading; the run returns `False`,
      `"cancelled after 1 cycles"` in the message, and one reading.
- [ ] Test `test_edge_return_worker_emits_done_with_readings` in `tests/test_edge_return_routine.py`:
      `EdgeReturnWorker` on the same mock, `run()` called directly (not `start()`), collects one `done`
      emission whose third argument is the readings list.

**What tests may fake:** the controller (`MagicMock(spec=GalilController)`) and `time.sleep` (patch
`rbl.hardware.galil_workers.time.sleep`, as `tests/test_auto_home.py` does). The routine and worker
are real.

**Out of scope:** the analysis (ticket 129), the tab, running all three speeds, and the CSV
(ticket 131). Running on several axes at once.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
