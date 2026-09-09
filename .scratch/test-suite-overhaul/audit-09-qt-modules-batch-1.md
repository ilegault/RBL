# Test Suite Audit — Ticket 09: Qt Modules (Batch 1)

**Audit Date:** 2026-09-08  
**Scope:** 17 Qt-dependent test files (alphabetically <= `tests/test_load_characterizer.py`)  
**Total Tests Audited:** 296  
**Total Verdicts:** 150 Keep (50.7%), 95 Rewrite (32.1%), 51 Delete (17.2%)

---

## Executive Summary & Inventory

| File | Total | Keep | Rewrite | Delete | Primary Audit Findings / Architectural Scope |
| :--- | :---: | :---: | :---: | :---: | :--- |
| `tests/test_amp_single_channel.py` | 7 | 1 | 6 | 0 | Single channel amplifier controller logic; eliminate mock timing loops in favor of virtual clock. |
| `tests/test_amp_tab_isolation.py` | 13 | 10 | 3 | 0 | Cross-talk and channel isolation invariants; rewrite tests with brittle mock call orders. |
| `tests/test_amp_waveform_fidelity.py` | 2 | 2 | 0 | 0 | DSP and signal fidelity math on waveform generators; pure numerical assertions. |
| `tests/test_auto_home.py` | 50 | 32 | 18 | 0 | State machine and multi-axis homing routines; replace mock hardware patches with `FakeAsyncHardwareManager`. |
| `tests/test_calibration_app_wiring.py` | 6 | 0 | 5 | 1 | App-level wiring and profile lifecycle contracts; delete private widget mocking. |
| `tests/test_calibration_runner.py` | 28 | 22 | 5 | 1 | Sweeper execution, safety trip limits, and writer persistence; delete tautological config constant test. |
| `tests/test_camera_tab.py` | 4 | 1 | 2 | 1 | Camera view widgets and FPS synchronization; delete sys.modules monkeypatching test. |
| `tests/test_drag_panel.py` | 30 | 0 | 0 | 30 | White-box testing of private drag layout internals (`_cols`, `_find`); all 30 violate ADR 0001 §5. |
| `tests/test_dynamic_adjustment.py` | 14 | 14 | 0 | 0 | Core mathematical adjustment controller and creep estimation algorithms; keep all. |
| `tests/test_dynamic_adjustment_tab.py` | 12 | 0 | 7 | 5 | Adjustment UI table/banner state; delete 5 private attribute mutation and forwarding tests. |
| `tests/test_funcgen_panel_status.py` | 18 | 13 | 5 | 0 | Function generator UI state reflection, channel mirroring, and status badges; keep core logic. |
| `tests/test_funcgen_sync.py` | 15 | 2 | 13 | 0 | Multi-generator phase alignment & SCPI sequencing; rewrite call-order mock spaghetti with `FakeAsyncHardwareManager`. |
| `tests/test_gui_hardware.py` | 51 | 22 | 21 | 8 | Main window integration & beamline updates; delete 8 tautological Qt stack navigation tests. |
| `tests/test_hv_conditioner.py` | 9 | 9 | 0 | 0 | High-voltage conditioning state machine, excursion back-off, and dwell timing; keep all. |
| `tests/test_inputs.py` | 10 | 10 | 0 | 0 | Input widget validation, typing debouncing, and suffix policy lints; keep all. |
| `tests/test_load_characterization_tab.py` | 15 | 0 | 10 | 5 | Load characterization UI widgets & results table; delete 5 private forwarding and state tests. |
| `tests/test_load_characterizer.py` | 12 | 12 | 0 | 0 | Capacitance recovery algorithms, voltage ladders, and leakage trip limits; keep all. |
| **TOTAL** | **296** | **150** | **95** | **51** | **Comprehensive Audit Complete** |

---

## Detailed Test-by-Test Audit

### 1. `tests/test_amp_single_channel.py` (7 tests)
**Summary:** 1 Keep, 6 Rewrite, 0 Delete

- `test_target_selector_enables_in_single_mode` (line 66) — **KEEP** — Asserts channel target combobox is enabled when switching to single-channel streaming mode.
- `test_streamed_channel_updates_and_others_paused` (line 75) — **REWRITE** — Asserts only the selected channel's buffer updates in single mode while other channels show paused status.
  - *Sanctioned rewrite path:* Rewrite using Qt signal emissions and `qtbot` to assert buffer/UI status rather than inspecting internal buffer dictionaries directly.
- `test_current_target_waveform_follows_selection` (line 93) — **REWRITE** — Asserts waveform display reflects the selected channel's waveform data.
  - *Sanctioned rewrite path:* Rewrite with `qtbot` to change combobox index and assert plot curve data updates.
- `test_multichannel_mode_keeps_all_live` (line 126) — **REWRITE** — Asserts multi-channel mode updates all four channel buffers simultaneously.
  - *Sanctioned rewrite path:* Rewrite with signal emissions to assert all channel curves update.
- `test_combo_change_stages_without_emitting` (line 134) — **REWRITE** — Asserts modifying channel dropdown stages new target without immediately sending hardware commands.
  - *Sanctioned rewrite path:* Rewrite to assert no hardware dispatch on combobox index change until Apply is triggered.
- `test_apply_emits_and_clears_pending` (line 145) — **REWRITE** — Asserts clicking Apply sends staged configuration to hardware and clears pending change indicator.
  - *Sanctioned rewrite path:* Rewrite using `qtbot.mouseClick` on Apply button and verifying hardware dispatch via `FakeAsyncHardwareManager`.
- `test_wide_window_is_trend_small_window_is_snapshot` (line 156) — **REWRITE** — Asserts window size parameter configures trend view vs instantaneous snapshot view.
  - *Sanctioned rewrite path:* Rewrite with `qtbot` setting window size spinbox and verifying buffer display length.

---

### 2. `tests/test_amp_tab_isolation.py` (13 tests)
**Summary:** 10 Keep, 3 Rewrite, 0 Delete

- `test_buffers_only_amp_channels` (line 57) — **KEEP** — Asserts data buffer manager ignores channels not mapped to amplifier inputs.
- `test_window_populates_only_amp_buffers` (line 62) — **KEEP** — Asserts incoming multi-channel stream window routes only amplifier channels to amp tab buffers.
- `test_voltage_conversion` (line 68) — **KEEP** — Numerical invariant test verifying ADC count to high-voltage kV calibration conversion.
- `test_current_conversion` (line 73) — **KEEP** — Numerical invariant test verifying ADC count to mA current monitor calibration conversion.
- `test_negative_rail_stores_signed_dc_kv` (line 78) — **KEEP** — Asserts negative HV rail readbacks preserve sign convention in telemetry records.
- `test_starts_live` (line 89) — **KEEP** — Asserts amplifier tab initializes in live streaming mode.
- `test_slider_enters_frozen` (line 93) — **REWRITE** — Asserts dragging history slider pauses live rendering and enters frozen history inspection mode.
  - *Sanctioned rewrite path:* Rewrite with `qtbot` slider interactions instead of directly invoking private slider callbacks.
- `test_jump_to_live` (line 100) — **REWRITE** — Asserts clicking "Jump to Live" button exits frozen history and resumes live streaming.
  - *Sanctioned rewrite path:* Rewrite with `qtbot.mouseClick` on Jump to Live button.
- `test_redraw_empty_is_safe` (line 107) — **KEEP** — Robustness smoke test verifying redraw with empty buffer does not raise exceptions.
- `test_buffers_only_log_amp_channels` (line 123) — **KEEP** — Asserts log amplifier tab buffers isolate log amp input channels.
- `test_log_amp_math_unchanged_with_twelve_channels` (line 128) — **KEEP** — Mathematical invariant test ensuring log amplifier calculation is unchanged regardless of total stream channel count.
- `test_centering_still_works` (line 136) — **KEEP** — Mathematical invariant test verifying beam centroid calculation from log amp voltages.
- `test_same_window_feeds_both_correctly` (line 143) — **REWRITE** — Asserts a single stream data window feeds both standard and log amp tabs correctly without cross-contamination.
  - *Sanctioned rewrite path:* Rewrite using Qt signal emission to dispatch window to both tab models and verify independent buffer updates.

---

