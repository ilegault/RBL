# 135: Homing finds the release edge (one axis and Auto-Home All)

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/slit-homing-release-edge/spec.md`
**Binding:** `docs/adr/0009-slits-homed-on-the-release-edge.md`, `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Deletes tests:** tests/test_auto_home.py::test_seek_then_three_passes_then_zero, tests/test_auto_home.py::test_each_pass_turns_down_both_of_hm_s_stages, tests/test_auto_home.py::test_it_puts_both_speeds_back, tests/test_auto_home.py::test_zero_is_defined_because_hm_does_not_do_it_on_a_stepper, tests/test_auto_home.py::test_routine_uses_the_speeds_it_is_given, tests/test_auto_home.py::test_a_pass_that_stops_on_a_limit_sets_no_zero, tests/test_auto_home.py::test_edited_pass_speeds_reach_the_next_auto_home, tests/test_auto_home.py::test_edited_pass_speeds_reach_a_per_axis_home

**Read first:** `docs/adr/0009-slits-homed-on-the-release-edge.md` (the whole decision),
`src/rbl/hardware/galil_workers.py` (`AxisHomeRoutine.run`, `back_out_of_home`,
`seek_home_limit`, `_arrival`, `_reverse_msg`, `home_passes`, `_restore_speeds`, `wait_idle`;
`HomingWorker`; `AutoHomeAllWorker`), `src/rbl/hardware/galil_driver.py` (`GalilError`,
`move_relative`, `jog_start`, `stop`, `get_switch_states`, `get_position`, `define_zero`,
`set_speed`), `src/rbl/config/hardware_config.py` (the homing constants block above
`HOME_PASS_SPEEDS_COUNTS_PER_SEC`), `src/rbl/gui/motor_tab.py` (`AxisControls._start_homing`,
`MotorTab._start_auto_home`), `tests/test_auto_home.py` (`no_sleep` fixture, `_galil`,
`_jogging_galil`, `tab` fixture), `tests/payloads.py` (how a shared test helper module is laid
out and imported as `from tests.payloads import ...`).

## What to build

Home, Seek + Home and Auto-Home All (one at a time) work again on slits whose home switch and
reverse limit trip at the same point. Each axis is homed by two passes that each drive onto the
switch and then step back out one count at a time until the home switch releases; zero goes on
the second pass's release point, and only if the two release points agree.

1. **`tests/sim_slit.py` (new): the simulated slit.** A test helper, not a test file. This is a
   requirement, not a convenience: the behaviour under test is "where does zero land given where
   the switch is", and only a simulated position answers that; scripted return values do not.
   - `SimSlit(start_at=2000, trip_at=-3, release_at=6, reverse_trip_at=None, reverse_release_at=None, overshoot_per_100_cps=1, slip_on_jog=None)`.
     Holds `phys` (physical position, starts at `start_at`) and `offset` (starts 0); the commanded
     position `_RP` is `phys + offset`.
   - The home switch becomes tripped once `phys <= trip_at` and stays tripped until
     `phys >= release_at` (hysteresis; the state is remembered between reads). The reverse limit
     works the same with `reverse_trip_at` / `reverse_release_at`, each defaulting to the home
     values (one shared sensor). The forward limit is never tripped.
   - `zeroed_at: list[int]` records `_RP` at every `define_zero`; `define_zero` then sets
     `offset` so `_RP` reads 0. `deepest: int` is the lowest `phys` ever reached.
   - `slip_on_jog: dict[int, int] | None` — `{n: c}` means the n-th negative jog on this axis
     (counted from 1) loses `c` counts: after it, `offset -= c` (the controller's count runs `c`
     ahead of the motor, as a stalled stepper does).
   - `SimGalil(axes="ABCD", **slit_kwargs)` holds one `SimSlit` per axis in `self.slits`
     (same kwargs for each; a test may replace `sim.slits["B"]` with its own `SimSlit`), has
     `connected = True`, records every call as `(method_name, args)` in `self.calls`, and
     implements, with the driver's signatures: `get_switch_states(axis)` (the driver's dict keys),
     `get_position(axis)`, `is_moving(axis)` (always False: motion completes inside the call),
     `get_stop_code(axis)`, `set_speed(axis, cps)`, `set_home_velocity(axis, cps)` (recorded
     only), `stop(axis)`, `define_zero(axis)`, `move_relative(axis, delta)` and
     `jog_start(axis, signed_speed)`.
   - Motion rules: a positive `move_relative` always completes (stop code 1). A negative
     `move_relative` or `jog_start` while the reverse limit is tripped raises
     `GalilError("BG", "22 Begin not possible due to limit switch", 22)`. A negative
     `move_relative` stops early at the point the reverse limit trips (stop code 3), otherwise
     completes (stop code 1). A negative `jog_start` runs until the home switch or the reverse
     limit trips, whichever comes first, then travels a further
     `abs(signed_speed) // 100 * overshoot_per_100_cps` counts (stop code 3 if the reverse limit
     is tripped, else 4), then applies `slip_on_jog`. A positive `jog_start` raises
     `AssertionError` (homing never jogs out).
