# 122: The Stepper Motors tab's Speed box sets the slit speed

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 121

**Spec:** `.scratch/slit-motion/spec.md`
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/motor_tab.py` (`AxisControls.__init__` speed row, `_speed_in_cps`,
`_on_speed_unit_changed`, `_jog`, `_move_absolute`), `src/rbl/state/motor_control.py`
(`slit_speed_cps`, `set_slit_speed_cps`, `move_axis_counts`, all added by ticket 121),
`tests/test_slit_cross_screen.py` (`screens`, `_FakeGalil`), `tests/test_gui_hardware.py`
(`TestMotorTabUnits`).

## What to build

Today the per-axis Speed box only reaches the Galil when Jog is pressed, so changing it and
then pressing Move does nothing to the speed. After this ticket the box **is** the axis's
slit speed on `Beamline`: editing it changes the speed of the next Jog and the next Move on
that axis, and of moves to that slit from the Overview and the Raster Planner.

1. `AxisControls.__init__`: the Speed box starts at `beamline.slit_speed_cps(axis)` (cps
   unit). Its form label becomes `"Slit speed:"` and its tooltip says exactly:
   `"Speed for Jog and Move on this axis, and for moves to this slit from the Overview and the Raster Planner. Applies to the next command."`
2. Connect `spn_speed.valueChanged` to a new `AxisControls._on_speed_edited(self, _value)` that
   calls `self.beamline.set_slit_speed_cps(self.axis, self._speed_in_cps())`. `_speed_in_cps`
   already handles the mm/s unit. A unit switch (`_on_speed_unit_changed`) re-expresses the
   same speed and must leave `beamline.slit_speed_cps(axis)` unchanged (rounding to whole cps
   is the only allowed change).
3. `_jog`: the jog speed is `self.beamline.slit_speed_cps(self.axis)`, signed by direction, sent
   with `g.jog_start` exactly as now. Log line unchanged.
4. `_move_absolute`: keep the log line through `beamline.log_motor` and `note_slit_target` as
   now, but prefix the logged text with `f"SP {self.axis}={speed} ; "` after the `"> "`, and send
   the move with `self.beamline.move_axis_counts(self.axis, target)` instead of
   `g.move_absolute`. The existing `GalilError` code-22 soft-limit dialog and the
   `ConnectionError` handling stay as they are.
5. `src/rbl/config/hardware_config.py`: delete `DEFAULT_JOG_SPEED`. Nothing else may read it.
   Update the `motor_tab.py` module docstring line about "Jog speed and target position" to say
   the box is the axis's slit speed.

## Acceptance criteria

- [ ] Test `test_speed_box_edit_changes_the_next_move_speed` in `tests/test_slit_cross_screen.py`:
      on the `screens` fixture, set `motors.axes["A"].spn_speed` to 450 (cps), set the target to
      1.5 mm, click `btn_move`; `beamline.slit_speed_cps("A") == 450` and
      `beamline.galil.speeds == [450]`. No jog is pressed anywhere in the test.
- [ ] Test `test_speed_box_edit_reaches_overview_moves` in `tests/test_slit_cross_screen.py`: set
      `motors.axes["B"].spn_speed` to 220, then `beamline.move_slit("X-", 2.0)`;
      `beamline.galil.speeds == [220]`.
- [ ] Test `test_jog_uses_the_slit_speed` in `tests/test_slit_cross_screen.py`: give `_FakeGalil`
      a `jog_start(axis, signed_speed)` that records `(axis, signed_speed)` in a `jogs` list
      (helper change); `beamline.set_slit_speed_cps("C", 333)`; call `motors.axes["C"]._jog(-1)`;
      `beamline.galil.jogs == [("C", -333)]`.
- [ ] Test `test_switching_speed_unit_keeps_the_slit_speed` in `tests/test_gui_hardware.py`
      (class `TestMotorTabUnits`): set panel A's speed to 1260 cps, switch `cbo_speed_unit` to
      `"mm/s"`, then back to `"cps"`; after each switch `abs(beamline.slit_speed_cps("A") - 1260) <= 1`.
      Build the `MotorTab` on a `Beamline` you keep a reference to.
- [ ] The existing test `test_default_speed_is_cps` in `tests/test_gui_hardware.py` is rewritten in
      place (same name) to assert `panel._speed_in_cps() == SC.DEFAULT_SPEED_COUNTS_PER_SEC`.
      `grep -rn DEFAULT_JOG_SPEED src tests` returns nothing. (by hand for the grep)

**What tests may fake:** the Galil (`_FakeGalil`, `MagicMock`). `Beamline`, `MotorTab`,
`AxisControls` and `OverviewTab` are real.

**Out of scope:** changing the speed of a jog that is already running (the next command picks
it up). Saving the speed between runs. Homing speeds. The floor on targets (ticket 128).

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