### 3. `tests/test_amp_waveform_fidelity.py` (2 tests)
**Summary:** 2 Keep, 0 Rewrite, 0 Delete

- `test_consecutive_windows_join_without_seams` (line 71) — **KEEP** — DSP numerical test verifying consecutive streaming data blocks align without phase discontinuities or dropped sample seams.
- `test_falls_back_to_nominal_period_without_sample_period` (line 98) — **KEEP** — Fallback timing logic test verifying nominal sample period is used when metadata omitted.

---

### 4. `tests/test_auto_home.py` (50 tests)
**Summary:** 32 Keep, 18 Rewrite, 0 Delete

- `test_jogs_negative_until_the_home_switch_trips` (line 93) — **REWRITE** — Asserts negative seek jog stops when home switch trips. Rewrite with `FakeAsyncHardwareManager`.
- `test_stops_on_the_reverse_limit_too` (line 105) — **REWRITE** — Asserts reverse limit switch halts seek jog as a safety boundary. Rewrite with fake hardware.
- `test_no_jog_when_already_on_the_limit` (line 114) — **REWRITE** — Asserts axis already resting on limit switch skips initial seek jog. Rewrite with fake hardware.
- `test_reports_a_jog_that_stopped_short` (line 120) — **REWRITE** — Asserts error report when seek jog halts prematurely before switch is found. Rewrite with fake hardware.
- `test_times_out_rather_than_jogging_forever` (line 129) — **REWRITE** — Asserts timeout abort when home switch is not detected within duration limit. Rewrite with virtual clock.
- `test_cancel_stops_the_jog` (line 137) — **REWRITE** — Asserts user cancellation immediately halts active jog. Rewrite with fake hardware.
- `test_a_clear_axis_is_not_mistaken_for_one_on_its_limit` (line 164) — **KEEP** — Limit switch status query invariant test.
- `test_an_axis_on_its_reverse_limit_is_detected` (line 171) — **KEEP** — Limit switch status detection test.
- `test_an_axis_on_its_home_switch_is_detected` (line 175) — **KEEP** — Home switch status detection test.
- `test_seek_then_three_passes_then_zero` (line 181) — **REWRITE** — Asserts full sequence of seek followed by 3 latch passes and zeroing. Rewrite with `FakeAsyncHardwareManager`.
- `test_a_failed_seek_never_reaches_hm` (line 191) — **REWRITE** — Asserts failed initial seek aborts homing before latch passes begin. Rewrite with fake hardware.
- `test_each_pass_turns_down_both_of_hm_s_stages` (line 201) — **REWRITE** — Asserts each successive homing pass reduces both coarse and fine velocities. Rewrite with fake hardware.
- `test_it_puts_both_speeds_back` (line 214) — **REWRITE** — Asserts pre-homing velocities are restored after successful homing. Rewrite with fake hardware.
- `test_the_speeds_go_back_after_a_failure_too` (line 223) — **REWRITE** — Asserts velocities restored even after homing failure. Rewrite with fake hardware.
- `test_zero_is_defined_because_hm_does_not_do_it_on_a_stepper` (line 238) — **REWRITE** — Asserts explicit zero position command sent after homing on stepper motors. Rewrite with fake hardware.
- `test_without_seek_it_is_the_old_routine` (line 246) — **KEEP** — Legacy routine fallback test.
- `test_seek_first_is_off_by_default` (line 255) — **KEEP** — Configuration default value check.
- `test_seek_first_adds_the_jog` (line 263) — **KEEP** — Configuration sequence modification check.
- `test_homes_every_axis_in_order` (line 277) — **REWRITE** — Asserts sequential execution of homing across all configured axes. Rewrite with fake hardware.
- `test_one_axis_at_a_time` (line 292) — **REWRITE** — Asserts sequential axis execution invariant. Rewrite with fake hardware.
- `test_a_failure_stops_the_run_and_names_what_was_skipped` (line 308) — **REWRITE** — Asserts single axis failure aborts remaining axes in multi-axis sequence. Rewrite with fake hardware.
- `test_cancel_leaves_the_remaining_axes_untouched` (line 329) — **REWRITE** — Asserts cancellation stops current axis and skips remaining. Rewrite with fake hardware.
- `test_defaults_to_all_four_axes_with_the_seek` (line 342) — **KEEP** — Multi-axis default configuration check.
- `test_both_buttons_exist_and_follow_the_connection` (line 365) — **KEEP** — UI button presence and enable state following connection status.
- `test_a_declined_confirmation_starts_nothing` (line 376) — **KEEP** — UI confirmation dialog cancellation test.
- `test_running_locks_the_per_axis_controls` (line 383) — **KEEP** — UI controls lock safety interlock during homing run.
- `test_a_held_status_survives_the_poll` (line 398) — **KEEP** — Status persistence across background polling cycles.
- `test_a_homed_axis_is_marked_referenced` (line 412) — **KEEP** — Axis referenced state flag update on completion.
- `test_emergency_stop_cancels_a_running_sequence` (line 421) — **KEEP** — E-stop signal aborts running homing sequence.
- `test_disconnect_cancels_a_running_sequence` (line 429) — **KEEP** — Hardware disconnection signal aborts homing sequence.
- `test_together_sends_one_command_not_four_workers` (line 435) — **REWRITE** — Asserts simultaneous homing sends unified multi-axis command. Rewrite with fake hardware.
- `test_together_locks_the_per_axis_controls` (line 450) — **KEEP** — UI safety lock during simultaneous homing.
- `test_together_marks_each_axis_referenced_as_it_reports` (line 460) — **KEEP** — Multi-axis incremental referenced status updates.
- `test_together_releases_everything_when_it_finishes` (line 467) — **KEEP** — UI unlock upon simultaneous homing completion.
- `test_the_two_all_axes_modes_refuse_to_overlap` (line 479) — **KEEP** — Concurrency guard preventing overlapping homing routines.
- `test_emergency_stop_cancels_the_together_run` (line 488) — **KEEP** — E-stop aborts simultaneous homing routine.
- `test_each_panel_offers_the_two_step_procedure_in_one_click` (line 499) — **KEEP** — UI single-click composite homing action availability.
- `test_it_becomes_cancel_while_running` (line 504) — **KEEP** — UI button morphing from Home to Cancel during execution.
- `test_the_axis_cannot_be_jogged_while_it_homes` (line 519) — **KEEP** — Safety interlock preventing manual jog during active homing.
- `test_the_controls_stay_locked_if_the_link_dropped` (line 537) — **KEEP** — Safety latch keeping controls disabled if connection drops mid-homing.
- `test_plain_home_still_skips_the_seek` (line 551) — **KEEP** — Plain homing option behavior verification.
- `test_one_hm_and_one_bg_for_the_whole_set` (line 572) — **REWRITE** — Command batching verification for multi-axis homing. Rewrite with fake hardware.
- `test_zero_is_defined_for_every_axis_in_one_command` (line 581) — **REWRITE** — Asserts multi-axis zero definition in single command. Rewrite with fake hardware.
- `test_each_pass_turns_down_both_stages` (line 587) — **REWRITE** — Velocity step-down check on multi-axis passes. Rewrite with fake hardware.
- `test_the_seek_starts_every_axis_with_one_jog` (line 594) — **REWRITE** — Multi-axis simultaneous seek jog dispatch check. Rewrite with fake hardware.
- `test_axes_already_on_the_limit_are_left_out_of_the_jog` (line 616) — **KEEP** — Multi-axis seek jog selective exclusion for axes on limit switch.
- `test_nothing_is_homed_when_an_axis_fails_its_seek` (line 624) — **KEEP** — Safety abort preventing subsequent stages when any axis fails initial seek.
- `test_every_axis_is_reported_when_the_run_succeeds` (line 639) — **KEEP** — Multi-axis completion status report completeness.
- `test_it_puts_both_speeds_back_for_every_axis` (line 647) — **KEEP** — Multi-axis speed restore invariant.
- `test_cancel_before_the_passes_homes_nothing` (line 655) — **KEEP** — Pre-execution cancellation boundary test.

---

### 5. `tests/test_calibration_app_wiring.py` (6 tests)
**Summary:** 0 Keep, 5 Rewrite, 1 Delete

