# 145: The Stepper Motors tab's count boxes take whole numbers only

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/slit-homing-release-edge/spec.md` (Addendum 2026-11-19, second part)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/motor_tab.py`: `AxisControls.__init__` (where `spn_speed` and
`spn_target` are built, with `setDecimals(2)` and `setDecimals(3)`), `AxisControls._set_speed_unit_range`,
`AxisControls._set_target_unit_range`, `AxisControls._on_speed_unit_changed`,
`AxisControls._on_target_unit_changed`, `AxisControls._speed_in_cps` and `AxisControls._target_in_counts`
(both already round to a whole count). `tests/test_slit_cross_screen.py`: the `screens` fixture, and
`test_overview_move_lands_in_the_stepper_tabs_current_unit` and
`test_stepper_tab_move_below_home_is_refused` for how a test switches a panel to counts.

## What to build

A stepper only moves in whole counts. On each axis panel of the Stepper Motors tab, the Slit speed box
in **cps** shows two decimals and the Target box in **counts** shows three, so the operator can type
1234.5 counts or 1000.25 cps and the fraction is quietly rounded away when the value is used. After
this ticket, a box showing counts or cps shows and accepts whole numbers only, so a fraction cannot be
typed in the first place. The mm and mm/s units keep their decimals: a fraction of a millimetre is a
real distance, and it is rounded to the nearest count when it is used, as now.

1. `AxisControls._set_speed_unit_range`: call `self.spn_speed.setDecimals(0)` in the `"cps"` branch and
   `self.spn_speed.setDecimals(2)` in the `"mm/s"` branch, each before the `setRange` call in that
   branch.
2. `AxisControls._set_target_unit_range`: call `self.spn_target.setDecimals(0)` in the `"counts"` branch
   and `self.spn_target.setDecimals(3)` in the `"mm"` branch, each before the `setRange` call in that
   branch.
3. `AxisControls.__init__`: delete the `self.spn_speed.setDecimals(2)` and `self.spn_target.setDecimals(3)`
   lines. The constructor already calls `_set_speed_unit_range("cps")` and `_set_target_unit_range("mm")`,
   which now set the decimals.
4. Do not change the order in `_on_speed_unit_changed` or `_on_target_unit_changed`: both read the old
   value before changing the range, then write the converted value, so the new decimals apply to the
   converted value, not to the old one. `_speed_in_cps` and `_target_in_counts` keep their rounding.

The number of decimals is decided by the unit shown, in these two helpers only. This is a requirement,
not a preference: the tests switch units and check the decimals, and a decimals call made anywhere
else (for example on a unit-change signal) is missed when the constructor sets the starting unit.

## Acceptance criteria

All new tests go in `tests/test_slit_cross_screen.py` and use its `screens` fixture.

- [ ] Test `test_target_box_in_counts_takes_whole_counts_only`: on `motors.axes["A"]`, set
      `cbo_target_unit` to "counts". `spn_target.decimals() == 0`; after `spn_target.setValue(1234.4)`,
      `spn_target.value() == 1234` and `spn_target.lineEdit().text() == "1234"`. Then set the unit back
      to "mm": `spn_target.decimals() == 3`.
- [ ] Test `test_speed_box_in_cps_takes_whole_counts_only`: on `motors.axes["A"]` as built (unit "cps"),
      `spn_speed.decimals() == 0`; after `spn_speed.setValue(1234.6)`, `spn_speed.lineEdit().text() == "1235"`
      and `beamline.slit_speed_cps("A") == 1235`. Then set `cbo_speed_unit` to "mm/s":
      `spn_speed.decimals() == 2`.
- [ ] Test `test_switching_the_target_to_counts_shows_a_whole_number`: on `motors.axes["A"]` in "mm",
      `spn_target.setValue(6.0)`, then set the unit to "counts". The text contains no "." and
      `spn_target.value() == SC.mm_to_counts("A", 6.0)`. On the current code the text ends in ".000".
- [ ] (no test: covered by the gate) Every existing test in `tests/test_slit_cross_screen.py`,
      `tests/test_slit_control.py` and `tests/test_gui_hardware.py` passes unchanged. No test is deleted.

**What tests may fake:** the Galil, as the `screens` fixture already does (`_FakeGalil`). `Beamline`,
`MotorTab` and `OverviewTab` are real.

**Out of scope:** the Overview tab's slit target boxes (they are in mm); the Approach speed, edge-return
cycles and out-counts boxes (already whole numbers); snapping mm values to whole-count multiples; the
shared number box in `src/rbl/gui/widgets/inputs.py` (ticket 143); the Approach speed ceiling (144).

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
