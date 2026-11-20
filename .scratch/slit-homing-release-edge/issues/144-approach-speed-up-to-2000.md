# 144: The Approach speed goes up to 2000 cps

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/slit-homing-release-edge/spec.md` (Addendum 2026-11-19)
**Binding:** `docs/adr/0009-slits-homed-on-the-release-edge.md`, `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/config/hardware_config.py` (`HOME_APPROACH_SPEED_COUNTS_PER_SEC`,
`HOME_APPROACH_SPEED_MAX_COUNTS_PER_SEC` and the comment above it, `HOME_CREEP_MAX_COUNTS`),
`src/rbl/state/motor_control.py` (`MotorControlMixin.set_home_approach_speed`),
`src/rbl/gui/motor_tab.py` (`MotorTab`, where `spn_approach_speed` takes its range from the constant),
`tests/test_beamline.py` (`test_home_approach_speed_defaults_and_is_validated`),
`tests/test_auto_home.py` (`TestMotorTabAutoHome`, its `tab` fixture, and
`test_approach_speed_box_edits_the_beamline`).

## What to build

The operator can set the Approach speed on the Stepper Motors tab anywhere from 1 to 2000 cps, so the
edge-return test can be run at 58, 900 (NEC's homing speed) and 1800 cps and the spreads compared. The
default stays 58 cps and is still not saved between runs.

1. `src/rbl/config/hardware_config.py`: `HOME_APPROACH_SPEED_MAX_COUNTS_PER_SEC = 2000`. Rewrite the
   comment above it to say: the approach does not decide where zero lands (ADR 0009); the ceiling is
   high enough to compare approach speeds up to and past NEC's 900 cps with the edge-return test; a
   faster approach drives deeper past the switch and makes each creep longer, and a creep past
   `HOME_CREEP_MAX_COUNTS` fails the pass with "check the sensor". `HOME_APPROACH_SPEED_COUNTS_PER_SEC`
   stays 58.
2. Nothing else in `src/` changes: `set_home_approach_speed` and the `spn_approach_speed` range both
   read the constant. If either has 500 written into it anywhere, replace it with the constant.

## Acceptance criteria

- [x] Test `test_home_approach_speed_accepts_up_to_2000` in `tests/test_beamline.py` (in the same class
      as the existing approach-speed test, using its `beamline` fixture): after
      `set_home_approach_speed(1800)` it reports 1800; after `set_home_approach_speed(2000)` it
      reports 2000. On the current code both raise `ValueError`.
- [x] Test `test_approach_speed_box_takes_1800` in `tests/test_auto_home.py` (in `TestMotorTabAutoHome`,
      using its `tab` fixture): `tab.spn_approach_speed.maximum() == 2000`;
      `tab.spn_approach_speed.setValue(1800)` leaves `tab.beamline.home_approach_speed() == 1800`.
- [x] (no test: rewrites an existing test in place, proven by the gate) In `tests/test_beamline.py`,
      test_home_approach_speed_defaults_and_is_validated is rewritten in place under the same name:
      the default is still 58, the out-of-range values become 0 and 2001, and the expected message
      becomes "approach speed must be in 1..2000" (regex `approach speed must be in 1\.\.2000`).
- [x] (no test: rewrites an existing test in place, proven by the gate) In `tests/test_auto_home.py`,
      test_approach_speed_box_edits_the_beamline is rewritten in place under the same name: it
      asserts `maximum() == 2000` where it asserted 500; everything else is unchanged.
- [x] (no test: covered by the gate) No test is deleted, and every other test that mentions an
      approach speed (including the `AxisHomeRoutine(..., approach_speed=500)` run in
      `tests/test_auto_home.py`) passes unchanged.

**What tests may fake:** what the existing `tab` and `beamline` fixtures already fake (the Galil). The
`Beamline` and the `MotorTab` are real.

**Out of scope:** changing the default approach speed; the seek speed (`HOME_SEEK_SPEED_COUNTS_PER_SEC`);
the switch-map step speed; slit speed; the number-box typing fix and the "Max is" note (143); ADR 0009
(it does not mention the ceiling).

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments

Summary (2026-11-19):
- Raised `HOME_APPROACH_SPEED_MAX_COUNTS_PER_SEC` from 500 to 2000 in `src/rbl/config/hardware_config.py` and updated the comment above it; `set_home_approach_speed` and `spn_approach_speed` already read the constant.
- Added `test_home_approach_speed_accepts_up_to_2000` and updated `test_home_approach_speed_defaults_and_is_validated` in `tests/test_beamline.py`.
- Added `test_approach_speed_box_takes_1800` and updated `test_approach_speed_box_edits_the_beamline` in `tests/test_auto_home.py`.
- Bench verification: three-speed edge-return comparison on axis A (58, 900, 1800 cps) tracked in ticket 142.