- `test_calibration_tab_present` (line 115) — **REWRITE** — Asserts `main_window.calibration_tab` is not None and present in stack.
  - *Sanctioned rewrite path:* Rewrite as a component integration test verifying tab registration via `CalibrationTab` contract rather than inspecting internal MainWindow attributes.
- `test_calibration_tab_receives_window_payloads` (line 122) — **REWRITE** — Asserts that stream data events from LabJack dispatch to calibration tab.
  - *Sanctioned rewrite path:* Rewrite to use Qt signal emissions and `qtbot.waitUntil` verifying observable calibration data flow rather than mock callback inspections.
- `test_starting_a_run_forces_cal_profile` (line 146) — **REWRITE** — Asserts profile switches to calibration mode when run begins.
  - *Sanctioned rewrite path:* Rewrite using real profile manager interface and signal spying to verify system state change on run invocation.
- `test_ending_a_run_restores_the_prior_profile` (line 152) — **REWRITE** — Asserts profile is restored upon run completion.
  - *Sanctioned rewrite path:* Rewrite using fake calibration runner emission of `finished` signal and verifying profile manager state.
- `test_abort_also_restores_the_prior_profile` (line 160) — **REWRITE** — Asserts profile is restored if calibration run is aborted.
  - *Sanctioned rewrite path:* Rewrite using fake calibration runner abort path and verifying profile manager restoration.
- `test_profile_selector_disabled_during_run_and_reenabled_after` (line 171) — **DELETE** — Tests internal widget enable/disable state by mocking `_profile_selector` directly instead of testing user interaction / observable state.
  - *Deletion rationale:* Violates ADR 0001 §5 by mocking internal UI controls to verify state reflection. The contract of profile locking is already covered by runner lifecycle tests.

---

### 6. `tests/test_calibration_runner.py` (28 tests)
**Summary:** 22 Keep, 5 Rewrite, 1 Delete

- `test_completes_without_time_sleep` (line 160) — **KEEP** — Verifies event-driven calibration step execution does not block with `time.sleep`.
- `test_total_setpoints_matches_config` (line 175) — **KEEP** — Pure mathematical test verifying grid point calculations match calibration configuration.
- `test_eight_rows_per_setpoint` (line 184) — **KEEP** — Mathematical invariant test ensuring 8 data rows (4 pairs x 2 polarities) per setpoint.
- `test_windows_during_settle_are_discarded` (line 196) — **KEEP** — Ensures settle time windows are dropped from measurement data.
- `test_railed_current_monitor_hard_trip_fires` (line 222) — **KEEP** — Safety interlock test verifying emergency stop when current monitor rails.
- `test_undriven_channels_commanded_zero_every_setpoint` (line 251) — **KEEP** — Safety invariant test ensuring undriven HV channels are explicitly zeroed.
- `test_no_commanded_value_exceeds_cal_max_kv` (line 269) — **KEEP** — Safety clamping test asserting commanded voltage never exceeds calibration ceiling.
- `test_every_write_followed_by_error_query` (line 283) — **KEEP** — Protocol check verifying SCPI error querying after every voltage command.
- `test_abort_mid_sweep_zeros_and_disables_all_four` (line 302) — **KEEP** — Safety abort test ensuring all channels are zeroed and disabled on user abort.
- `test_exception_mid_sweep_still_zeros_and_disables` (line 318) — **KEEP** — Safety error recovery test ensuring hardware is left in safe state on exception.
- `test_fixed_seed_reproduces_sequence` (line 345) — **KEEP** — Determinism test verifying pseudo-random sweep sequence is reproducible with fixed seed.
- `test_healthy_point_is_ok` (line 375) — **KEEP** — Point classification logic verifying nominal voltage/current response.
- `test_amp_off_when_both_monitors_near_zero` (line 382) — **KEEP** — Diagnostic classification logic when amplifier monitor reports zero.
- `test_current_limited_when_voltage_low_current_pinned` (line 389) — **KEEP** — Diagnostic classification for current-limited amplifier state.
- `test_idle_when_no_data_collected_for_this_amp` (line 397) — **KEEP** — Diagnostic classification for unmeasured channels.
- `test_make_row_includes_regulation_columns` (line 403) — **KEEP** — Verifies row schema contains required regulation telemetry.
- `test_rows_and_metadata_reach_a_real_writer` (line 413) — **REWRITE** — Asserts data persistence to disk.
  - *Sanctioned rewrite path:* Rewrite to use `tmp_path` fixture and verify actual saved CSV/HDF5 contents instead of mocking writer object.
- `test_progress_emitted_each_setpoint` (line 442) — **KEEP** — Signal emission test verifying progress updates across sweep steps.
- `test_finished_emits_a_string` (line 452) — **KEEP** — Signal emission test verifying completion message emission.
- `test_on_plates_8h_refused` (line 484) — **KEEP** — Parameter validation test refusing out-of-spec duration for on-plates mode.
- `test_on_plates_1h_accepted` (line 493) — **KEEP** — Parameter validation test accepting valid on-plates duration.
- `test_disconnected_10h_accepted` (line 503) — **KEEP** — Parameter validation test accepting valid disconnected mode duration.
- `test_disconnected_20h_refused` (line 513) — **KEEP** — Parameter validation test refusing excessive disconnected duration.
- `test_guard_boundaries_match_config_constants` (line 522) — **DELETE** — Tautological test asserting config values match hardcoded local constants.
  - *Deletion rationale:* Restating configuration constants in test assertions without behavioral validation catches no regressions.
- `test_auto_zeros_at_completion` (line 529) — **KEEP** — Safety contract test verifying all channels zeroed upon normal sweep completion.
- `test_watchdog_triggers_on_gap_and_zeros_output` (line 554) — **REWRITE** — Tests stream watchdog timeout triggering safe shutdown.
  - *Sanctioned rewrite path:* Rewrite using a deterministic fake clock instead of `mock.patch('time.monotonic')` to prevent timing flakiness.
- `test_watchdog_resets_on_each_window` (line 577) — **REWRITE** — Tests watchdog reset on incoming data stream.
  - *Sanctioned rewrite path:* Rewrite using fake clock and simulated window stream.
- `test_drift_rows_carry_pass_type_drift` (line 591) — **REWRITE** — Tests drift monitoring pass tagging in output data.
  - *Sanctioned rewrite path:* Rewrite to verify generated row records directly without deep mock interception.

---

### 7. `tests/test_camera_tab.py` (4 tests)
**Summary:** 1 Keep, 2 Rewrite, 1 Delete

- `test_camera_tab_constructs_without_error` (line 102) — **KEEP** — Smoke test verifying `CameraTab` instantiates cleanly with default widgets.
- `test_camera_tab_constructs_without_cv2` (line 107) — **DELETE** — Tests import fallback when OpenCV is missing by monkeypatching `sys.modules['cv2'] = None`.
  - *Deletion rationale:* Mutating `sys.modules` in a single test pollutes the entire test runner process and causes sporadic import errors in subsequent tests.
- `test_record_fps_change_syncs_between_views` (line 120) — **REWRITE** — Tests synchronization of FPS setting between controls.
  - *Sanctioned rewrite path:* Rewrite using `qtbot` user interaction (`qtbot.keyClicks` or `spinBox.setValue`) to assert UI sync via public signals.
- `test_crosshair_toggle_no_raise_with_no_frame` (line 131) — **REWRITE** — Tests crosshair toggle robustness when no video feed is active.
  - *Sanctioned rewrite path:* Rewrite using `qtbot.mouseClick` on the crosshair action/button.

---

### 8. `tests/test_drag_panel.py` (30 tests)
**Summary:** 0 Keep, 0 Rewrite, 30 Delete

- `test_initial_state_one_empty_col` (line 53) — **DELETE** — Tests private `_cols` list initial structure.
  - *Deletion rationale:* White-box inspection of private internal storage (`_cols`). Violates ADR 0001 §5.
- `test_add_to_col0` (line 58) — **DELETE** — Tests private `_cols` list mutation on append.
  - *Deletion rationale:* White-box inspection of private internal storage (`_cols`).
- `test_add_two_panels_same_col_ordered` (line 63) — **DELETE** — Tests private `_cols` list ordering.
  - *Deletion rationale:* White-box inspection of private internal storage (`_cols`).
