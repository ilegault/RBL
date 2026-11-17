# 125: The three home pass speeds are adjustable in the app

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 124

**Spec:** `.scratch/slit-motion/spec.md`
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/hardware/galil_workers.py` (`AxisHomeRoutine.SPEEDS`/`BACKOFFS` and
`home_passes`, `HomingWorker`, `MultiAxisHomeRoutine`, `MultiAxisHomeWorker`, `AutoHomeAllWorker`),
`src/rbl/gui/motor_tab.py` (`MotorTab._build_auto_home_box`, `_start_auto_home`,
`_start_auto_home_parallel`, `AxisControls._start_homing`), `src/rbl/state/motor_control.py`
(`set_slit_speed_cps` from ticket 121, the pattern to copy), `src/rbl/gui/widgets/inputs.py`
(`QuietDoubleSpinBox`), `tests/test_auto_home.py` (`TestMotorTabAutoHome`, `tab` fixture).

## What to build

Homing runs three home passes at 225, 112 and 58 cps, fixed in code. The operator suspects the
pass speed changes where the zero lands and wants to set them in the app. After this ticket the
three speeds are three boxes on the Stepper Motors tab, shared by all four axes, used by every
homing run started after they change. Each speed is still applied to both of HM's stages (SP and
HV) for its pass, exactly as today. Not saved between runs.

1. `hardware_config.py`: `HOME_PASS_SPEEDS_COUNTS_PER_SEC: tuple[int, int, int] = (225, 112, 58)`
   under the automatic-homing constants, and `HOME_PASS_SPEED_MAX_COUNTS_PER_SEC = 5000`.
   `AxisHomeRoutine.SPEEDS` becomes `SC.HOME_PASS_SPEEDS_COUNTS_PER_SEC` (the class attribute stays,
   as the default). `BACKOFFS` is unchanged.
2. `AxisHomeRoutine.__init__` and `MultiAxisHomeRoutine.__init__` take `speeds=None`; `None` means
   the config default. `home_passes` iterates `zip(self.speeds, self.BACKOFFS)`. `HomingWorker`,
   `MultiAxisHomeWorker` and `AutoHomeAllWorker` take `speeds=None` and pass it to the routine(s)
   they build.
3. `MotorControlMixin` (`src/rbl/state/motor_control.py`): `_init_motors` stores
   `self._home_pass_speeds = tuple(SC.HOME_PASS_SPEEDS_COUNTS_PER_SEC)`;
   `home_pass_speeds(self) -> tuple[int, int, int]`; `set_home_pass_speeds(self, speeds)` stores
   `tuple(int(s) for s in speeds)` and raises `ValueError` (storing nothing) unless there are
   exactly three and each is in `1 .. SC.HOME_PASS_SPEED_MAX_COUNTS_PER_SEC`.
4. `MotorTab._build_auto_home_box`: a row of three `QuietDoubleSpinBox` (decimals 0, range
   1–`HOME_PASS_SPEED_MAX_COUNTS_PER_SEC`, initial values from `beamline.home_pass_speeds()`),
   attributes `spn_pass1`, `spn_pass2`, `spn_pass3`, labelled `"Pass 1 (cps)"`, `"Pass 2 (cps)"`,
   `"Pass 3 (cps)"`, with the tooltip
   `"Speed of each home pass, for both its search and its slow re-approach. Shared by all four axes. Used by the next homing run."`
   Each box's `valueChanged` calls `beamline.set_home_pass_speeds((p1, p2, p3))`. While any homing
   run is going the three boxes are disabled (the same moments `_lock_axes_for_auto_home` and
   `AxisControls.set_motion_controls_enabled` lock the other controls).
5. Every start path passes `speeds=self.beamline.home_pass_speeds()` into its worker:
   `AxisControls._start_homing`, `MotorTab._start_auto_home`, `MotorTab._start_auto_home_parallel`.
   Replace the hard-coded "225 → 112 → 58 cps" text in the `motor_tab.py` docstring and in any
   tooltip or standing note with wording that refers to the pass speed boxes.

## Acceptance criteria

- [x] Test `test_routine_uses_the_speeds_it_is_given` in `tests/test_auto_home.py`:
      `AxisHomeRoutine(_galil(), "A", speeds=(400, 150, 30)).home_passes()` succeeds and the
      `(speed, fine_speed)` pairs of the three `begin_home` calls are `[(400, 400), (150, 150), (30, 30)]`.
- [x] Test `test_together_uses_the_speeds_it_is_given` in `tests/test_auto_home.py` (class
      `TestMultiAxisHomeRoutine`): the same with `MultiAxisHomeRoutine(g, "ABCD", speeds=(400, 150, 30))`
      and `begin_home_multi`.
- [x] Test `test_edited_pass_speeds_reach_the_next_auto_home` in `tests/test_auto_home.py` (class
      `TestMotorTabAutoHome`, `tab` fixture): set `spn_pass1/2/3` to 300, 120, 40; patch
      `rbl.gui.motor_tab.AutoHomeAllWorker` with a `MagicMock` class and accept the confirmation as
      the class's existing tests do; start the sequential run; the mock class was constructed with
      `speeds=(300, 120, 40)`.
- [x] Test `test_edited_pass_speeds_reach_a_per_axis_home` in `tests/test_auto_home.py`: same edit,
      patch `rbl.gui.motor_tab.HomingWorker`, click axis A's Home button; constructed with
      `speeds=(300, 120, 40)`.
- [x] Test `test_home_pass_speeds_are_validated` in `tests/test_beamline.py`: a fresh `Beamline`
      returns `SC.HOME_PASS_SPEEDS_COUNTS_PER_SEC`; `set_home_pass_speeds((100, 0, 50))` and
      `set_home_pass_speeds((100, 50))` each raise `ValueError` and leave the speeds unchanged.

**What tests may fake:** the controller (`_galil()`), the worker classes when testing that the tab
hands them the speeds, `QMessageBox` confirmations, and `time.sleep`. `Beamline`, `MotorTab` and the
routines are real.

**Out of scope:** splitting a pass into a fast search and a slow re-approach (SP ≠ HV). The seek
speed and back-off distances. Saving the speeds. The edge-return test (tickets 129–131).

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments

### 2026-11-17
Implemented: pass-speed config + Beamline validation, `speeds=` through routines/workers, three QuietDoubleSpinBox on the tab (locked while homing). Local gate and CI green (PR #191).
