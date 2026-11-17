# 124: Every homing run starts by backing out of home

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 123

**Spec:** `.scratch/slit-motion/spec.md`
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/hardware/galil_workers.py` (`AxisHomeRoutine.run`, `seek_home_limit`,
`home_passes` and its existing "on home switch — backing off" block, `wait_idle`;
`MultiAxisHomeRoutine.run`, `home_passes`, `wait_idle`), `src/rbl/hardware/galil_driver.py`
(`move_relative`, `move_relative_multi`, `set_speed`, `set_speed_multi`, `get_switch_states`),
`tests/test_auto_home.py` (`_galil`, `_switches`, `TestRoutineEndToEnd`, `TestMultiAxisHomeRoutine`).

## What to build

An axis that was turned off (MO) sitting on its home switch gives homing a confusing start.
After this ticket **every homing run begins by backing out of home** before anything else:
the per-axis Home and Seek + Home buttons, Auto-Home All — One at a Time, and Auto-Home All —
Together. Directions do not change: positive counts are away from home on all four axes, as
the existing back-off in `home_passes` already assumes.

1. `hardware_config.py`, under the automatic-homing constants: `HOME_START_BACKOFF_COUNTS = 1000`
   and `HOME_START_BACKOFF_TRIES = 3`, with a comment saying why (an axis left on its home switch
   when its motor was turned off; 1000 counts ≈ 1.6 mm).
2. `AxisHomeRoutine.back_out_of_home(self) -> tuple[bool, str]`:
   - If `get_switch_states(axis)["forward_switch"]` is active, report
     `f"{axis}: on the forward limit — no back-out needed"` through progress and return `(True, "")`.
   - If neither the home switch nor the reverse limit is active, return `(True, "")` with no motion.
   - Otherwise call `g.set_speed(axis, SC.HOME_SEEK_SPEED_COUNTS_PER_SEC)`, then up to
     `HOME_START_BACKOFF_TRIES` times: `g.move_relative(axis, SC.HOME_START_BACKOFF_COUNTS)`,
     `wait_idle(timeout=15.0)` (a timeout returns `(False, f"{axis}: timeout backing out of home")`),
     then re-read the switches; once neither the home switch nor the reverse limit is active,
     return `(True, "")`.
   - After the last try: return
     `(False, f"{axis}: still on the home switch after backing out {tries} × {counts} counts — zero not set")`.
   - Check `self._cancelled()` before each move; cancelled returns `(False, f"{axis}: cancelled while backing out of home")`.
3. `AxisHomeRoutine.run`: call `back_out_of_home()` first, for both `seek_first` values; on failure
   return its result. Then the seek (if asked) and `home_passes` as now. Keep the existing
   back-off block inside `home_passes`: after a seek the axis is ON the switch again and still
   needs it.
4. `MultiAxisHomeRoutine.back_out_of_homes(self) -> tuple[bool, str]`: the same rule, per axis,
   with one `move_relative_multi` over the axes that need it per try, and `set_speed_multi` first.
   Axes on their forward limit are left out. Axes still on home after the last try are named:
   `"Still on the home switch after backing out: " + ", ".join(axes) + " — no zero set"`, each
   reported through `axis_done(axis, False, ...)`. `run` calls it first for both `seek_first` values.
5. Add a short **BACKING OUT FIRST** paragraph to the `galil_workers.py` module docstring.

Existing tests: the back-out reads the switches once before the seek does. Existing tests in
`tests/test_auto_home.py` that script switch readings one per query (`_jogging_galil`,
`side_effect` lists) get ONE extra leading reading for that check (clear, unless the test is
about an axis on its home switch). That is a change to the scripted controller, not to what
the test asserts; no assertion is edited or removed. If an existing test cannot pass that way,
escalate rather than editing its assertions.

## Acceptance criteria

- [ ] Test `test_homing_backs_out_before_anything_else` in `tests/test_auto_home.py`: a `_galil()`
      whose `get_switch_states` returns home-active on the first read and clear on every later
      read; `AxisHomeRoutine(g, "A").run()` returns success, and the first motion call in
      `g.method_calls` is `move_relative("A", SC.HOME_START_BACKOFF_COUNTS)`, before any
      `begin_home` or `jog_start`.
- [ ] Test `test_back_out_gives_up_after_the_last_try` in `tests/test_auto_home.py`: switches report
      the home switch active on every read; `run()` returns `(False, msg)` with
      `"still on the home switch after backing out 3"` in `msg`; `move_relative` was called exactly
      `SC.HOME_START_BACKOFF_TRIES` times with `SC.HOME_START_BACKOFF_COUNTS`; `begin_home` and
      `define_zero` were never called.
- [ ] Test `test_no_back_out_on_the_forward_limit` in `tests/test_auto_home.py`: switches report the
      forward limit active and nothing else; `back_out_of_home()` returns `(True, "")` and
      `move_relative` was never called.
- [ ] Test `test_together_backs_out_only_the_axes_on_home` in `tests/test_auto_home.py` (class
      `TestMultiAxisHomeRoutine`): B and D report home active on their first read and clear after;
      A and C are clear throughout. `MultiAxisHomeRoutine(g).run(seek_first=False)` succeeds, and
      the first `move_relative_multi` call is `("BD", SC.HOME_START_BACKOFF_COUNTS)`.
- [ ] Test `test_seek_and_home_button_backs_out_first` in `tests/test_auto_home.py`: drive the
      Stepper Motors tab's per-axis Seek + Home the way the file's `TestSeekAndHomeButton` class
      already does, with the controller reporting axis A on its home switch at the start; the
      first motion command the controller receives is the back-out `move_relative`.

**What tests may fake:** the controller (`MagicMock(spec=GalilController)` via the file's helpers)
and `time.sleep`. The routines, workers and `MotorTab` are real.

**Out of scope:** adjustable pass speeds (ticket 125). Any change of direction or to the seek
speed. The stop-code checks (ticket 123, already done).

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