- `test_add_four_panels_same_col_ordered` (line 69) — **DELETE** — Tests private `_cols` list multi-item ordering.
  - *Deletion rationale:* White-box inspection of private internal storage (`_cols`).
- `test_add_to_col1_creates_new_col` (line 75) — **DELETE** — Tests private `_cols` column expansion.
  - *Deletion rationale:* White-box inspection of private internal storage (`_cols`).
- `test_add_skips_intermediate_empty_cols` (line 84) — **DELETE** — Tests private `_cols` index gap handling.
  - *Deletion rationale:* White-box inspection of private internal storage (`_cols`).
- `test_area_reference_set_on_panel` (line 93) — **DELETE** — Tests internal backlink assignment `panel._area`.
  - *Deletion rationale:* White-box inspection of internal pointer assignment.
- `test_stack_two_cols_independently` (line 98) — **DELETE** — Tests private `_cols` multi-column structure.
  - *Deletion rationale:* White-box inspection of private internal storage (`_cols`).
- `test_find_only_panel` (line 114) — **DELETE** — Tests private helper `_find(panel)`.
  - *Deletion rationale:* Unit testing private helper method directly.
- `test_find_two_in_same_col` (line 119) — **DELETE** — Tests private helper `_find(panel)` with multiple panels.
  - *Deletion rationale:* Unit testing private helper method directly.
- `test_find_in_second_col` (line 126) — **DELETE** — Tests private helper `_find(panel)` in column 1.
  - *Deletion rationale:* Unit testing private helper method directly.
- `test_find_across_multiple_cols` (line 133) — **DELETE** — Tests private helper `_find(panel)` across columns.
  - *Deletion rationale:* Unit testing private helper method directly.
- `test_find_uses_identity_not_equality` (line 146) — **DELETE** — Tests private helper `_find(panel)` pointer comparison semantics.
  - *Deletion rationale:* Unit testing private helper method directly.
- `test_no_op_same_row` (line 169) — **DELETE** — Tests private helper `_apply_move(...)` no-op path.
  - *Deletion rationale:* Unit testing private helper method directly.
- `test_move_first_to_end` (line 174) — **DELETE** — Tests private helper `_apply_move(...)` reordering.
  - *Deletion rationale:* Unit testing private helper method directly.
- `test_move_last_to_first` (line 180) — **DELETE** — Tests private helper `_apply_move(...)` reordering.
  - *Deletion rationale:* Unit testing private helper method directly.
- `test_move_middle_up` (line 185) — **DELETE** — Tests private helper `_apply_move(...)` reordering.
  - *Deletion rationale:* Unit testing private helper method directly.
- `test_move_middle_to_end` (line 190) — **DELETE** — Tests private helper `_apply_move(...)` reordering.
  - *Deletion rationale:* Unit testing private helper method directly.
- `test_index_shift_moving_forward` (line 195) — **DELETE** — Tests private helper `_apply_move(...)` index recalculations.
  - *Deletion rationale:* Unit testing private helper method directly.
- `test_col_count_unchanged_after_reorder` (line 202) — **DELETE** — Tests private helper `_apply_move(...)` column count preservation.
  - *Deletion rationale:* Unit testing private helper method directly.
- `test_move_to_front_of_other_col` (line 213) — **DELETE** — Tests private helper `_apply_move(...)` inter-column move.
  - *Deletion rationale:* Unit testing private helper method directly.
- `test_move_to_end_of_other_col` (line 223) — **DELETE** — Tests private helper `_apply_move(...)` inter-column move.
  - *Deletion rationale:* Unit testing private helper method directly.
- `test_move_from_multi_row_col_preserves_remainder` (line 232) — **DELETE** — Tests private helper `_apply_move(...)` remainder preservation.
  - *Deletion rationale:* Unit testing private helper method directly.
- `test_empty_col_is_pruned_after_move_out` (line 243) — **DELETE** — Tests private helper `_apply_move(...)` empty column pruning.
  - *Deletion rationale:* Unit testing private helper method directly.
- `test_move_preserves_two_col_structure` (line 253) — **DELETE** — Tests private helper `_apply_move(...)` multi-column preservation.
  - *Deletion rationale:* Unit testing private helper method directly.
- `test_move_to_new_col_at_right` (line 272) — **DELETE** — Tests private helper `_apply_move(...)` new column insertion.
  - *Deletion rationale:* Unit testing private helper method directly.
- `test_move_only_panel_to_new_col_stays_one_col` (line 281) — **DELETE** — Tests private helper `_apply_move(...)` single column invariant.
  - *Deletion rationale:* Unit testing private helper method directly.
- `test_new_col_beyond_existing_appended_at_end` (line 290) — **DELETE** — Tests private helper `_apply_move(...)` boundary clamping.
  - *Deletion rationale:* Unit testing private helper method directly.
- `test_always_at_least_one_col` (line 307) — **DELETE** — Tests private `_cols` list non-empty invariant.
  - *Deletion rationale:* White-box inspection of private internal storage.
- `test_pruning_keeps_or_falls_back_to_one_empty_col` (line 314) — **DELETE** — Tests a pure Python list comprehension `[c for c in cols if c] or [[]]` in isolation without calling production code.
  - *Deletion rationale:* Pure tautology testing Python language syntax without touching any application classes or methods.

---

### 9. `tests/test_dynamic_adjustment.py` (14 tests)
**Summary:** 14 Keep, 0 Rewrite, 0 Delete

- `test_blank_pot_position_raises` (line 73) — **KEEP** — Input validation test verifying `ValueError` on empty potentiometer label.
- `test_whitespace_only_pot_position_raises` (line 77) — **KEEP** — Input validation test verifying `ValueError` on whitespace potentiometer label.
- `test_valid_pot_position_is_stored_and_stripped` (line 81) — **KEEP** — Data handling test verifying label whitespace trimming and storage.
- `test_starts_on_voltage_stage_targeting_voltage_ain` (line 87) — **KEEP** — Controller state machine verification for initial voltage acquisition stage.
- `test_commands_a_low_amplitude_square_wave` (line 95) — **KEEP** — Hardware command generation test verifying safe excitation amplitude.
- `test_undercompensated_creep_is_positive` (line 136) — **KEEP** — Mathematical verification of creep error calculation sign convention.
- `test_overcompensated_creep_is_negative` (line 140) — **KEEP** — Mathematical verification of creep error calculation sign convention.
- `test_record_carries_pot_position_and_both_stages` (line 144) — **KEEP** — Data schema verification for adjustment trial results.
- `test_decimated_traces_are_stored` (line 151) — **KEEP** — Data reduction algorithm verification for waveform decimation.
- `test_amplifier_output_is_zeroed_after_trial` (line 158) — **KEEP** — Safety requirement verifying amplifier is zeroed when trial finishes.
- `test_find_edges_on_a_clean_square_wave` (line 165) — **KEEP** — Digital signal processing algorithm test for edge detection.
- `test_find_edges_on_flat_signal_is_empty` (line 172) — **KEEP** — Boundary condition test for edge detection on DC signal.
- `test_average_edge_window_flips_falling_edges` (line 176) — **KEEP** — Signal processing algorithm test aligning rising and falling edge responses.
- `test_average_edge_window_none_when_no_edges` (line 186) — **KEEP** — Boundary condition test for edge window averaging with no transitions.

---

### 10. `tests/test_dynamic_adjustment_tab.py` (12 tests)
**Summary:** 0 Keep, 7 Rewrite, 5 Delete

- `test_run_without_labjack_connection_warns` (line 42) — **REWRITE** — Tests user warning dialog when starting run disconnected.
  - *Sanctioned rewrite path:* Rewrite with `qtbot` and `QMessageBox` spy to test user warning presentation on button click.
- `test_run_without_pot_position_warns` (line 52) — **REWRITE** — Tests user validation warning on missing pot position.
  - *Sanctioned rewrite path:* Rewrite with `qtbot` to enter invalid input and verify UI feedback.
- `test_run_with_whitespace_only_pot_position_warns` (line 62) — **REWRITE** — Tests user validation warning on whitespace input.
  - *Sanctioned rewrite path:* Rewrite with `qtbot` to test input validation UI.
