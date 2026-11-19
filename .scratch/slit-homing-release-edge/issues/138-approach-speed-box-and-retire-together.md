# 138: One Approach speed box; the Together homing and the HM code retired

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 136, 137

**Spec:** `.scratch/slit-homing-release-edge/spec.md`
**Binding:** `docs/adr/0009-slits-homed-on-the-release-edge.md`, `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Deletes tests:** tests/test_auto_home.py::test_together_sends_one_command_not_four_workers, tests/test_auto_home.py::test_together_locks_the_per_axis_controls, tests/test_auto_home.py::test_together_marks_each_axis_referenced_as_it_reports, tests/test_auto_home.py::test_together_releases_everything_when_it_finishes, tests/test_auto_home.py::test_the_two_all_axes_modes_refuse_to_overlap, tests/test_auto_home.py::test_emergency_stop_cancels_the_together_run, tests/test_auto_home.py::test_edited_pass_speeds_reach_the_next_together_home, tests/test_auto_home.py::test_pass_speed_boxes_disabled_while_homing, tests/test_auto_home.py::test_one_hm_and_one_bg_for_the_whole_set, tests/test_auto_home.py::test_zero_is_defined_for_every_axis_in_one_command, tests/test_auto_home.py::test_each_pass_turns_down_both_stages, tests/test_auto_home.py::test_together_uses_the_speeds_it_is_given, tests/test_auto_home.py::test_the_seek_starts_every_axis_with_one_jog, tests/test_auto_home.py::test_axes_already_on_the_limit_are_left_out_of_the_jog, tests/test_auto_home.py::test_nothing_is_homed_when_an_axis_fails_its_seek, tests/test_auto_home.py::test_together_sets_no_zero_when_one_axis_stops_off_home, tests/test_auto_home.py::test_together_fails_a_seek_on_the_reverse_limit, tests/test_auto_home.py::test_every_axis_is_reported_when_the_run_succeeds, tests/test_auto_home.py::test_it_puts_both_speeds_back_for_every_axis, tests/test_auto_home.py::test_cancel_before_the_passes_homes_nothing, tests/test_auto_home.py::test_together_backs_out_only_the_axes_on_home, tests/test_galil_protocol.py::test_begin_home_sequence, tests/test_galil_protocol.py::test_begin_home_sets_the_two_stages_separately, tests/test_galil_protocol.py::test_home_velocity_is_positional_per_axis, tests/test_galil_protocol.py::test_begin_home_jog_is_negative, tests/test_galil_protocol.py::test_begin_home_multi_is_one_hm_and_one_bg, tests/test_galil_protocol.py::test_begin_home_multi_defaults_hv_to_the_search_speed, tests/test_beamline.py::test_home_pass_speeds_are_validated

**Read first:** `src/rbl/gui/motor_tab.py` (`MotorTab._build_auto_home_box`, `_AUTO_HOME_STANDING`,
`_start_auto_home`, `_confirm_auto_home` text, `_on_pass_speeds_changed`,
`_update_pass_speeds_enabled`, `_lock_axes_for_auto_home`, `_start_auto_home_parallel`,
`_parallel_worker_running`, `_on_parallel_home_done`, `_any_homing_active`, `_on_edge_run_clicked`,
`_finish_edge_return`, every other use of `btn_auto_home_par` / `_parallel_worker`; the per-axis
Home and Seek + Home tooltips in `AxisControls.__init__`; the module docstring),
`src/rbl/hardware/galil_workers.py` (`MultiAxisHomeRoutine`, `MultiAxisHomeWorker`, the module
docstring), `src/rbl/hardware/galil_driver.py` (`begin_home`, `begin_home_multi`,
`set_home_velocity`, `set_home_velocity_multi`), `src/rbl/state/motor_control.py`
(`home_pass_speeds`, `set_home_pass_speeds`, `home_approach_speed` from ticket 136),
`src/rbl/config/hardware_config.py`, `tests/test_auto_home.py` (`tab` fixture,
`test_pass_speed_boxes_disabled_while_homing` for the lock-test shape).

## What to build

The Stepper Motors tab has one Approach speed box instead of three pass-speed boxes, one
auto-home button (one at a time), and no code left that homes with the controller's `HM`.

1. **Approach speed box.** In `_build_auto_home_box`, replace the three "Pass N (cps)" boxes with
   one `QLabel("Approach speed (cps)")` and `self.spn_approach_speed` (`QuietDoubleSpinBox`,
   decimals 0, range 1..`SC.HOME_APPROACH_SPEED_MAX_COUNTS_PER_SEC`, step 10, value from
   `self.beamline.home_approach_speed()`), tooltip
   `"Speed each slit is driven onto its home switch. Zero is found on the way back out, one count at a time, so this does not decide where zero lands. Shared by all four axes; used by the next homing run and the edge-return test."`
   Its `valueChanged` calls `self.beamline.set_home_approach_speed(int(round(value)))`. It is
   disabled whenever any homing run or the edge-return test is going, exactly where the pass boxes
   were disabled (rename `_update_pass_speeds_enabled` → `_update_approach_speed_enabled`;
   remove `_on_pass_speeds_changed`).
2. **Together retired.** Remove `btn_auto_home_par`, `_start_auto_home_parallel`,
   `_parallel_worker`, `_parallel_worker_running`, `_on_parallel_home_done` and every reference
   (E-stop, disconnect, edge-return lock, `_any_homing_active`, connection enabling). Remove
   `MultiAxisHomeRoutine` and `MultiAxisHomeWorker` from `galil_workers.py` and the import.
3. **HM code removed.** Remove `GalilController.begin_home`, `begin_home_multi`,
   `set_home_velocity`, `set_home_velocity_multi`; `SimGalil.set_home_velocity` in
   `tests/sim_slit.py`; constants `HOME_STOP_CODE`, `DEFAULT_HOME_VELOCITY_COUNTS_PER_SEC`,
   `HOME_PASS_SPEEDS_COUNTS_PER_SEC`, `HOME_PASS_SPEED_MAX_COUNTS_PER_SEC`; and
   `home_pass_speeds` / `set_home_pass_speeds` / `_home_pass_speeds` on `Beamline`. Keep the other
   `*_multi` driver helpers.
4. **Words.** `_AUTO_HOME_STANDING` becomes
   `"Homes all four slits, one at a time: each is driven onto its home switch, stepped back out to where the switch releases, twice, and zeroed there if the two agree within 4 counts. Start it and work on another tab."`
   The sequential button's tooltip and `_start_auto_home`'s confirmation text, the per-axis Home
   and Seek + Home tooltips, and the `motor_tab.py` and `galil_workers.py` module docstrings
   describe the two release-edge passes and the agreement check (ADR 0009) and no longer mention
   `HM`, three passes, pass speeds or Together.

Existing tests: `test_both_buttons_exist_and_follow_the_connection` is rewritten in place to check
the one remaining auto-home button; `test_speed_restores_are_vectors_too` in
`tests/test_galil_protocol.py` is rewritten in place to check only `set_speed_multi`. The
`tab` fixture in `tests/test_edge_return_tab.py` stops calling `set_home_pass_speeds`. Any other
test that fails only because it referenced a removed name is rewritten in place under its name.

## Acceptance criteria

- [ ] Test `test_approach_speed_box_edits_the_beamline` in `tests/test_auto_home.py`: on a new
      tab, `spn_approach_speed.value() == 58`; after `spn_approach_speed.setValue(40)`,
      `tab.beamline.home_approach_speed() == 40`; the spin box's maximum is 500.
- [ ] Test `test_approach_speed_box_is_locked_while_homing` in `tests/test_auto_home.py`: with
      `AutoHomeAllWorker`, `HomingWorker` and `EdgeReturnWorker` `start` patched to record and
      `isRunning` patched True, the box is disabled after clicking `btn_auto_home_seq`, after
      clicking axis A's `btn_home`, and after pressing `btn_edge_run`, and enabled again after each
      run's `done` is emitted.
- [ ] Test `test_the_together_path_is_gone` in `tests/test_auto_home.py`: a `MotorTab` has no
      attribute `btn_auto_home_par`; `rbl.hardware.galil_workers` has no `MultiAxisHomeRoutine` or
      `MultiAxisHomeWorker`; `GalilController` has no `begin_home`, `begin_home_multi` or
      `set_home_velocity`; `hardware_config` has no `HOME_STOP_CODE` or
      `HOME_PASS_SPEEDS_COUNTS_PER_SEC`.
- [ ] Test `test_no_homing_text_mentions_the_old_passes` in `tests/test_auto_home.py`: collecting
      `text()` and `toolTip()` from every `QLabel`, `QPushButton` and `QAbstractSpinBox` under a
      `MotorTab`, plus `_AUTO_HOME_STANDING`, none contains `"three-pass"`, `"HM "`, `"Together"`,
      `"Pass 1"` or `"pass speed"`.

**What tests may fake:** the worker classes' `start` / `isRunning` (as the deleted
`test_pass_speed_boxes_disabled_while_homing` did), the Galil (`MagicMock(connected=True)`), the
confirmation dialog. `MotorTab` and `Beamline` are real.

**Out of scope:** the switch map (139, 140). The status label (141). Renaming the edge-return CSV
column. CN, MT, BL/FL, directions.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