2. **Constants** in `src/rbl/config/hardware_config.py`, each with a comment citing ADR 0009:
   `HOME_APPROACH_SPEED_COUNTS_PER_SEC = 58`, `HOME_CREEP_STEP_COUNTS = 1`,
   `HOME_CREEP_MAX_COUNTS = 400`, `HOME_PASS2_BACKOFF_COUNTS = 300`,
   `HOME_EDGE_AGREEMENT_COUNTS = 4`. Leave `HOME_PASS_SPEEDS_COUNTS_PER_SEC`, `HOME_STOP_CODE`
   and `DEFAULT_HOME_VELOCITY_COUNTS_PER_SEC` in place (the Together path and the edge-return
   test still use them until tickets 137 and 138).
3. **`AxisHomeRoutine`** in `src/rbl/hardware/galil_workers.py`:
   - Constructor `__init__(self, galil, axis, progress=None, cancelled=None, approach_speed: int | None = None)`;
     `None` means `SC.HOME_APPROACH_SPEED_COUNTS_PER_SEC`. Remove `SPEEDS`, `BACKOFFS` and the
     `speeds` argument.
   - New `approach(self, speed: int) -> tuple[bool, str]`: if the home switch already reads
     tripped, return `(True, "")`. Otherwise `jog_start(axis, -speed)`, sleep `_SEEK_SETTLE_S`,
     then wait until `is_moving` is False (cancel: `stop`, return
     `(False, f"{axis}: cancelled while approaching the home switch")`; timeout after
     `SC.HOME_SEEK_TIMEOUT_S`: `stop`, return a timeout failure). Then by `_arrival()`:
     `"home"` → `(True, "")`; `"reverse"` → `(False, self._reverse_msg(axis))`;
     `""` → `(False, f"{axis}: approach stopped before reaching the home switch")`.
     The axis stopping on the reverse limit is expected and is not checked by stop code.
   - New `creep_to_release(self) -> tuple[bool, str, int | None]`: `set_speed(axis, self.approach_speed)`,
     then repeat: `move_relative(axis, SC.HOME_CREEP_STEP_COUNTS)`, `wait_idle(timeout=15.0)`
     (False → timeout failure), and if `get_switch_states(axis)["home_switch"]` is False return
     `(True, "", g.get_position(axis))`. Check `self._cancelled()` before every step. After
     `SC.HOME_CREEP_MAX_COUNTS` counts without a release return
     `(False, f"{axis}: home switch did not release within {SC.HOME_CREEP_MAX_COUNTS} counts — check the sensor", None)`.
   - `home_passes(self)` rewritten: pass 1 = `approach(self.approach_speed)` (a no-op when a seek
     already put the axis on the switch) then `creep_to_release()` → `edge1`, progress
     `f"{axis}: pass 1/2 — edge at {edge1} cts"`. Pass 2 = `set_speed(axis, SC.HOME_SEEK_SPEED_COUNTS_PER_SEC)`,
     `move_relative(axis, SC.HOME_PASS2_BACKOFF_COUNTS)`, `wait_idle`, fail with
     `f"{axis}: still on the home switch after backing out {SC.HOME_PASS2_BACKOFF_COUNTS} counts — zero not set"`
     if the home switch still reads tripped, then `approach(self.approach_speed)` and
     `creep_to_release()` → `edge2`, progress `f"{axis}: pass 2/2 — edge at {edge2} cts"`.
     If `abs(edge2 - edge1) > SC.HOME_EDGE_AGREEMENT_COUNTS` return
     `(False, f"{axis}: home edges disagree — pass 1 at {edge1} cts, pass 2 at {edge2} cts (limit {SC.HOME_EDGE_AGREEMENT_COUNTS}) — zero not set")`
     without calling `define_zero`. Otherwise `define_zero(axis)` and return
     `(True, f"{axis}: homed — edges {edge1}, {edge2} cts, DP=0 at pass 2")`. Any failure of a
     step is returned as is. `_restore_speeds()` runs on every exit path.
   - `_restore_speeds` sets SP back to `SC.DEFAULT_SPEED_COUNTS_PER_SEC` only; homing no longer
     touches HV.
   - `run()` keeps its order: `back_out_of_home`, then `seek_home_limit` if `seek_first`, then
     `home_passes`. `begin_home` and the stop-code check are no longer used by this class.
4. `HomingWorker(galil, axis, parent=None, seek_first=False, approach_speed=None)` and
   `AutoHomeAllWorker(galil, axes=None, parent=None, seek_first=True, stop_on_failure=True, approach_speed=None)`
   pass `approach_speed` to `AxisHomeRoutine`; their `speeds` argument and `HomingWorker._SPEEDS`
   / `_BACKOFFS` are removed.