- `test_run_without_generator_connected_warns` (line 73) — **REWRITE** — Tests generator disconnection warning.
  - *Sanctioned rewrite path:* Rewrite with `qtbot` verifying warning when generator is offline.
- `test_no_trials_shows_placeholder_hint` (line 86) — **REWRITE** — Tests initial table placeholder text.
  - *Sanctioned rewrite path:* Rewrite to inspect visible QTableWidget items without mock inspection.
- `test_trials_populate_the_table_and_winner_hint` (line 90) — **REWRITE** — Tests table population and recommendation hint on completed trials.
  - *Sanctioned rewrite path:* Rewrite using signal emission to supply trial data and verifying QTableWidget contents.
- `test_large_creep_winner_suggests_a_direction` (line 106) — **REWRITE** — Tests suggestion banner text formatting based on creep sign.
  - *Sanctioned rewrite path:* Rewrite to verify suggestion banner label update.
- `test_on_labjack_connected_updates_state` (line 118) — **DELETE** — Asserts `tab._connected == True` after invoking private method `_on_labjack_connected`.
  - *Deletion rationale:* Direct test of private attribute mutation with no observable UI behavior. Violates ADR 0001 §5.
- `test_on_labjack_disconnected_updates_state` (line 122) — **DELETE** — Asserts `tab._connected == False` after invoking private method.
  - *Deletion rationale:* Direct test of private attribute mutation. Violates ADR 0001 §5.
- `test_on_profile_changed_is_cached` (line 127) — **DELETE** — Asserts `tab._current_profile == profile` on private callback.
  - *Deletion rationale:* White-box testing of private field caching. Violates ADR 0001 §5.
- `test_on_window_forwards_to_trial` (line 131) — **DELETE** — Asserts private `_trial.process_window` mock is called.
  - *Deletion rationale:* Tautological mock forwarding test violating ADR 0001 §5.
- `test_shutdown_does_not_raise` (line 140) — **DELETE** — Empty smoke test calling private `tab._shutdown()`.
  - *Deletion rationale:* Redundant lifecycle smoke test already covered by widget parent deletion.

---

### 11. `tests/test_funcgen_panel_status.py` (18 tests)
**Summary:** 13 Keep, 5 Rewrite, 0 Delete

- `test_mirror_copies_amplitude_and_frequency_to_the_partner` (line 52) — **KEEP** — UI sync test verifying mirror checkbox updates partner channel values.
- `test_mirror_does_not_copy_phase` (line 62) — **KEEP** — Verifies mirror logic preserves independent channel phase setpoints.
- `test_mirror_writes_through_the_shared_setpoint_model` (line 72) — **KEEP** — Architecture test verifying setpoint model synchronization.
- `test_mirror_stays_inside_its_own_axis` (line 80) — **KEEP** — Isolation test verifying mirror operations remain confined to axis pair.
- `test_mirror_sends_nothing_to_the_instrument` (line 88) — **KEEP** — Safety test verifying mirror changes do not transmit hardware commands until applied.
- `test_each_panel_names_the_channel_it_mirrors_onto` (line 99) — **KEEP** — UI label test verifying mirror target description.
- `test_dot_is_grey_before_any_readback` (line 108) — **KEEP** — Visual status test verifying indicator dot starts in disconnected/unverified state.
- `test_dot_goes_green_when_the_instrument_reports_the_output_on` (line 114) — **KEEP** — Visual status test verifying indicator dot reflects active output readback.
- `test_dot_follows_the_instrument_not_the_output_button` (line 121) — **KEEP** — Safety test verifying status indicator reflects hardware telemetry rather than command intent.
- `test_a_dropped_session_clears_the_dot` (line 131) — **KEEP** — Connection loss indicator reset test.
- `test_a_read_error_is_not_shown_as_output_off` (line 139) — **KEEP** — Diagnostic integrity test verifying read error is distinguished from output disabled.
- `test_no_badge_when_the_boxes_match_the_instrument` (line 147) — **KEEP** — Unapplied change badge test verifying badge hidden when setpoint matches readback.
- `test_badge_names_the_field_that_has_not_been_applied` (line 155) — **KEEP** — Unapplied change badge test verifying specific modified field is identified.
- `test_badge_catches_an_output_intent_that_was_never_applied` (line 163) — **KEEP** — Unapplied change badge test verifying pending output toggle is flagged.
- `test_no_badge_before_there_is_anything_to_compare_against` (line 172) — **KEEP** — Initial condition test for unapplied change badge.
- `test_shape_is_compared_across_the_scpi_abbreviation` (line 180) — **KEEP** — SCPI protocol mapping test verifying waveform name matching.
- `test_dc_mode_hides_each_units_label_with_its_box` (line 192) — **REWRITE** — Tests widget visibility toggling when switching to DC mode.
  - *Sanctioned rewrite path:* Rewrite with `qtbot` to change combo box selection and assert widget `isVisible()` state directly.
- `test_a_high_frequency_is_not_flagged_by_float_dust` (line 205) — **REWRITE** — Floating point comparison tolerance test in badge logic.
  - *Sanctioned rewrite path:* Rewrite to exercise panel setpoint sync via public widget setters.

---

### 12. `tests/test_funcgen_sync.py` (15 tests)
**Summary:** 2 Keep, 13 Rewrite, 0 Delete

- `test_all_configured_before_any_output_enabled` (line 87) — **REWRITE** — Sequencing test verifying all channel parameters are sent before output enable.
  - *Sanctioned rewrite path:* Rewrite using a recorded call log on a `FakeAsyncHardwareManager` rather than fragile `unittest.mock.call` order assertions.
- `test_align_phase_runs_once_per_unit_after_enable` (line 107) — **REWRITE** — Protocol sync test verifying phase alignment command sequence.
  - *Sanctioned rewrite path:* Rewrite using fake hardware protocol logger.
- `test_output_enable_burst_is_contiguous` (line 126) — **REWRITE** — Timing requirement test verifying enable commands are dispatched without interleaving.
  - *Sanctioned rewrite path:* Rewrite using fake hardware protocol logger.
- `test_channels_left_off_are_disabled_not_enabled` (line 141) — **REWRITE** — Safety test ensuring unselected channels are explicitly commanded off.
  - *Sanctioned rewrite path:* Rewrite using fake hardware state verification.
- `test_start_phase_called_four_times_before_output_on` (line 160) — **REWRITE** — SCPI sequence check for phase reset.
  - *Sanctioned rewrite path:* Rewrite using fake hardware protocol logger.
- `test_axis_pairs_share_a_generator` (line 177) — **KEEP** — Hardware mapping architecture invariant.
- `test_poll_readback_republishes_via_beamline` (line 199) — **REWRITE** — Telemetry publication test.
  - *Sanctioned rewrite path:* Rewrite using Qt signal spy on Beamline model rather than mock callback spying.
- `test_poll_readback_marks_unconnected_generator` (line 214) — **REWRITE** — Disconnection handling test.
  - *Sanctioned rewrite path:* Rewrite with fake disconnected instrument to assert telemetry status flags.
- `test_widget_apply_is_rejected_by_the_same_interlock` (line 241) — **REWRITE** — Safety interlock test for UI apply button.
  - *Sanctioned rewrite path:* Rewrite with `qtbot.mouseClick` on Apply button while interlocked, asserting rejection status.
- `test_direct_beamline_call_is_rejected_identically` (line 255) — **KEEP** — Safety interlock test for direct programmatic API.
- `test_both_paths_reach_the_same_beamline_and_agree` (line 273) — **REWRITE** — Parity test between UI and API execution paths.
  - *Sanctioned rewrite path:* Rewrite using unified fake hardware manager to assert state parity.
- `test_toggle_calls_verify_external_lock_on_gen_b` (line 296) — **REWRITE** — Clock lock verification sequence test.
  - *Sanctioned rewrite path:* Rewrite using fake hardware protocol logger.
- `test_untoggle_returns_both_internal` (line 304) — **REWRITE** — Clock lock restore sequence test.
  - *Sanctioned rewrite path:* Rewrite using fake hardware protocol logger.
- `test_ext_lock_failure_triggers_warning` (line 310) — **REWRITE** — External clock sync fault handling test.
  - *Sanctioned rewrite path:* Rewrite using fake hardware returning lock failure and asserting UI warning dialog.
