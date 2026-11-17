# 128: Nothing goes below the homed position once an axis is zeroed

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 122, 126

**Spec:** `.scratch/slit-motion/spec.md`
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/state/motor_control.py` (`move_slit`, `move_axis_counts`, `is_zeroed`),
`src/rbl/gui/motor_tab.py` (`AxisControls._jog`, `_stop`, `_move_absolute`),
`src/rbl/gui/widgets/slit_control.py` (`SlitControl.__init__` target box, `set_target_mm`),
`src/rbl/gui/overview_tab.py` (`_redraw_slits`), `src/rbl/config/hardware_config.py`
(`MM_ZERO_OFFSET`, `SLIT_DISPLAY_MIN_MM`), `tests/test_slit_cross_screen.py` (`screens`, `_FakeGalil`),
`tests/test_slit_control.py`.

## What to build

The homed position is 0 counts, where a slit sits 0.2 mm from beam centre. Today the Overview
accepts targets from 0.0 mm, up to 126 counts past home, and nothing anywhere refuses them. After
this ticket, **on a zeroed axis no target below 0 counts is sent, from any screen**. On an axis
that is not zeroed there is no floor (its counts do not mean anything yet), and homing is never
subject to the floor (it talks to the driver directly and is unchanged).

1. `MotorControlMixin.floor_refusal(self, axis_letter: str, counts: int) -> str | None`: returns
   `f"{slit}: {counts} counts is below the homed position (0 counts) — refused"` when
   `self.is_zeroed(axis_letter) and counts < 0`, else `None`. `slit` is `SC.AXIS_NAMES[axis_letter]`.
2. `move_slit`: after converting mm to counts and BEFORE logging, publishing the target or touching
   the driver, call `floor_refusal`. If it returns a message: `log_motor("! " + msg)`, emit
   `command_failed("motors", msg)`, return `False`. No target is published.
3. `move_axis_counts`: the same check first; on refusal log and emit as above and return `False`
   without calling the driver.
4. `AxisControls._move_absolute` (Stepper Motors tab): call `self.beamline.floor_refusal(...)` before
   its log line and `note_slit_target`; on refusal `self.beamline.log_motor("! " + msg)` and return.
5. `AxisControls._jog(-1)` on a zeroed axis does not jog: it logs
   `f"> SP {axis}={speed} ; PA {axis}=0 ; BG {axis}   (Jog − on a zeroed axis stops at home)"` and
   calls `self.beamline.move_axis_counts(self.axis, 0)`. Releasing the button still sends `ST` through
   `_stop` as today, so letting go early stops it short of 0. `_jog(+1)`, and `_jog(-1)` on an axis
   that is not zeroed, are unchanged.
6. `SlitControl.set_floor_mm(self, mm: float | None)`: sets the target box minimum to `mm`, or back to
   `SC.SLIT_DISPLAY_MIN_MM` for `None`. `OverviewTab._redraw_slits` calls it on every redraw with
   `SC.MM_ZERO_OFFSET[letter]` when that axis's snapshot is zeroed, else `None` (letter from
   `beamline.axis_letter_for(slit)`).

Add a **THE FLOOR** paragraph to the `motor_control.py` module docstring: what it is, why it only
applies once zeroed, and that homing is exempt.

## Acceptance criteria

- [ ] Test `test_move_below_home_on_a_zeroed_axis_is_refused` in `tests/test_slit_cross_screen.py`:
      on the `screens` fixture, `beamline.mark_zeroed("A")`, then `beamline.move_slit("X+", 0.1)`
      returns `False`; `beamline.galil.moves == []`; the console contains
      `"below the homed position (0 counts) — refused"`; the Overview's X+ target box value is not 0.1.
- [ ] Test `test_move_below_home_on_an_unzeroed_axis_is_sent` in `tests/test_slit_cross_screen.py`:
      no axis zeroed; `beamline.move_slit("X+", 0.1)` returns `True` and `beamline.galil.moves` has
      one entry with negative counts.
- [ ] Test `test_stepper_tab_move_below_home_is_refused` in `tests/test_slit_cross_screen.py`:
      `mark_zeroed("B")`; on panel B set the unit to counts, target `-50`, click `btn_move`;
      `beamline.galil.moves == []` and the console contains `"-50 counts is below the homed position"`.
- [ ] Test `test_jog_minus_on_a_zeroed_axis_moves_to_home` in `tests/test_slit_cross_screen.py`:
      `_FakeGalil` records jogs (helper from ticket 122); `mark_zeroed("C")`;
      `motors.axes["C"]._jog(-1)`; `beamline.galil.jogs == []` and `beamline.galil.moves == [("C", 0)]`.
      With C not zeroed, the same call records one jog with a negative speed and no move.
- [ ] Test `test_overview_target_floor_follows_zeroed` in `tests/test_slit_control.py`:
      `SlitControl("X+")`; `set_floor_mm(0.2)` makes `spn_target.minimum() == 0.2` and setting the
      value to 0.05 leaves it at 0.2; `set_floor_mm(None)` restores `SC.SLIT_DISPLAY_MIN_MM`.

**What tests may fake:** the Galil (`_FakeGalil`). `Beamline`, `MotorTab`, `OverviewTab` and
`SlitControl` are real.

**Out of scope:** writing a controller soft limit (BL). Any floor during homing or the edge-return
test. A ceiling (the controller's FL already refuses those).

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
