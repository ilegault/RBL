# 147: Slit motion runs at NEC's speeds

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/slit-homing-release-edge/spec.md` (Addendum 2026-11-20)
**Binding:** `docs/adr/0009-slits-homed-on-the-release-edge.md` (amended 2026-11-20), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/config/hardware_config.py` (`DEFAULT_SPEED_COUNTS_PER_SEC`, the comment block
above `HOME_SEEK_SPEED_COUNTS_PER_SEC`, `HOME_APPROACH_SPEED_COUNTS_PER_SEC` and its comment,
`SWITCH_MAP_STEP_SPEED_COUNTS_PER_SEC`), `src/rbl/hardware/galil_workers.py` (the module docstring's
"Approach" paragraph, `EdgeReturnRoutine.run` and the `set_speed` / `move_relative` pair at the top of
each cycle, `SwitchMapRoutine.run`), `tests/sim_slit.py` (`SimGalil.calls` records every call in
order as `(name, args)`, e.g. `("set_speed", ("A", 900))` and `("move_relative", ("A", 2000))`),
`tests/test_edge_return_routine.py` (`test_edge_return_collects_one_reading_per_cycle` for how a
routine is built and run on `SimGalil`), `tests/test_switch_map_routine.py`
(`test_switch_map_finds_a_shared_switch`), `tests/test_auto_home.py` (`TestReleaseEdgeHoming`,
`test_approach_speed_box_edits_the_beamline`), `tests/test_beamline.py`
(`test_home_approach_speed_defaults_and_is_validated`).

## What to build

On the bench, slow continuous motion loses steps on every slit axis; fast motion does not. NEC's spec
for the 2HA075520 slit controller gives 1800 steps/s for normal motion and 900 steps/s for homing.
After this ticket every slit motion runs at those speeds by default.

1. `src/rbl/config/hardware_config.py`:
   - `DEFAULT_SPEED_COUNTS_PER_SEC = 1800`. Comment: NEC's normal speed (`SP 1800`, 2HA075520 spec
     e-mail); the starting Slit speed of every axis at launch (not saved).
   - `HOME_SEEK_SPEED_COUNTS_PER_SEC = 900`. Rewrite the comment block above it: it is NEC's homing
     speed (`SP 900`); the seek, every homing back-off, and the switch map's back-off and run-in use it;
     slow continuous motion loses steps on these slits (bench, 2026-11-19), so nothing automatic runs
     slower than this. Delete the sentences about "the three HM passes" and "58 cps"; they describe
     code that no longer exists.
   - `HOME_APPROACH_SPEED_COUNTS_PER_SEC = 900`, comment `# ADR 0009 (amended 2026-11-20): default
     approach speed (cps)`. The comment above `HOME_APPROACH_SPEED_MAX_COUNTS_PER_SEC` stays.
   - `SWITCH_MAP_STEP_SPEED_COUNTS_PER_SEC = 900`. Comment: each step is one count, so the speed does
     not set the accuracy; it is NEC's homing speed so no automatic motion is set in the slow range
     where steps are lost.
2. `src/rbl/hardware/galil_workers.py`, `EdgeReturnRoutine.run`: the move out at the top of each cycle
   runs at the run's approach speed. Replace `g.set_speed(axis, SC.HOME_SEEK_SPEED_COUNTS_PER_SEC)`
   immediately before `g.move_relative(axis, self.out_counts)` with
   `g.set_speed(axis, self.approach_speed)`. Add one sentence to the class docstring: the move out runs
   at the approach speed being measured, so the axis never leaves the switch slower than it arrived.
   The start-of-run `home.back_out_of_home()` is unchanged.
3. `src/rbl/hardware/galil_workers.py`, module docstring: in the "Approach" step, "58 cps" becomes
   "900 cps".

Homing's own back-offs (`AxisHomeRoutine`) keep using `HOME_SEEK_SPEED_COUNTS_PER_SEC`. No speed is
saved between runs.

## Acceptance criteria