5. `src/rbl/gui/motor_tab.py`: `AxisControls._start_homing` and `MotorTab._start_auto_home`
   stop passing `speeds=`; they pass nothing (ticket 136 wires the operator's approach speed).
   The three pass-speed boxes stay on screen for now and reach only the Together button and the
   edge-return test.
6. The `galil_workers.py` module docstring: replace the "WHAT HOMING ACTUALLY TAKES" and
   "A PASS IS ACCEPTED ONLY ON STOP CODE 10" sections with a "HOMING ON THE RELEASE EDGE"
   section that explains the approach, the creep out, the two passes and the agreement check, and
   points at ADR 0009. Leave the Together and edge-return sections for tickets 137 and 138.

Existing tests: rewrite in place, under the same name, `test_a_failed_seek_never_reaches_hm`
(assert no creep step and no `define_zero`), `test_the_speeds_go_back_after_a_failure_too`
(SP restored to `SC.DEFAULT_SPEED_COUNTS_PER_SEC` after a failed run) and
`test_without_seek_it_is_the_old_routine` (no jog at the seek speed; the first jog is at the
approach speed), each driving `SimGalil`. Any other test in `tests/test_auto_home.py` that fails
only because its mock scripted the old HM passes is rewritten in place under the same name to
drive `SimGalil`. Every test not listed at the top of this ticket stays.

## Acceptance criteria

- [x] Test `test_homing_zeroes_on_the_release_edge` in `tests/test_auto_home.py`:
      `SimGalil(trip_at=-3, release_at=6, start_at=2000)`; `AxisHomeRoutine(sim, "A").run(seek_first=True)`
      returns `(True, msg)` with `"edges 6, 6 cts"` in `msg`, and `sim.slits["A"].zeroed_at == [6]`.
- [x] Test `test_approach_speed_does_not_move_the_edge` in `tests/test_auto_home.py`: two fresh
      `SimGalil(trip_at=-3, release_at=6)`, one homed with `approach_speed=58` and one with
      `approach_speed=500` (both `run(seek_first=False)`); their `deepest` values differ, and both
      `zeroed_at` lists are `[6]`.
- [x] Test `test_a_lost_step_between_passes_sets_no_zero` in `tests/test_auto_home.py`:
      `SimGalil(trip_at=-3, release_at=6, slip_on_jog={2: 8})`; `run(seek_first=True)` returns
      `(False, msg)` with `"home edges disagree — pass 1 at 6 cts, pass 2 at -2 cts (limit 4)"` in
      `msg`, and `zeroed_at == []`.
- [x] Test `test_a_switch_that_never_releases_sets_no_zero` in `tests/test_auto_home.py`:
      `SimGalil(trip_at=-3, release_at=1000)`; `run(seek_first=True)` returns `(False, msg)` with
      `"home switch did not release within 400 counts — check the sensor"` in `msg`, and
      `zeroed_at == []`.
- [x] Test `test_an_approach_stopped_by_the_limit_off_home_fails` in `tests/test_auto_home.py`:
      `SimGalil(trip_at=-50, release_at=-40, reverse_trip_at=-3, reverse_release_at=6)`;
      `run(seek_first=False)` returns `(False, msg)` with
      `"reached the reverse limit before the home switch"` in `msg`, and `zeroed_at == []`.
- [x] Test `test_auto_home_all_zeroes_every_axis_on_its_release_edge` in `tests/test_auto_home.py`:
      a `SimGalil` whose four slits have release points 6, 7, 5 and 6 (A–D); `AutoHomeAllWorker(sim)`
      with `done` connected to a list, called via `.run()` (not `.start()`); each slit's `zeroed_at`
      is `[its release point]` and `done` carried `True`.

**What tests may fake:** the controller, with `SimGalil` from `tests/sim_slit.py` for routine
tests and `MagicMock(spec=GalilController)` only where a test checks the tab's wiring;
`time.sleep` (the `no_sleep` fixture). `AxisHomeRoutine`, `HomingWorker` and `AutoHomeAllWorker`
are real.

**Out of scope:** the operator's approach speed on `Beamline` (136). The edge-return test (137).
Removing the pass-speed boxes, the Together button, `MultiAxisHomeRoutine`, `begin_home*` or the
old constants (138). The switch map (139, 140). The status label (141). CN, MT, BL/FL, motion
directions.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments

- 2026-11-18: Implemented release-edge homing (ADR 0009):
  - Added `tests/sim_slit.py` (`SimSlit`, `SimGalil`).
  - Added ADR 0009 homing constants in `src/rbl/config/hardware_config.py`.
  - Rewrote `AxisHomeRoutine` (`approach`, `creep_to_release`, `home_passes`, `_restore_speeds`, module docstring) and updated `HomingWorker` / `AutoHomeAllWorker` in `src/rbl/hardware/galil_workers.py`.
  - Updated `AxisControls._start_homing` and `MotorTab._start_auto_home` in `src/rbl/gui/motor_tab.py` to stop passing `speeds=`.
  - Updated `tests/test_auto_home.py` (added `TestReleaseEdgeHoming`, rewrote `SimGalil`-backed tests in place, deleted the 8 tests listed in `Deletes tests:`) and `tests/test_gui_hardware.py` (`test_three_passes_then_define_zero` driven by `SimGalil`).
  - Bench verification deferred to ticket 142.
