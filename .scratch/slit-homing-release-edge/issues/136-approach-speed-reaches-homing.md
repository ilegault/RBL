# 136: The operator's approach speed reaches Home and Auto-Home All

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 135

**Spec:** `.scratch/slit-homing-release-edge/spec.md`
**Binding:** `docs/adr/0009-slits-homed-on-the-release-edge.md`, `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/state/motor_control.py` (`_init_motors`, `slit_speed_cps` /
`set_slit_speed_cps` and `home_pass_speeds` / `set_home_pass_speeds` for the state-plus-validation
pattern), `src/rbl/gui/motor_tab.py` (`AxisControls._start_homing`, `MotorTab._start_auto_home`),
`src/rbl/hardware/galil_workers.py` (`HomingWorker`, `AutoHomeAllWorker`, their
`approach_speed` argument from ticket 135), `tests/test_beamline.py`
(`TestMoveSlit.test_home_pass_speeds_are_validated` for the test shape),
`tests/test_auto_home.py` (`TestMotorTabAutoHome`, its `tab` fixture, and how the deleted
`test_edited_pass_speeds_reach_the_next_auto_home` patched the worker class).

## What to build

One approach speed, held on `Beamline` and shared by all four axes, is what every homing run
started from the Stepper Motors tab uses to drive onto the home switch.

1. `src/rbl/config/hardware_config.py`: `HOME_APPROACH_SPEED_MAX_COUNTS_PER_SEC = 500`, next to
   `HOME_APPROACH_SPEED_COUNTS_PER_SEC`, with a comment (the approach does not decide where zero
   lands, but a fast approach drives deeper past the switch and makes each creep longer).
2. `MotorControlMixin` in `src/rbl/state/motor_control.py`: `_init_motors` sets
   `self._home_approach_speed_cps: int = SC.HOME_APPROACH_SPEED_COUNTS_PER_SEC`. Add
   `home_approach_speed(self) -> int` and `set_home_approach_speed(self, cps) -> None`; the
   setter converts with `int(cps)` and raises
   `ValueError(f"approach speed must be in 1..{SC.HOME_APPROACH_SPEED_MAX_COUNTS_PER_SEC} counts/s, got {cps!r}")`
   outside that range, leaving the old value. Not saved between runs. `home_pass_speeds` stays
   until ticket 138.
3. `AxisControls._start_homing` constructs `HomingWorker(..., seek_first=seek_first, approach_speed=self.beamline.home_approach_speed())`;
   `MotorTab._start_auto_home` constructs
   `AutoHomeAllWorker(self.galil, SC.AXIS_LETTERS, self, approach_speed=self.beamline.home_approach_speed())`.
   The value is read when the run starts.

## Acceptance criteria

- [x] Test `test_home_approach_speed_defaults_and_is_validated` in `tests/test_beamline.py`: a
      new `Beamline` reports `58`; after `set_home_approach_speed(40)` it reports `40`;
      `set_home_approach_speed(0)` and `set_home_approach_speed(501)` each raise `ValueError` with
      `"approach speed must be in 1..500"` in the message, and it still reports `40`.
- [x] Test `test_approach_speed_reaches_the_next_auto_home` in `tests/test_auto_home.py`:
      `tab.beamline.set_home_approach_speed(40)`, `rbl.gui.motor_tab.AutoHomeAllWorker` patched
      with a `MagicMock`, the confirmation patched to Yes; clicking `btn_auto_home_seq` constructs
      the worker once with `approach_speed == 40` in its keyword arguments.
- [x] Test `test_approach_speed_reaches_a_per_axis_home` in `tests/test_auto_home.py`: with
      `rbl.gui.motor_tab.HomingWorker` patched and the approach speed set to 40, clicking axis A's
      `btn_home` and then axis B's `btn_seek_home` each construct the worker with
      `approach_speed == 40`.

**What tests may fake:** the worker classes (a `MagicMock` patched into `rbl.gui.motor_tab`), the
Galil (`MagicMock(connected=True)`), the confirmation dialog. `Beamline` and `MotorTab` are real.

**Out of scope:** the on-screen Approach speed box and removing the pass-speed boxes (138). The
edge-return test (137).

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments

2026-11-18: Implemented shared `home_approach_speed` on `Beamline` (`MotorControlMixin`) with `HOME_APPROACH_SPEED_MAX_COUNTS_PER_SEC = 500` in `hardware_config.py`, and wired `beamline.home_approach_speed()` into `AxisControls._start_homing` (`HomingWorker`) and `MotorTab._start_auto_home` (`AutoHomeAllWorker`) in `motor_tab.py`. Covered by `test_home_approach_speed_defaults_and_is_validated` (`tests/test_beamline.py`), `test_approach_speed_reaches_the_next_auto_home`, and `test_approach_speed_reaches_a_per_axis_home` (`tests/test_auto_home.py`). Bench verification is deferred to ticket 142.