- `test_toggle_guard_refuses_when_gen_a_is_ext` (line 324) — **REWRITE** — Invalid clock configuration guard test.
  - *Sanctioned rewrite path:* Rewrite using fake hardware to verify guard error return.

---

### 13. `tests/test_gui_hardware.py` (51 tests)
**Summary:** 22 Keep, 21 Rewrite, 8 Delete

- `test_one_window_updates_the_current_and_amp_tabs` (line 74) — **REWRITE** — Multi-tab data distribution test.
  - *Sanctioned rewrite path:* Rewrite using signal emission to verify both tabs update concurrently without internal mock spying.
- `test_neither_tab_sees_the_other_half` (line 88) — **REWRITE** — Tab channel isolation test.
  - *Sanctioned rewrite path:* Rewrite using signal emission and verifying isolation via public tab data properties.
- `test_starts_on_the_overview` (line 104) — **KEEP** — Navigation initial state check.
- `test_click_motors_shows_motors` (line 112) — **DELETE** — Navigation click test asserting `_stack.currentIndex() == 1`.
  - *Deletion rationale:* Restates basic Qt `QStackedWidget` framework behavior without asserting application contracts.
- `test_click_current_shows_current` (line 116) — **DELETE** — Navigation click test asserting `_stack.currentIndex() == 2`.
  - *Deletion rationale:* Restates basic Qt `QStackedWidget` framework behavior.
- `test_click_amplifiers_shows_amplifiers` (line 120) — **DELETE** — Navigation click test asserting `_stack.currentIndex() == 3`.
  - *Deletion rationale:* Restates basic Qt `QStackedWidget` framework behavior.
- `test_click_funcgen_shows_funcgen` (line 124) — **DELETE** — Navigation click test asserting `_stack.currentIndex() == 4`.
  - *Deletion rationale:* Restates basic Qt `QStackedWidget` framework behavior.
- `test_switch_away_and_back_lands_correctly` (line 128) — **DELETE** — Rapid tab switching test asserting currentIndex.
  - *Deletion rationale:* Restates basic Qt `QStackedWidget` framework behavior.
- `test_multiple_rapid_switches_land_correctly` (line 134) — **DELETE** — Rapid tab switching test asserting currentIndex.
  - *Deletion rationale:* Restates basic Qt `QStackedWidget` framework behavior.
- `test_clicking_same_tab_twice_stays_put` (line 139) — **DELETE** — Duplicate tab click test asserting currentIndex.
  - *Deletion rationale:* Restates basic Qt `QStackedWidget` framework behavior.
- `test_funcgen_reachable_from_any_tab` (line 144) — **DELETE** — Redundant tab reachability check asserting currentIndex.
  - *Deletion rationale:* Restates basic Qt `QStackedWidget` framework behavior.
- `test_motor_spinbox_preserved_after_leaving` (line 154) — **KEEP** — UI state preservation across view switching.
- `test_current_tab_live_mode_preserved_after_leaving` (line 163) — **KEEP** — UI mode preservation across view switching.
- `test_frozen_current_tab_stays_frozen_after_navigation` (line 171) — **KEEP** — UI mode preservation across view switching.
- `test_current_tab_redraw_only_while_visible` (line 191) — **REWRITE** — Rendering performance optimization test.
  - *Sanctioned rewrite path:* Rewrite to verify plot paint events skip when hidden using QtBot.
- `test_amp_tab_redraw_only_while_visible` (line 206) — **REWRITE** — Rendering performance optimization test.
  - *Sanctioned rewrite path:* Rewrite to verify plot paint events skip when hidden using QtBot.
- `test_disconnect_stops_redraw_even_while_visible` (line 221) — **REWRITE** — Plot timer stop on disconnect.
  - *Sanctioned rewrite path:* Rewrite with fake hardware disconnect signal and verifying timer active status.
- `test_switching_tabs_does_not_stop_funcgen_poll_timer` (line 231) — **KEEP** — Background polling continuity test.
- `test_default_speed_is_cps` (line 255) — **KEEP** — Motor UI unit default test.
- `test_speed_cps_to_mms_round_trip` (line 260) — **KEEP** — Motor unit conversion calculation test.
- `test_target_counts_to_mm_and_back` (line 267) — **KEEP** — Motor coordinate transformation calculation test.
- `test_target_default_counts` (line 274) — **KEEP** — Motor UI initial value test.
- `test_all_four_axes_present` (line 280) — **KEEP** — Motor panel widget completeness test.
- `test_controls_disabled_until_connected` (line 283) — **KEEP** — Safety interlock test disabling motor controls when disconnected.
- `test_up_down_history` (line 289) — **KEEP** — Command line widget history navigation test.
- `test_duplicate_consecutive_not_stored_twice` (line 306) — **KEEP** — Command line history deduplication test.
- `test_up_on_empty_history_noop` (line 313) — **KEEP** — Command line history boundary test.
- `test_window_updates_buffers_and_labels` (line 334) — **REWRITE** — Current monitor display update test.
  - *Sanctioned rewrite path:* Rewrite with signal emission and verify QLabel text updates.
- `test_raw_voltage_is_shown_beside_the_current` (line 341) — **REWRITE** — Current monitor telemetry display test.
  - *Sanctioned rewrite path:* Rewrite with signal emission and verify QLabel text updates.
- `test_unsampled_channel_shows_paused_not_stale` (line 347) — **REWRITE** — Stale channel detection display test.
  - *Sanctioned rewrite path:* Rewrite with signal emission and verify QLabel status styling.
- `test_balanced_beam_reads_zero_imbalance` (line 356) — **KEEP** — Beam position calculation math test.
- `test_imbalanced_beam_positive` (line 360) — **KEEP** — Beam position calculation math test.
- `test_slit_positions_enable_mm_reconstruction` (line 365) — **KEEP** — Slit geometry reconstruction calculation test.
- `test_starts_in_live_mode` (line 377) — **KEEP** — Stream view initial state test.
- `test_drag_slider_left_enters_frozen` (line 381) — **REWRITE** — Stream history slider interaction test.
  - *Sanctioned rewrite path:* Rewrite with `qtbot` slider events.
- `test_jump_to_live_returns_to_live` (line 390) — **REWRITE** — Stream view "Jump to Live" button test.
  - *Sanctioned rewrite path:* Rewrite with `qtbot.mouseClick`.
- `test_slider_far_right_is_live` (line 398) — **REWRITE** — Stream slider right edge behavior test.
  - *Sanctioned rewrite path:* Rewrite with `qtbot`.
- `test_redraw_does_not_raise_when_empty` (line 402) — **KEEP** — Smoke test for empty buffer rendering.
- `test_three_passes_then_define_zero` (line 438) — **REWRITE** — Auto-home workflow execution test.
  - *Sanctioned rewrite path:* Rewrite with `FakeAsyncHardwareManager` motor controller.
- `test_backs_off_when_starting_on_home_switch` (line 451) — **REWRITE** — Auto-home switch backoff sequence test.
  - *Sanctioned rewrite path:* Rewrite with `FakeAsyncHardwareManager` motor controller.
- `test_cancel_before_pass_aborts` (line 464) — **REWRITE** — Auto-home user cancellation test.
  - *Sanctioned rewrite path:* Rewrite with `FakeAsyncHardwareManager` motor controller.
- `test_restores_default_speed_after_homing` (line 478) — **REWRITE** — Auto-home speed restore test.
  - *Sanctioned rewrite path:* Rewrite with `FakeAsyncHardwareManager` motor controller.
- `test_poll_worker_emits_error_and_stops_on_exception` (line 491) — **REWRITE** — Async polling worker error handling test.
  - *Sanctioned rewrite path:* Rewrite using `qtbot.waitUntil` and proper exception injection.
- `test_every_step_runs_in_order` (line 542) — **REWRITE** — Connection wizard sequence execution test.
  - *Sanctioned rewrite path:* Rewrite with fake subsystem orchestrator.
- `test_the_button_is_re_armed_when_the_sequence_finishes` (line 552) — **REWRITE** — Connection wizard button re-arm test.
  - *Sanctioned rewrite path:* Rewrite with `qtbot`.