- [x] Test `test_edge_return_moves_out_at_the_approach_speed` in `tests/test_edge_return_routine.py`:
      for approach speeds 58 and 1800, each on a fresh `SimGalil(trip_at=-3, release_at=6,
      start_at=2000)`, `EdgeReturnRoutine(sim, "A", speed, cycles=2, out_counts=2000).run()` succeeds,
      and for every `("move_relative", ("A", 2000))` in `sim.calls` the nearest earlier `set_speed`
      call for axis A has that speed (58, then 1800). On the current code it is 500.
- [x] Test `test_switch_map_steps_at_900` in `tests/test_switch_map_routine.py`: on
      `SimGalil(trip_at=-3, release_at=6, start_at=2000)`, `SwitchMapRoutine(sim, "A").run()` succeeds,
      and for every `move_relative` of +1 or -1 counts on axis A in `sim.calls`, the nearest earlier
      `set_speed` call for axis A is 900. On the current code it is 25.
- [x] Test `test_homing_seeks_approaches_and_backs_off_at_900` in `tests/test_auto_home.py` (in
      `TestReleaseEdgeHoming`): `AxisHomeRoutine(sim, "A").run(seek_first=True)` on
      `SimGalil(trip_at=-3, release_at=6, start_at=2000)` succeeds; every `jog_start` call in
      `sim.calls` is `("A", -900)`; and the nearest `set_speed` before `("move_relative", ("A", 300))`
      (the back-off before pass 2) is 900. On the current code the jogs are -500 and -58.
- [x] Test `test_launch_speeds_follow_necs_spec` in `tests/test_beamline.py` (in the same class as
      test_slit_speed_defaults_and_rejects_below_one, using its `beamline` fixture): every axis's
      `slit_speed_cps` is 1800 and `home_approach_speed()` is 900.
- [x] (no test: rewrites existing tests in place, proven by the gate) In `tests/test_beamline.py`,
      test_home_approach_speed_defaults_and_is_validated is rewritten in place under the same name: its
      first line asserts the default is 900 instead of 58; nothing else changes. In
      `tests/test_auto_home.py`, test_approach_speed_box_edits_the_beamline is rewritten in place under
      the same name: its first line asserts `value() == 900` instead of 58; nothing else changes.
- [x] (no test: CI's full suite) No test is deleted, and every test that passes an explicit speed
      (58, 500, 40 and so on) keeps it and passes unchanged.

**What tests may fake:** nothing beyond what `SimGalil` and the existing `beamline` / `tab` fixtures
already fake (the Galil). The routines, `Beamline` and `MotorTab` are real.

**Out of scope:** the at-rest wait (148), `KS` (149), the 2 cps minimum and "Min is N" (150, 151), a
floor on slow speeds, saving speeds, homing back-offs following the approach speed, `AC`/`DC`/`SD`.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_edge_return_routine.py tests/test_switch_map_routine.py tests/test_auto_home.py tests/test_beamline.py
```

pytest runs on the touched test files only; CI runs the full suite on the PR.

## Comments

2026-11-20: Implemented NEC's speeds (`DEFAULT_SPEED_COUNTS_PER_SEC = 1800`, `HOME_SEEK_SPEED_COUNTS_PER_SEC = 900`, `HOME_APPROACH_SPEED_COUNTS_PER_SEC = 900`, `SWITCH_MAP_STEP_SPEED_COUNTS_PER_SEC = 900`) in `src/rbl/config/hardware_config.py`, and updated `EdgeReturnRoutine.run` and docstrings in `src/rbl/hardware/galil_workers.py` so the move out runs at `self.approach_speed`. Added `test_edge_return_moves_out_at_the_approach_speed`, `test_switch_map_steps_at_900`, `test_homing_seeks_approaches_and_backs_off_at_900`, and `test_launch_speeds_follow_necs_spec`; rewrote `test_home_approach_speed_defaults_and_is_validated` and `test_approach_speed_box_edits_the_beamline` for the 900 cps default; updated `test_without_seek_it_is_the_old_routine` to pass `approach_speed=58` explicitly now that default approach and seek speeds are both 900. Bench verification covered by ticket 142.
