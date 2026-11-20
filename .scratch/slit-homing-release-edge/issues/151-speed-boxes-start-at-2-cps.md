# 151: The slit and approach speeds start at 2 cps

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 147

**Spec:** `.scratch/slit-homing-release-edge/spec.md` (Addendum 2026-11-20)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/config/hardware_config.py` (`DEFAULT_SPEED_COUNTS_PER_SEC`,
`HOME_APPROACH_SPEED_MAX_COUNTS_PER_SEC`), `src/rbl/state/motor_control.py`
(`MotorControlMixin.set_slit_speed_cps`, `MotorControlMixin.set_home_approach_speed`),
`src/rbl/gui/motor_tab.py` (`_set_speed_unit_range`, and where `spn_approach_speed` gets its range),
`tests/test_beamline.py` (test_slit_speed_defaults_and_rejects_below_one,
test_home_approach_speed_defaults_and_is_validated), `tests/test_auto_home.py`
(`TestMotorTabAutoHome` and its `tab` fixture), `tests/test_gui_hardware.py` (`TestMotorTabUnits` and
its `motor` fixture, `panel.cbo_speed_unit`).

## What to build

On the bench a speed of 1 cps does nothing; 2 cps is the slowest that moves (the Galil's `SP` is
believed to take even values only, so `SP 1` becomes 0). The Slit speed box (in cps) and the Approach
speed box still accept 1. After this ticket both start at 2, and `Beamline` refuses anything lower.
This is not a floor against slow speeds: 2 to 58 cps stay available for comparisons.

1. `src/rbl/config/hardware_config.py`: add `SLIT_SPEED_MIN_COUNTS_PER_SEC = 2` just below
   `DEFAULT_SPEED_COUNTS_PER_SEC`, with a comment: the slowest speed that moves an axis (`SP 1` does
   not move it on the bench, 2026-11-19; bench ticket 142 confirms `SP 1` reads back 0); the minimum of
   every speed box and `Beamline` speed setter; not a floor against slow speeds.
2. `src/rbl/state/motor_control.py`: `set_slit_speed_cps` raises
   `ValueError(f"slit speed must be >= {SC.SLIT_SPEED_MIN_COUNTS_PER_SEC} count/s, got {cps}")` when
   `cps < SC.SLIT_SPEED_MIN_COUNTS_PER_SEC`. `set_home_approach_speed` accepts
   `SC.SLIT_SPEED_MIN_COUNTS_PER_SEC <= val <= SC.HOME_APPROACH_SPEED_MAX_COUNTS_PER_SEC`, and its
   message reads `f"approach speed must be in {SC.SLIT_SPEED_MIN_COUNTS_PER_SEC}..{SC.HOME_APPROACH_SPEED_MAX_COUNTS_PER_SEC} counts/s, got {cps!r}"`.
3. `src/rbl/gui/motor_tab.py`: in `_set_speed_unit_range`, the cps branch's range becomes
   `(float(SC.SLIT_SPEED_MIN_COUNTS_PER_SEC), 100_000.0)`; the mm/s branch is unchanged (its 0.01 mm/s
   minimum is already about 6 cps). `spn_approach_speed`'s range becomes
   `(float(SC.SLIT_SPEED_MIN_COUNTS_PER_SEC), float(SC.HOME_APPROACH_SPEED_MAX_COUNTS_PER_SEC))`.

## Acceptance criteria

- [ ] Test `test_slit_speed_rejects_below_two` in `tests/test_beamline.py` (same class and `beamline`
      fixture as test_slit_speed_defaults_and_rejects_below_one): `set_slit_speed_cps("C", 1)` raises
      `ValueError` matching `slit speed must be >= 2 count/s` and leaves `slit_speed_cps("C")` unchanged;
      `set_slit_speed_cps("C", 2)` then reads back 2.
- [ ] Test `test_approach_speed_box_starts_at_2` in `tests/test_auto_home.py` (in `TestMotorTabAutoHome`,
      using its `tab` fixture): `tab.spn_approach_speed.minimum() == 2`, and after
      `tab.spn_approach_speed.setValue(2)`, `tab.beamline.home_approach_speed() == 2`.
- [ ] Test `test_slit_speed_box_starts_at_2_cps` in `tests/test_gui_hardware.py` (in
      `TestMotorTabUnits`, using its `motor` fixture): on `motor.axes["A"]` in cps,
      `spn_speed.minimum() == 2`; after `cbo_speed_unit.setCurrentText("mm/s")`,
      `spn_speed.minimum() == 0.01`.
- [ ] (no test: rewrites an existing test in place, proven by the gate) In `tests/test_beamline.py`,
      test_home_approach_speed_defaults_and_is_validated is rewritten in place under the same name: the
      refused values become 1 and 2001 and the expected message regex becomes
      `approach speed must be in 2\.\.2000`; its default line (900 after ticket 147) is unchanged.
- [ ] (no test: CI's full suite) No test is deleted and every other test passes unchanged.

**What tests may fake:** the Galil, as the existing `beamline`, `tab` and `motor` fixtures do. `Beamline`
and `MotorTab` are real.

**Out of scope:** the "Min is N" note (150; this ticket only sets minimums); any floor above 2; odd
speeds (the controller rounding 59 down to 58 is accepted); the mm/s minimum; other speed boxes in the
app.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_beamline.py tests/test_auto_home.py tests/test_gui_hardware.py
```

pytest runs on the touched test files only; CI runs the full suite on the PR.

## Comments