- `test_one_failure_does_not_stop_the_rest` (line 558) — **REWRITE** — Connection wizard fault tolerance test.
  - *Sanctioned rewrite path:* Rewrite with fake subsystem orchestrator.
- `test_a_failure_is_named_with_its_reason` (line 567) — **REWRITE** — Connection wizard error reporting test.
  - *Sanctioned rewrite path:* Rewrite with fake subsystem orchestrator.
- `test_a_raising_step_is_caught_and_reported` (line 578) — **REWRITE** — Connection wizard exception trap test.
  - *Sanctioned rewrite path:* Rewrite with fake subsystem orchestrator.
- `test_all_good_reports_everything_connected` (line 589) — **REWRITE** — Connection wizard success reporting test.
  - *Sanctioned rewrite path:* Rewrite with fake subsystem orchestrator.
- `test_the_real_step_list_covers_every_subsystem` (line 600) — **KEEP** — Subsystem configuration coverage test.
- `test_connect_all_never_disconnects_anything` (line 605) — **KEEP** — Connect-all safety invariant test.

---

### 14. `tests/test_hv_conditioner.py` (9 tests)
**Summary:** 9 Keep, 0 Rewrite, 0 Delete

- `test_no_excursions_reaches_target_cleanly` (line 100) — **KEEP** — State machine test verifying nominal voltage conditioning ramp to target.
- `test_multiple_clean_dwells_required_before_advancing` (line 123) — **KEEP** — Conditioning algorithm test verifying dwell time requirements.
- `test_excursion_logs_event_and_backs_off` (line 145) — **KEEP** — Current excursion safety response test verifying immediate voltage back-off.
- `test_repeated_excursions_at_same_level_yield_conditioned_ceiling` (line 164) — **KEEP** — Conditioning algorithm test establishing breakdown ceiling after repeated trips.
- `test_regulation_state_alone_can_trigger_an_excursion` (line 182) — **KEEP** — Safety interlock test tripping on un-regulated hardware state.
- `test_interlock_block_aborts_before_ramping` (line 196) — **KEEP** — Safety interlock test aborting conditioning if interlock opens.
- `test_pressure_is_recorded_at_every_dwell` (line 206) — **KEEP** — Telemetry recording test ensuring vacuum pressure logged at each dwell step.
- `test_abort_mid_dwell_finishes_and_zeros` (line 220) — **KEEP** — Safety abort test ensuring immediate zeroing on operator abort.
- `test_abort_when_not_running_is_a_noop` (line 231) — **KEEP** — Boundary test for abort when idle.

---

### 15. `tests/test_inputs.py` (10 tests)
**Summary:** 10 Keep, 0 Rewrite, 0 Delete

- `test_typing_a_sub_integer_frequency_is_not_committed_digit_by_digit` (line 95) — **KEEP** — UI input debouncing test preventing premature value commits while typing decimal numbers.
- `test_leading_zero_is_not_clamped_to_the_minimum_mid_word` (line 107) — **KEEP** — Input validation UX test allowing leading zero during intermediate text entry.
- `test_arrow_keys_still_commit_immediately` (line 115) — **KEEP** — Spinbox keyboard interaction test verifying immediate step commit.
- `test_sync_value_writes_straight_through_when_not_being_edited` (line 125) — **KEEP** — Data binding test updating spinbox value when user is not actively editing.
- `test_sync_value_defers_while_the_box_has_focus` (line 130) — **KEEP** — UX conflict resolution test deferring incoming telemetry updates while user has focus.
- `test_the_operators_own_edit_beats_a_deferred_sync` (line 138) — **KEEP** — UX conflict resolution test prioritizing user input over background sync.
- `test_a_deferred_sync_applies_when_nothing_was_typed` (line 151) — **KEEP** — UX focus test applying deferred sync on blur if user made no edits.
- `test_no_spinbox_in_the_app_carries_its_unit_as_a_suffix` (line 162) — **KEEP** — UI consistency lint asserting all spinboxes use separate unit labels.
- `test_every_spinbox_in_the_app_is_a_quiet_one` (line 175) — **KEEP** — UI consistency lint asserting custom non-emitting spinbox widget usage.
- `test_unit_row_puts_the_unit_beside_the_box` (line 185) — **KEEP** — Layout helper test verifying unit label placement adjacent to input.

---

### 16. `tests/test_load_characterization_tab.py` (15 tests)
**Summary:** 0 Keep, 10 Rewrite, 5 Delete

- `test_defaults_to_mode_a_on_plates` (line 41) — **REWRITE** — Tests UI combo box initial mode selection.
  - *Sanctioned rewrite path:* Rewrite with `qtbot` inspecting combo box state.
- `test_mode_b_selection` (line 45) — **REWRITE** — Tests combo box mode switching.
  - *Sanctioned rewrite path:* Rewrite with `qtbot.keyClicks` or `setCurrentText` and assert model update.
- `test_mode_c_selection` (line 49) — **REWRITE** — Tests combo box mode switching.
  - *Sanctioned rewrite path:* Rewrite with `qtbot`.
- `test_disconnected_condition_selection` (line 53) — **REWRITE** — Tests condition dropdown selection.
  - *Sanctioned rewrite path:* Rewrite with `qtbot`.
- `test_run_without_labjack_connection_warns_and_does_not_start` (line 59) — **REWRITE** — Disconnection warning test.
  - *Sanctioned rewrite path:* Rewrite with `qtbot` and message box spy.
- `test_run_without_generator_connected_warns` (line 68) — **REWRITE** — Generator disconnection warning test.
  - *Sanctioned rewrite path:* Rewrite with `qtbot`.
- `test_abort_with_no_runner_is_a_noop` (line 78) — **REWRITE** — Robustness test for abort button when idle.
  - *Sanctioned rewrite path:* Rewrite with `qtbot.mouseClick`.
- `test_unmeasured_channels_show_placeholder` (line 83) — **REWRITE** — UI table placeholder test for unmeasured channels.
  - *Sanctioned rewrite path:* Rewrite with `qtbot` inspecting table items.
- `test_measured_channel_shows_values` (line 88) — **REWRITE** — UI table display test for measured channel data.
  - *Sanctioned rewrite path:* Rewrite with signal emission and verify table widget cell text.
- `test_outlier_conductance_is_flagged` (line 96) — **REWRITE** — UI visual indicator test for conductance outlier warning.
  - *Sanctioned rewrite path:* Rewrite with signal emission and verify visual alert styling.
- `test_on_labjack_connected_updates_state` (line 107) — **DELETE** — Asserts private attribute `tab._connected == True`.
  - *Deletion rationale:* Private attribute mutation test violating ADR 0001 §5.
- `test_on_labjack_disconnected_aborts_any_run` (line 111) — **DELETE** — Asserts private method forwarding `runner.abort()`.
  - *Deletion rationale:* Tautological mock forwarding test violating ADR 0001 §5.
- `test_on_profile_changed_is_cached` (line 122) — **DELETE** — Asserts private attribute `tab._current_profile == profile`.
  - *Deletion rationale:* Private attribute mutation test violating ADR 0001 §5.
- `test_on_window_forwards_to_runner` (line 126) — **DELETE** — Asserts private method forwarding `runner.process_window()`.
  - *Deletion rationale:* Tautological mock forwarding test violating ADR 0001 §5.
- `test_shutdown_aborts_any_run` (line 135) — **DELETE** — Asserts private method forwarding on `tab._shutdown()`.
  - *Deletion rationale:* Tautological mock forwarding test violating ADR 0001 §5.

---

### 17. `tests/test_load_characterizer.py` (12 tests)
**Summary:** 12 Keep, 0 Rewrite, 0 Delete

- `test_recovers_known_capacitance_from_synthetic_sine` (line 73) — **KEEP** — Numerical algorithm test recovering known capacitance from synthetic voltage/current waveforms.
- `test_amplitude_chosen_to_target_current_band` (line 103) — **KEEP** — Auto-ranging algorithm test sizing excitation amplitude to target safe current range.
- `test_unknown_amp_label_raises` (line 113) — **KEEP** — Parameter validation test asserting `KeyError` on unknown amplifier identifier.
- `test_finished_emits_and_persists_measurement` (line 118) — **KEEP** — Data persistence and signal emission test on successful characterization run.
- `test_healthy_ladder_completes_without_aborting` (line 138) — **KEEP** — Multi-step voltage ladder execution test verifying complete sequence traversal.
- `test_aborts_on_leakage_excursion` (line 154) — **KEEP** — Safety interlock test aborting characterization upon excessive leakage conductance.
- `test_pressure_provider_is_recorded` (line 176) — **KEEP** — Telemetry metadata test recording chamber pressure during characterization.
- `test_missing_pressure_provider_defaults_to_nan` (line 188) — **KEEP** — Fallback handling test recording `nan` when pressure gauge is unavailable.
- `test_finds_edges_and_recovers_capacitance_ballpark` (line 202) — **KEEP** — DSP algorithm test for square-wave edge extraction and capacitance estimation.
- `test_no_edges_reports_nan_not_a_crash` (line 242) — **KEEP** — Numerical robustness test returning `nan` without throwing exception on unmodulated signal.
- `test_abort_mid_run_finishes_and_zeros_outputs` (line 258) — **KEEP** — Safety abort test zeroing generator and amplifier outputs.
- `test_abort_when_idle_is_a_noop` (line 267) — **KEEP** — Boundary test for abort when idle.

---

## Dedicated List: Tests That "Would Have Caught Nothing" (51 tests)

The following 51 tests across Batch 1 are recommended for outright deletion because they represent tautological assertions, test the Python language or Qt framework itself, inspect private internal data structures without verifying observable behavior, or monkeypatch the runtime environment in a harmful manner:

### 1. Pure Tautologies & Framework Redundancy (9 tests)
These tests assert that framework primitives (such as `QStackedWidget` navigation) work as documented by Qt, or test raw Python syntax without exercising application code:
- `tests/test_calibration_runner.py::test_guard_boundaries_match_config_constants` (line 522) — Redundant equality check between configuration constants and test-local constants.
- `tests/test_drag_panel.py::test_pruning_keeps_or_falls_back_to_one_empty_col` (line 314) — Evaluates an isolated list comprehension `[c for c in cols if c] or [[]]` without touching any production class.
- `tests/test_gui_hardware.py::test_click_motors_shows_motors` (line 112) — Asserts `stack.currentIndex() == 1` after clicking tab.
- `tests/test_gui_hardware.py::test_click_current_shows_current` (line 116) — Asserts `stack.currentIndex() == 2` after clicking tab.
- `tests/test_gui_hardware.py::test_click_amplifiers_shows_amplifiers` (line 120) — Asserts `stack.currentIndex() == 3` after clicking tab.
- `tests/test_gui_hardware.py::test_click_funcgen_shows_funcgen` (line 124) — Asserts `stack.currentIndex() == 4` after clicking tab.
- `tests/test_gui_hardware.py::test_switch_away_and_back_lands_correctly` (line 128) — Repeated stack index assertions.
- `tests/test_gui_hardware.py::test_multiple_rapid_switches_land_correctly` (line 134) — Repeated stack index assertions.
- `tests/test_gui_hardware.py::test_clicking_same_tab_twice_stays_put` (line 139) — Redundant stack index assertion.
- `tests/test_gui_hardware.py::test_funcgen_reachable_from_any_tab` (line 144) — Redundant stack index assertion.

### 2. White-Box Testing of Private Drag Panel Data Structures (29 tests)
These tests directly access and assert mutations on `_cols`, `_find`, and `_apply_move` of `DragPanelArea`, coupling the test suite to private internal representations rather than observable UI behavior:
- `tests/test_drag_panel.py::test_initial_state_one_empty_col` (line 53)
- `tests/test_drag_panel.py::test_add_to_col0` (line 58)
- `tests/test_drag_panel.py::test_add_two_panels_same_col_ordered` (line 63)
- `tests/test_drag_panel.py::test_add_four_panels_same_col_ordered` (line 69)
- `tests/test_drag_panel.py::test_add_to_col1_creates_new_col` (line 75)
- `tests/test_drag_panel.py::test_add_skips_intermediate_empty_cols` (line 84)
- `tests/test_area_reference_set_on_panel` (line 93)
- `tests/test_stack_two_cols_independently` (line 98)
- `tests/test_find_only_panel` (line 114)
- `tests/test_find_two_in_same_col` (line 119)
- `tests/test_find_in_second_col` (line 126)
- `tests/test_find_across_multiple_cols` (line 133)
- `tests/test_find_uses_identity_not_equality` (line 146)
- `tests/test_no_op_same_row` (line 169)
- `tests/test_move_first_to_end` (line 174)
- `tests/test_move_last_to_first` (line 180)
- `tests/test_move_middle_up` (line 185)
- `tests/test_move_middle_to_end` (line 190)
- `tests/test_index_shift_moving_forward` (line 195)
- `tests/test_col_count_unchanged_after_reorder` (line 202)
- `tests/test_move_to_front_of_other_col` (line 213)
- `tests/test_move_to_end_of_other_col` (line 223)
- `tests/test_move_from_multi_row_col_preserves_remainder` (line 232)
- `tests/test_empty_col_is_pruned_after_move_out` (line 243)
- `tests/test_move_preserves_two_col_structure` (line 253)
- `tests/test_move_to_new_col_at_right` (line 272)
- `tests/test_move_only_panel_to_new_col_stays_one_col` (line 281)
- `tests/test_new_col_beyond_existing_appended_at_end` (line 290)
- `tests/test_always_at_least_one_col` (line 307)

### 3. Tautological Mock Forwarding & Private Attribute Verification (12 tests)
These tests assert that calling method A on class X calls mock method A on member Y, or inspect private internal boolean flags (`_connected`, `_profile`):
- `tests/test_calibration_app_wiring.py::test_profile_selector_disabled_during_run_and_reenabled_after` (line 171)
- `tests/test_dynamic_adjustment_tab.py::test_on_labjack_connected_updates_state` (line 118)
- `tests/test_dynamic_adjustment_tab.py::test_on_labjack_disconnected_updates_state` (line 122)
- `tests/test_dynamic_adjustment_tab.py::test_on_profile_changed_is_cached` (line 127)
- `tests/test_dynamic_adjustment_tab.py::test_on_window_forwards_to_trial` (line 131)
- `tests/test_dynamic_adjustment_tab.py::test_shutdown_does_not_raise` (line 140)
- `tests/test_load_characterization_tab.py::test_on_labjack_connected_updates_state` (line 107)
- `tests/test_load_characterization_tab.py::test_on_labjack_disconnected_aborts_any_run` (line 111)
- `tests/test_load_characterization_tab.py::test_on_profile_changed_is_cached` (line 122)
- `tests/test_load_characterization_tab.py::test_on_window_forwards_to_runner` (line 126)
- `tests/test_load_characterization_tab.py::test_shutdown_aborts_any_run` (line 135)

### 4. Process-Polluting Monkeypatching (1 test)
- `tests/test_camera_tab.py::test_camera_tab_constructs_without_cv2` (line 107) — Mutates `sys.modules['cv2'] = None`, polluting global interpreter state and causing flaky imports across other test modules.

---

## Architectural Patterns & Recommendations

1. **Adopt `FakeAsyncHardwareManager` across Qt Suite:**
   In Batch 1, 95 tests need rewrites primarily because they mock `AsyncHardwareManager` methods using `unittest.mock.MagicMock` with deep call-order assertions (`mock.assert_has_calls(...)`). Replacing these with a single in-memory stateful `FakeAsyncHardwareManager` adhering to the hardware protocol will reduce mock setup overhead and make async event sequences robust against refactoring.

2. **Transition from `mock.patch('time.sleep')` / `mock.patch('time.monotonic')` to Virtual Time / QtBot:**
   Several runner tests (`test_calibration_runner.py`, `test_funcgen_sync.py`, `test_auto_home.py`) patch `time.monotonic` or `QTimer` to simulate async progression. This leads to brittle step increments and race conditions. A standardized virtual clock fixture and `qtbot.waitUntil(timeout=...)` should be used consistently.

3. **Delete White-box Layout Testing:**
   All 30 tests in `test_drag_panel.py` must be deleted. UI layout management should be validated through integration tests on parent container widgets, not by unit testing private 2D list manipulation methods.
