# Audit — Qt test modules, batch 2 (ticket 10)

**Verdicts only. No test file, ADR, or production module was modified while producing this audit.** Criteria are ADR 0001's vocabulary, as restated in ticket 10:

- **Keep** — drives the production path and asserts on something an operator could see or a downstream consumer reads, tests pure math/configuration models, or serves as a cross-tab / system-wide contract test. Any test reaching widgets through shared payload helpers (`tests/payloads.py`) is a model to imitate and kept.
- **Rewrite** — covers real behaviour but reaches it by calling a private method or reading a private attribute (`_`). Must name the sanctioned path the rewrite would use (e.g. `tests/payloads.py` / `LabJackFeed`, `Beamline` stream ingestion, public Qt signals, or operator-visible widget text).
- **Delete** — a churn test (fails on restructuring with no behaviour change, would not have caught a real defect), restates a guarantee the Qt framework already makes, tests negative attribute existence (`not hasattr`), or writes private state and reads it back.

## Batch file list (15 files)

The Qt-importing test modules whose filename sorts alphabetically after `test_load_characterizer.py`.
Exhaustive and disjoint from Audit 07 (28 hardware/math files), Audit 08 (20 state/services files),
and Audit 09 (17 Qt batch 1 files): 28 + 20 + 17 + 15 = 80 total test files across the repository.

1. `tests/test_no_scroll_inputs.py`
2. `tests/test_overview_tab.py`
3. `tests/test_ramp_engine.py`
4. `tests/test_raster_planner_tab.py`
5. `tests/test_recording_panel.py`
6. `tests/test_regulation_monitor.py`
7. `tests/test_regulation_response.py`
8. `tests/test_scope_acquisition_and_ports.py`
9. `tests/test_session_recorder.py`
10. `tests/test_setpoint_sync.py`
11. `tests/test_slit_control.py`
12. `tests/test_slit_cross_screen.py`
13. `tests/test_video_recorder.py`
14. `tests/test_video_transcoder.py`
15. `tests/test_widgets_mini.py`

## Result

**317 test functions audited across 15 files: 141 KEEP, 164 REWRITE, 12 DELETE.**

| # | File | Total | Keep | Rewrite | Delete |
|---|---|---:|---:|---:|---:|
| 1 | tests/test_no_scroll_inputs.py | 23 | 21 | 0 | 2 |
| 2 | tests/test_overview_tab.py | 63 | 26 | 35 | 2 |
| 3 | tests/test_ramp_engine.py | 14 | 12 | 2 | 0 |
| 4 | tests/test_raster_planner_tab.py | 87 | 7 | 75 | 5 |
| 5 | tests/test_recording_panel.py | 5 | 0 | 4 | 1 |
| 6 | tests/test_regulation_monitor.py | 12 | 10 | 2 | 0 |
| 7 | tests/test_regulation_response.py | 4 | 4 | 0 | 0 |
| 8 | tests/test_scope_acquisition_and_ports.py | 37 | 12 | 24 | 1 |
| 9 | tests/test_session_recorder.py | 7 | 4 | 3 | 0 |
| 10 | tests/test_setpoint_sync.py | 10 | 10 | 0 | 0 |
| 11 | tests/test_slit_control.py | 10 | 8 | 2 | 0 |
| 12 | tests/test_slit_cross_screen.py | 9 | 6 | 3 | 0 |
| 13 | tests/test_video_recorder.py | 5 | 5 | 0 | 0 |
| 14 | tests/test_video_transcoder.py | 6 | 1 | 4 | 1 |
| 15 | tests/test_widgets_mini.py | 25 | 15 | 10 | 0 |
| | **Total** | **317** | **141** | **164** | **12** |

### Models to imitate (highlighted)
- **`tests/test_setpoint_sync.py` (all 10 tests)**: Exemplary cross-tab agreement tests. Tests both directions of synchronization between `FuncGenTab` and `OverviewTab` via `Beamline` / `FuncGenSetpoints`, asserting on operator-visible widgets (`spn_amp`, `spn_freq`) and verifying unit conversion round-trips without accessing private attributes.
- **`tests/test_no_scroll_inputs.py` (`TestEveryDropdownInTheApp`, 9 tests)**: Exemplary whole-codebase contract tests. States one critical UX safety rule (no accidental mouse-wheel tampering with numeric inputs) and enforces it uniformly across all application screens (`MotorTab`, `FuncGenTab`, `AmpTab`, `ProfilerTab`, `VacuumTab`, `BeamPositionIndicator`, `OverviewTab`).

### Summary of Deletes and Rewrites
The 12 `delete` verdicts represent pure churn:
- **Framework behavior restatements (2)**: `test_a_plain_combo_box_would_have` and `test_a_plain_spin_box_would_have` re-test base Qt widget defaults.
- **Framework event / private flag plumbing (2)**: `test_redraw_is_a_noop_while_hidden` and `test_show_event_starts_timer_and_repaints_immediately` test internal visibility flags and QTimer start calls.
- **Negative attribute assertions on deleted legacy code (5)**: `TestGoneForGood` (`test_no_steerer_picker`, `test_no_single_capacitance_channel_picker`, `test_no_lissajous_readout`, `test_no_bare_unlabelled_kv_readouts`, `test_no_half_width_boxes`) assert `not hasattr` on obsolete attributes from past refactors.
- **No-op construction check (1)**: `test_panel_constructs_without_error` asserts `p is not None`.
- **Source code string inspection (1)**: `test_the_measure_step_catches_more_than_transport_errors` performs `inspect.getsource()` substring search.
- **Duplicate test (1)**: `test_find_ffmpeg_no_exception_when_absent` duplicates `test_find_ffmpeg_returns_none_when_nothing_found`.

The 164 `rewrite` verdicts cover genuine operator-facing or mathematical behaviors that currently reach into private attributes (`_redraw()`, `_solution`, `_worker`, `_target`, `_consec`, `_last_measured`). Every rewrite below specifies the sanctioned public path (`tests/payloads.py` `LabJackFeed`, `Beamline` state ingestion, public widget queries, or Qt signals).

---

## tests/test_no_scroll_inputs.py

Production module read: `src/rbl/gui/widgets/inputs.py`

- `TestNoScrollComboBox.test_the_wheel_does_not_change_the_selection` — **KEEP** — Tests `NoScrollComboBox` custom widget directly by dispatching wheel events and asserting the selected index remains unchanged.
- `TestNoScrollComboBox.test_a_plain_combo_box_would_have` — **DELETE** — Restates Qt framework default behavior (verifying that a standard `QComboBox` changes index on wheel event); churn test that tests third-party library behavior rather than application code.
- `TestNoScrollComboBox.test_the_event_is_passed_up_rather_than_swallowed` — **KEEP** — Verifies custom event handling contract: `NoScrollComboBox` leaves wheel events unaccepted so containing scroll containers can scroll.
- `TestNoScrollComboBox.test_clicking_an_item_still_selects_it` — **KEEP** — Verifies standard item selection remains functional on `NoScrollComboBox`.
- `TestQuietDoubleSpinBox.test_the_wheel_does_not_change_the_value` — **KEEP** — Tests `QuietDoubleSpinBox` custom spin box by dispatching wheel events and asserting the numeric value does not change.
- `TestQuietDoubleSpinBox.test_not_even_when_it_has_focus` — **KEEP** — Tests focus state safety on `QuietDoubleSpinBox`, confirming wheel events over a focused input are ignored.
- `TestQuietDoubleSpinBox.test_it_emits_nothing` — **KEEP** — Asserts that `QuietDoubleSpinBox` emits no `valueChanged` signals when scrolled over.
- `TestQuietDoubleSpinBox.test_a_plain_spin_box_would_have` — **DELETE** — Restates Qt framework default behavior (verifying standard `QDoubleSpinBox` changes value on wheel event); churn test testing upstream library defaults.
- `TestQuietDoubleSpinBox.test_the_event_is_passed_up_rather_than_swallowed` — **KEEP** — Verifies event propagation contract: wheel event is not accepted and propagates to parent scroll container.
- `TestQuietDoubleSpinBox.test_the_arrows_still_step_it` — **KEEP** — Verifies targeted keyboard / arrow stepping functionality remains active on `QuietDoubleSpinBox`.
- `TestQuietDoubleSpinBox.test_typing_still_commits` — **KEEP** — Verifies keyboard input interpretation and commit on focus-out behavior.
- `TestNoScrollSpinBox.test_wheel_does_not_change_the_value` — **KEEP** — Tests whole-number `NoScrollSpinBox` ignoring wheel events.
- `TestNoScrollSpinBox.test_wheel_event_is_ignored_so_a_scroll_area_still_scrolls` — **KEEP** — Verifies `NoScrollSpinBox` wheel event is not accepted, allowing container scrolling.
- `TestNoScrollSpinBox.test_arrow_keys_still_work` — **KEEP** — Verifies stepUp/stepDown arrow functionality remains operational on `NoScrollSpinBox`.
- `TestEveryDropdownInTheApp.test_stepper_motors_tab` — **KEEP** — Contract test: scans all comboboxes and spinboxes on `MotorTab` and verifies they use `NoScrollComboBox`, `QuietDoubleSpinBox`, or `NoScrollSpinBox`.
- `TestEveryDropdownInTheApp.test_function_generators_tab` — **KEEP** — Contract test: verifies all input controls on `FuncGenTab` are quiet/no-scroll subclasses.
- `TestEveryDropdownInTheApp.test_hv_amplifier_tab` — **KEEP** — Contract test: verifies all input controls on `AmpTab` are safe subclasses.
- `TestEveryDropdownInTheApp.test_beam_profiler_tab` — **KEEP** — Contract test: verifies all dropdowns and spinboxes on `ProfilerTab` are safe subclasses.
- `TestEveryDropdownInTheApp.test_vacuum_tab` — **KEEP** — Contract test: verifies all dropdowns on `VacuumTab` are safe subclasses.
- `TestEveryDropdownInTheApp.test_beam_position_indicator` — **KEEP** — Contract test: verifies `BeamPositionIndicator` inputs are safe subclasses.
- `TestEveryDropdownInTheApp.test_every_numeric_box_is_quiet` — **KEEP** — Contract test: scans `MotorTab`, `FuncGenTab`, and `OverviewTab` to ensure >= 4 numeric spinboxes exist and all are `QuietDoubleSpinBox`.
- `TestEveryDropdownInTheApp.test_a_slit_target_cannot_be_nudged_by_a_wheel` — **KEEP** — End-to-end UX integration test: verifies scrolling over `OverviewTab` slit target spinner does not alter commanded move value.
- `TestEveryDropdownInTheApp.test_unit_dropdowns_keep_their_options` — **KEEP** — Verifies dropdown items and default units on `MotorTab` panels.

---

## tests/test_overview_tab.py

Production module read: `src/rbl/gui/overview_tab.py`

- `test_caches_motor_state_and_redraws_slit_bar` — **REWRITE** — Covers motor position caching and slit bar label rendering, but invokes private `tab._redraw()`. Sanctioned path: emit `beamline.motors_changed` and assert on operator-visible `tab.slits["X+"].bar.lbl_value.text()`.
- `test_slit_bar_blank_when_motors_disconnected` — **REWRITE** — Covers disconnected motor state rendering, but invokes private `tab._redraw()`. Sanctioned path: emit disconnected `motors_changed` on `Beamline` and assert on `tab.slits["X+"].bar.lbl_value.text()` and `bar.fraction()`.
- `test_gap_is_the_sum_of_both_slit_positions` — **REWRITE** — Covers slit gap summation display, but uses helper that invokes `tab._redraw()`. Sanctioned path: emit `motors_changed` on `Beamline` and assert on operator-visible `tab.lbl_gaps.text()`.
- `test_gap_unknown_while_disconnected` — **REWRITE** — Covers disconnected gap display, but invokes `tab._redraw()`. Sanctioned path: emit disconnected `motors_changed` and assert on `tab.lbl_gaps.text()`.
- `test_logamp_state_feeds_the_beam_indicator` — **REWRITE** — Covers beam position indicator current feeds, but reads private `tab.beam._currents` and calls `_redraw()`. Sanctioned path: feed stream window via `tests/payloads.py` `LabJackFeed` (or `beamline.logamps_changed`) and assert on `BeamPositionIndicator` visual rendering or public state.
- `test_logamp_state_feeds_the_per_slit_current_bars` — **REWRITE** — Covers log-amp current bar rendering, but invokes `tab._redraw()`. Sanctioned path: push window payload via `tests/payloads.py` `LabJackFeed` and assert on `tab.currents["X+"].fraction()` and label text.
- `test_current_bar_blank_for_an_unsampled_channel` — **REWRITE** — Covers unsampled channel (NaN) blank bar handling, but calls `tab._redraw()`. Sanctioned path: push profile payload with missing/NaN channel via `tests/payloads.py` and assert blank current bar label.
- `test_amp_state_feeds_the_hv_peak_bars` — **REWRITE** — Covers HV amplifier peak voltage readouts on Overview, but calls `tab._redraw()`. Sanctioned path: feed amplifier stream window through `tests/payloads.py` `LabJackFeed` and assert on `tab.hv_peak` bars and text.
- `test_a_push_pull_pair_reads_as_locked` — **REWRITE** — Covers push-pull phase lock detection and display, but calls `tab._redraw()`. Sanctioned path: push differential waveforms through `tests/payloads.py` `LabJackFeed` and assert on phase-lock indicator label text.
- `test_two_in_phase_channels_are_flagged` — **REWRITE** — Covers in-phase fault detection on push-pull pair, but calls `tab._redraw()`. Sanctioned path: push in-phase waveforms through `tests/payloads.py` `LabJackFeed` and assert on operator-visible warning banner.
- `test_no_waveform_is_reported_as_absent_not_as_a_fault` — **REWRITE** — Covers absent waveform rendering during non-sampling profiles, but calls `tab._redraw()`. Sanctioned path: push payload without waveform arrays via `tests/payloads.py` and assert clean status.
- `test_trace_caption_reads_back_the_window_it_settled_on` — **REWRITE** — Covers trace window span caption, but calls `tab._redraw()`. Sanctioned path: push window payload with span metadata and assert on trace caption label text.
- `test_trace_caption_says_when_no_cycle_was_found` — **REWRITE** — Covers unmeasured frequency trace caption, but calls `tab._redraw()`. Sanctioned path: push raw window payload without detected cycles and assert caption text.
- `test_trace_caption_is_blank_without_a_window` — **REWRITE** — Covers blank caption when no amp window received, but calls `tab._redraw()`. Sanctioned path: emit empty `AmpState` on `Beamline` and assert blank caption text.
- `test_funcgen_readback_feeds_the_axis_amplitude_bar` — **REWRITE** — Covers funcgen readback amplitude bar display, but calls `tab._redraw()`. Sanctioned path: emit `beamline.funcgens_changed` and assert on `tab.drives["X"].bar.lbl_value.text()`.
- `test_axis_bar_blank_when_generator_not_connected` — **REWRITE** — Covers disconnected generator display on axis bar, but calls `tab._redraw()`. Sanctioned path: emit disconnected `funcgens_changed` and assert blank axis bar text.
- `test_a_split_pair_is_called_out_on_the_live_line` — **REWRITE** — Covers split pair warning banner on live readback, but calls `tab._redraw()`. Sanctioned path: emit mismatched `FuncGenState` on `Beamline` and assert on warning label text.
- `test_connection_pills_track_each_subsystem` — **KEEP** — Tests subsystem connection indicator pills updating from public state emissions.
- `test_command_failed_updates_the_status_label` — **KEEP** — Tests operator status label displaying error messages on `beamline.command_failed` signal.
- `test_move_goes_through_beamline` — **KEEP** — Tests clicking slit move button commands motion through `Beamline` command interface.
- `test_move_marks_the_commanded_target_on_the_bar` — **REWRITE** — Covers target caret marking on slit bar, but calls private `tab._on_move_requested` and reads `_target`. Sanctioned path: click `tab.slits["X+"].btn_move` and assert target position via public bar target property.
- `test_target_published_by_another_screen_lands_on_this_one` — **REWRITE** — Covers cross-screen target synchronization, but reads private `bar.track._target`. Sanctioned path: emit `beamline.slit_target_changed` and assert on `tab.slits["X+"].spn_target.value()` and public bar target fraction.
- `test_target_beyond_the_old_ten_mm_ceiling_is_accepted` — **KEEP** — Tests entering a target beyond 10 mm (up to 25 mm) into the slit target spinbox.
- `test_current_bars_are_laid_out_as_a_diamond` — **KEEP** — Tests physical layout geometry of the slit current bars forming a diamond matching beamline axes.
- `test_move_refused_and_reported_while_disconnected` — **REWRITE** — Covers refusal and reporting of move when Galil is disconnected, but calls `_on_move_requested` and `_redraw()`. Sanctioned path: trigger move via UI button and assert status message.
- `test_unzeroed_move_needs_confirmation` — **REWRITE** — Covers safety confirmation dialog on unzeroed motor move, but monkeypatches `_confirm_unzeroed_move` and calls `_on_move_requested`. Sanctioned path: trigger move on unzeroed axis via public button with a mock dialog factory.
- `test_zeroed_move_asks_nothing` — **REWRITE** — Covers direct move execution without confirmation when zeroed, but monkeypatches `_confirm_unzeroed_move` and calls `_on_move_requested`. Sanctioned path: trigger move on zeroed axis and assert immediate command dispatch.
- `test_unzeroed_state_is_warned_about_on_screen` — **KEEP** — Tests unzeroed warning text in `tab.lbl_note` when motors are connected but not zeroed.
- `test_command_note_expires_and_the_warning_returns` — **REWRITE** — Covers status note timer expiration, but reaches into `_NOTE_FRAMES`, `_confirm_unzeroed_move`, `_on_move_requested`, and `_redraw()`. Sanctioned path: trigger move and let status message timer elapse.
- `test_no_stop_button_on_this_screen` — **KEEP** — Design contract test: asserts Overview tab deliberately has no abort button, preserving single abort control on Stepper Motors tab.
- `test_slit_controls_follow_the_galil_connection` — **REWRITE** — Covers slit control enable/disable tracking Galil connection, but calls `tab._redraw()`. Sanctioned path: emit `motors_changed` on `Beamline` and assert `tab.slits["X+"].btn_move.isEnabled()`.
- `test_target_boxes_preload_the_live_position_on_connect` — **KEEP** — Tests live motor position preloading into target spinbox upon connection.
- `test_connected_redraws_do_not_fight_the_operator` — **KEEP** — Tests operator edit preservation in target spinbox during ongoing motor state updates.
- `test_hv_amplifiers_stay_read_only` — **KEEP** — Design contract test: asserts Overview tab HV amplifier section exposes only readouts and no command inputs.
- `test_the_tab_holds_no_driver` — **KEEP** — Architecture contract test: asserts OverviewTab holds no direct driver instances and routes all commands via Beamline.
- `test_redraw_is_a_noop_while_hidden` — **DELETE** — Tests private `_visible` guard in internal `_redraw()`; churn test verifying internal widget flag optimization.
- `test_show_event_starts_timer_and_repaints_immediately` — **DELETE** — Tests internal `_redraw_timer` lifecycle during Qt `showEvent`; framework event plumbing churn test.
- `test_axis_edit_writes_both_channels_of_that_axis` — **KEEP** — Tests editing axis amplitude spinner updates both push-pull channels in shared `FuncGenSetpoints`.
- `test_axis_edit_never_flattens_the_push_pull_phase` — **KEEP** — Tests editing axis amplitude preserves 0 deg / 180 deg push-pull phase configuration.
- `test_output_toggle_is_intent_not_a_command` — **KEEP** — Tests output toggle button records intent in shared setpoints without immediately commanding hardware.
- `test_apply_all_sends_every_channel_through_beamline` — **KEEP** — Tests clicking Apply All commands setpoints for all channels through `Beamline`.
- `test_apply_all_asks_before_a_high_peak` — **REWRITE** — Covers high peak voltage confirmation dialog on Apply All, but monkeypatches private `tab._confirm_high_peak`. Sanctioned path: drive Apply All with high setpoint using a dialog factory or public confirmation hook.
- `test_apply_all_does_not_ask_below_the_advisory_threshold` — **REWRITE** — Covers Apply All execution without confirmation below advisory threshold, but monkeypatches `_confirm_high_peak`. Sanctioned path: drive Apply All below threshold and assert immediate dispatch.
- `test_apply_all_is_disabled_until_a_generator_is_connected` — **KEEP** — Tests Apply All button disabled when function generators are disconnected.
- `test_redraw_does_not_fight_the_operator_typing_an_amplitude` — **REWRITE** — Covers spinbox edit preservation during funcgen state redraws, but calls `tab._redraw()`. Sanctioned path: emit `funcgens_changed` on `Beamline` while editing and assert spinner value.
- `test_setpoint_change_from_elsewhere_lands_in_the_axis_boxes` — **KEEP** — Tests cross-tab sync: updates on `beamline.funcgen_setpoints` update Overview axis spinboxes.
- `test_timebase_checkbox_needs_both_generators` — **KEEP** — Tests timebase lock checkbox enabled only when both function generators are connected.
- `test_timebase_toggle_goes_through_beamline` — **KEEP** — Tests toggling timebase checkbox commands timebase lock through `Beamline`.
- `test_a_failed_lock_leaves_the_box_unchecked` — **REWRITE** — Covers timebase lock failure unchecking box, but monkeypatches private `tab._warn`. Sanctioned path: simulate failed timebase lock via `Beamline` and assert `tab.chk_timebase.isChecked()` is False.
- `test_the_lock_state_follows_the_other_tab` — **REWRITE** — Covers timebase sync between tabs, but calls private `tab._on_timebase_changed()`. Sanctioned path: emit `beamline.timebase_locked_changed` and assert `tab.chk_timebase.isChecked()`.
- `test_lock_state_arrives_on_the_funcgen_snapshot` — **REWRITE** — Covers timebase lock state display from FuncGen snapshot, but calls `tab._redraw()`. Sanctioned path: emit `funcgens_changed` and assert `tab.chk_timebase.isChecked()`.
- `test_the_absolute_position_note_sits_above_the_gap_readout` — **KEEP** — Tests UI visual hierarchy: position note label placed above gap readout.
- `test_every_current_bar_is_the_same_width` — **KEEP** — Tests layout sizing: asserts all slit current bars have uniform width.
- `test_the_y_bars_are_centred_over_the_x_pair` — **KEEP** — Tests layout geometry: asserts Y current bars are centred over X pair.
- `test_the_bars_come_out_equal_once_laid_out` — **KEEP** — Tests layout rendered geometry of slit position bars.
- `TestHvInterlockIndicator.test_no_payload_yet_shows_placeholder` — **REWRITE** — Covers initial HV interlock indicator placeholder, but reads `tab._hv_interlock` and calls `_redraw_hv_interlock()`. Sanctioned path: assert `tab.lbl_hv_interlock.text()` directly on constructed tab.
- `TestHvInterlockIndicator.test_ok_state_is_shown` — **REWRITE** — Covers OK interlock state display, but writes `tab._hv_interlock` and calls `_redraw_hv_interlock()`. Sanctioned path: emit `beamline.hv_interlock_changed` (or `vacuum_changed`) and assert on `tab.lbl_hv_interlock` text and style.
- `TestHvInterlockIndicator.test_block_state_is_shown` — **REWRITE** — Covers BLOCKED interlock state display, but writes `tab._hv_interlock` and calls `_redraw_hv_interlock()`. Sanctioned path: emit blocked interlock state on `Beamline` and assert on `tab.lbl_hv_interlock` text and style.
- `TestHvInterlockIndicator.test_stale_reading_reads_distinctly_from_a_pressure_block` — **REWRITE** — Covers STALE interlock state display, but writes `tab._hv_interlock` and calls `_redraw_hv_interlock()`. Sanctioned path: emit stale vacuum state on `Beamline` and assert on `tab.lbl_hv_interlock` text.
- `TestHvInterlockIndicator.test_beamline_signal_reaches_the_tab` — **REWRITE** — Covers Beamline interlock signal connection, but calls `_recompute_hv_interlock` and reads `_hv_interlock`. Sanctioned path: emit `beamline.hv_interlock_changed` and assert on `tab.lbl_hv_interlock.text()`.
- `TestConnectAllButton.test_the_button_only_emits_a_request` — **KEEP** — Tests Connect All button emits connection request signal without performing direct I/O.
- `TestConnectAllButton.test_busy_disables_the_button_so_it_cannot_be_double_pressed` — **KEEP** — Tests Connect All button disabled when connection process is in progress.
- `TestConnectAllButton.test_status_text_is_displayed` — **KEEP** — Tests status text label updated with connection status.

---

## tests/test_ramp_engine.py

Production module read: `src/rbl/hardware/ramp_engine.py`

- `TestOffsetRamp.test_reaches_target_voltage` — **KEEP** — Tests `RampEngine` DC offset ramp execution reaching target voltage on generator instrument.
- `TestOffsetRamp.test_final_step_uses_checked_write` — **KEEP** — Verifies `RampEngine` uses checked `set_offset` write on final step to detect errors.
- `TestOffsetRamp.test_intermediate_steps_use_write_fast_not_apply` — **KEEP** — Verifies intermediate steps use latency-optimized fast writes rather than disruptive `APPLy` calls.
- `TestOffsetRamp.test_emits_started_progress_finished` — **KEEP** — Tests `ramp_started`, `ramp_progress`, and `ramp_finished` Qt signals emitted during ramp lifecycle.
- `TestOffsetRamp.test_target_is_clamped_to_max_gen_volts` — **KEEP** — Tests offset target voltage clamping at `MAX_GEN_VOLTS` safety boundary.
- `TestAmplitudeRamp.test_reaches_target_vpp` — **KEEP** — Tests `RampEngine` AC amplitude ramp execution reaching target Vpp.
- `TestAmplitudeRamp.test_target_is_clamped_to_max_amp_vpp` — **KEEP** — Tests amplitude target clamping at `MAX_AMP_VPP` safety boundary.
- `TestRetargetMidRamp.test_updates_in_place_rather_than_queuing` — **REWRITE** — Covers mid-ramp retargeting behavior, but reads private `engine._ramps`. Sanctioned path: call `retarget()` mid-ramp and verify seamless progress to final target via public `current_step_value` and generator state without inspecting `_ramps`.
- `TestLockstepOrdering.test_pair_members_stepped_adjacent_and_in_canonical_order` — **KEEP** — Tests lockstep stepping order in `ramp_progress` signal emissions across push-pull channel pairs.
- `TestAbort.test_clears_all_ramps_without_commanding` — **REWRITE** — Covers ramp aborting, but reads private `engine._timer.isActive()`. Sanctioned path: call `abort()` and assert `is_ramping()` and `ramping_labels()` are empty without inspecting internal timer.
- `TestFailurePropagation.test_scpi_failure_on_final_step_emits_ramp_failed` — **KEEP** — Tests SCPI error handling on final checked step, confirming `ramp_failed` signal emission.
- `TestCurrentStepValue.test_none_when_not_ramping` — **KEEP** — Tests public `current_step_value()` returns None when channel is not ramping.
- `TestCurrentStepValue.test_tracks_interim_value_while_ramping` — **KEEP** — Tests public `current_step_value()` returns interim progress voltage during active ramp.
- `TestUnknownChannel.test_emits_ramp_failed_for_unmapped_label` — **KEEP** — Tests `ramp_failed` signal emitted when commanding unmapped channel label.

---

## tests/test_raster_planner_tab.py

Production module read: `src/rbl/gui/raster_planner_tab.py`

- `TestPerChannelCapacitance.test_reports_all_four_channels` — **REWRITE** — Covers reporting all four amplifier channels, but calls private `tab._channel_capacitance()`. Sanctioned path: inspect capacitance table column items or public getter.
- `TestPerChannelCapacitance.test_unmeasured_channels_fall_back_and_say_so` — **REWRITE** — Covers fallback indicator for unmeasured channels, but calls `tab._channel_capacitance()`. Sanctioned path: inspect capacitance table status text for fallback annotation.
- `TestPerChannelCapacitance.test_one_measured_channel_does_not_change_the_others` — **REWRITE** — Covers isolated channel calibration update, but calls `tab._channel_capacitance()`. Sanctioned path: update calibration in store, click refresh, and assert table status text.
- `TestPerChannelCapacitance.test_each_channel_current_uses_its_own_capacitance` — **REWRITE** — Covers per-channel current calculation scaling with channel capacitance, but calls `tab._recompute()` and parses `tab.lbl_currents.text()`. Sanctioned path: save distinct capacitances in store and assert current readouts on UI labels.
- `TestPerChannelCapacitance.test_a_fallback_channel_is_flagged_not_silent` — **REWRITE** — Covers visible flagging of fallback channels, but calls `tab._recompute()`. Sanctioned path: assert fallback notification in `tab.lbl_c_source.text()`.
- `TestTheJawsAreImagedOntoTheSample.test_the_patch_is_the_size_that_was_asked_for` — **REWRITE** — Covers patch size calculation at sample, but calls `tab._recompute()` and reads `_solution`. Sanctioned path: set patch spinboxes and assert patch size readout label.
- `TestTheJawsAreImagedOntoTheSample.test_the_jaws_are_set_narrower_than_the_patch` — **REWRITE** — Covers jaw gap calculation set narrower than sample patch, but calls `tab._recompute()` and reads `_solution`. Sanctioned path: assert jaw gap readout label is narrower than requested patch.
- `TestTheJawsAreImagedOntoTheSample.test_the_two_axes_get_different_magnifications` — **REWRITE** — Covers unequal axis magnifications due to differing slit distances, but calls `tab._recompute()` and reads `_solution`. Sanctioned path: assert magnification label text.
- `TestTheJawsAreImagedOntoTheSample.test_a_square_request_gives_unequal_jaws` — **REWRITE** — Covers square patch producing unequal X and Y jaw gaps, but calls `tab._recompute()` and reads `_solution`. Sanctioned path: set square patch and assert differing X/Y jaw gap labels.
- `TestTheJawsAreImagedOntoTheSample.test_moving_the_slit_plane_moves_the_magnification` — **REWRITE** — Covers slit plane fraction effect on magnification, but calls `tab._recompute()` and reads `_solution`. Sanctioned path: change slit fraction spinbox and assert magnification label text updates.
- `TestBladePositions.test_all_four_blades_are_named_in_motor_tab_units` — **REWRITE** — Covers blade position naming matching motor tab, but calls `_recompute()`. Sanctioned path: assert blade labels in UI.
- `TestBladePositions.test_symmetric_by_default` — **REWRITE** — Covers symmetric default blade positions, but calls `_recompute()` and reads `_solution`. Sanctioned path: assert symmetrical blade positions in UI table.
- `TestBladePositions.test_a_blade_that_would_cross_centre_is_flagged` — **REWRITE** — Covers crossing-centre warning on blade positions, but calls `_recompute()`. Sanctioned path: set invalid blade target and assert warning indicator/text.
- `TestBladePositions.test_apply_is_disabled_without_a_beamline` — **REWRITE** — Covers Apply button disabled state without beamline, but calls `_recompute()`. Sanctioned path: assert `tab.btn_apply.isEnabled()` without beamline.
- `TestBladePositions.test_apply_is_offered_once_a_beamline_is_wired` — **REWRITE** — Covers Apply button enabled state when beamline is wired, but calls `_recompute()`. Sanctioned path: wire beamline and assert `tab.btn_apply.isEnabled()`.
- `TestTheOverscanIsAtTheJaw.test_the_margin_is_k_times_the_slit_plane_fwhm` — **REWRITE** — Covers overscan margin scaling with k * FWHM, but calls `_recompute()` and reads `_solution`. Sanctioned path: assert on overscan margin readout.
- `TestTheOverscanIsAtTheJaw.test_the_beam_width_does_not_change_the_patch` — **REWRITE** — Covers sample patch size independence from beam FWHM, but calls `_recompute()` and reads `_solution`. Sanctioned path: change FWHM spinner and assert sample patch size label remains constant.
- `TestTheOverscanIsAtTheJaw.test_a_wider_beam_costs_amplitude` — **REWRITE** — Covers required amplitude increase for wider beam, but calls `_recompute()`. Sanctioned path: increase FWHM spinner and assert amplitude label increases.
- `TestTheOverscanIsAtTheJaw.test_the_droop_readout_tracks_k` — **REWRITE** — Covers droop readout tracking k, but calls `_recompute()`. Sanctioned path: change k spinner and assert droop label updates.
- `TestTheOverscanIsAtTheJaw.test_the_dose_is_flat_across_the_patch` — **REWRITE** — Covers patch dose flatness verification, but calls `_recompute()` and reads `_solution`. Sanctioned path: assert dose uniformity label/table.
- `TestTheCostOfOverscan.test_transmission_is_reported_for_both_axes_and_combined` — **REWRITE** — Covers transmission reporting per axis and combined, but calls `_recompute()`. Sanctioned path: assert transmission label text.
- `TestTheCostOfOverscan.test_more_overscan_throws_more_beam_away` — **REWRITE** — Covers transmission drop with increased overscan, but calls `_recompute()` and reads `_solution`. Sanctioned path: increase overscan and assert lower transmission on UI label.
- `TestTheCostOfOverscan.test_the_cost_is_named_as_a_trade_not_a_fault` — **REWRITE** — Covers transmission labelling wording, but calls `_recompute()`. Sanctioned path: assert transmission label wording.
- `TestAsymmetry.test_both_offsets_default_off` — **KEEP** — Tests default offset spinbox values are initialized to 0.0.
- `TestAsymmetry.test_the_jaw_offset_moves_the_patch_without_touching_the_drive` — **REWRITE** — Covers jaw offset shifting patch position independently of drive, but calls `_recompute()` and reads `_solution`. Sanctioned path: set offset spinner and assert patch position readout.
- `TestAsymmetry.test_centring_the_sweep_buys_the_amplitude_back` — **REWRITE** — Covers amplitude reduction when sweep is centred, but calls `_recompute()`. Sanctioned path: assert amplitude label reduction when centred.
- `TestAsymmetry.test_the_dc_term_is_spelled_out_as_equal_and_opposite` — **REWRITE** — Covers DC offset term presentation, but calls `_recompute()`. Sanctioned path: assert DC offset readouts.
- `TestAsymmetry.test_an_x_offset_does_not_disturb_y` — **REWRITE** — Covers cross-axis offset isolation, but calls `_recompute()`. Sanctioned path: change X offset and assert Y readouts unchanged.
- `TestTheDriveReadoutSaysWhoseVoltsTheseAre.test_the_generator_line_names_the_channels_and_the_phase` — **REWRITE** — Covers generator drive readout text and phase, but calls `_recompute()`. Sanctioned path: assert generator line text.
- `TestTheDriveReadoutSaysWhoseVoltsTheseAre.test_the_plate_line_gives_both_per_plate_and_differential` — **REWRITE** — Covers plate voltage readout giving both single-plate and differential values, but calls `_recompute()`. Sanctioned path: assert plate voltage line text.
- `TestTheDriveReadoutSaysWhoseVoltsTheseAre.test_vpp_and_differential_kv_are_numerically_equal_but_labelled_apart` — **REWRITE** — Covers distinction between Vpp and differential kV labels, but calls `_recompute()` and reads `_solution`. Sanctioned path: assert labels distinguish Vpp and differential kV.
- `TestTheDriveReadoutSaysWhoseVoltsTheseAre.test_over_rating_is_flagged_on_the_axis_that_exceeds_it` — **REWRITE** — Covers plate voltage over-rating warning badge, but calls `_recompute()`. Sanctioned path: set large sweep and assert warning badge on exceeding axis.
- `TestTheDriveReadoutSaysWhoseVoltsTheseAre.test_the_generator_rail_is_checked_in_generator_volts` — **REWRITE** — Covers generator voltage rail check in generator units, but calls `_recompute()`. Sanctioned path: set out-of-range drive and assert generator rail warning.
- `TestModeSwitch.test_it_opens_in_slit_limited_mode` — **KEEP** — Tests initial combobox mode defaults to slit-limited mode.
- `TestModeSwitch.test_steerer_mode_says_the_jaws_are_a_floor_not_a_setting` — **KEEP** — Tests switching to steerer mode updates jaw role label text.
- `TestModeSwitch.test_steerer_mode_still_sizes_from_the_sample_edge` — **REWRITE** — Covers sweep sizing from sample edge in steerer mode, but calls `_recompute()` and reads `_solution`. Sanctioned path: select steerer mode and assert sweep readouts.
- `TestModeSwitch.test_slit_mode_needs_less_amplitude_than_steerer_mode` — **REWRITE** — Covers comparison of required amplitude between slit mode and steerer mode, but calls `_recompute()`. Sanctioned path: compare amplitude readouts between modes.
- `TestModeSwitch.test_transmission_is_not_claimed_in_steerer_mode` — **KEEP** — Tests selecting steerer mode displays placeholder ('—') for transmission efficiency.
- `TestTheBeamlineTable.test_it_has_a_row_for_each_slit_pair_and_the_sample` — **REWRITE** — Covers beamline summary table row structure, but calls `_recompute()`. Sanctioned path: assert table row count and header text.
- `TestTheBeamlineTable.test_the_slit_rows_say_where_to_put_the_jaws` — **REWRITE** — Covers slit row target jaw values in beamline table, but calls `_recompute()`. Sanctioned path: assert slit row table cell text.
- `TestTheBeamlineTable.test_the_alumina_only_appears_when_it_is_offset` — **REWRITE** — Covers alumina screen row display only when offset, but calls `_recompute()`. Sanctioned path: toggle alumina offset and assert table row visibility.
- `TestTheBeamlineTable.test_the_alumina_sees_a_slightly_bigger_patch_than_the_sample` — **REWRITE** — Covers beam patch size at alumina screen, but calls `_recompute()`. Sanctioned path: assert alumina patch cell value.
- `TestTheBeamlineTable.test_the_drift_tube_is_checked_against_its_bore` — **REWRITE** — Covers drift tube clearance checking, but calls `_recompute()`. Sanctioned path: assert drift tube clearance cell text.
- `TestTheBeamlineTable.test_a_sweep_that_paints_the_drift_tube_is_warned_about` — **REWRITE** — Covers drift tube collision warning, but calls `_recompute()`. Sanctioned path: set oversized sweep and assert drift tube warning style/text.
- `TestSpeciesTable.test_opens_on_the_sheets_four_species` — **KEEP** — Tests species table contains the standard 4 ion species.
- `TestSpeciesTable.test_the_selected_species_lands_exactly_on_the_target` — **REWRITE** — Covers species selection targeting, but calls `_recompute()` and reads `_solution`. Sanctioned path: select species and assert patch size readout.
- `TestSpeciesTable.test_a_higher_charge_state_sweeps_further_at_the_same_voltage` — **REWRITE** — Covers charge-state effect on required deflection voltage, but calls `_recompute()`. Sanctioned path: select higher charge state and assert lower required voltage on UI label.
- `TestSpeciesTable.test_mass_does_not_affect_deflection` — **REWRITE** — Covers mass independence in species table selection, but calls `_recompute()`. Sanctioned path: compare voltage readouts for isotopes.
- `TestSpeciesTable.test_selecting_a_different_species_redesigns_the_drive` — **REWRITE** — Covers drive recalculation upon species change, but calls `_recompute()`. Sanctioned path: select species and assert updated drive parameters.
- `TestSpeciesTable.test_an_unparseable_row_does_not_raise` — **REWRITE** — Covers error handling for malformed species entries, but calls `_recompute()`. Sanctioned path: edit species table with invalid text and assert graceful error display.
- `TestEnvelopeCheck.test_low_frequency_low_amplitude_is_inside` — **REWRITE** — Covers amplifier operating envelope check, but calls `_recompute()`. Sanctioned path: set low freq/amp and assert safe envelope status label.
- `TestEnvelopeCheck.test_it_names_the_binding_channel` — **REWRITE** — Covers identifying the binding channel in envelope readout, but calls `_recompute()`. Sanctioned path: assert binding channel name in envelope label.
- `TestEnvelopeCheck.test_envelope_shrinks_at_higher_frequency` — **REWRITE** — Covers envelope frequency derating, but calls `_recompute()`. Sanctioned path: increase frequency and assert envelope headroom readout.
- `TestEnvelopeCheck.test_beyond_bandwidth_wall_is_flagged` — **REWRITE** — Covers bandwidth wall warning, but calls `_recompute()`. Sanctioned path: set frequency beyond limit and assert warning status text.
- `TestFrequencyIsNotGeometry.test_frequency_does_not_change_the_required_voltage` — **REWRITE** — Covers frequency independence of spatial deflection voltage, but calls `_recompute()`. Sanctioned path: change frequency spinner and assert voltage label is unchanged.
- `TestFrequencyIsNotGeometry.test_frequency_does_change_the_predicted_current` — **REWRITE** — Covers capacitive load current scaling with frequency, but calls `_recompute()`. Sanctioned path: change frequency spinner and assert current label updates.
- `TestTheProfilerIsOfferedNeverAdopted.test_without_a_profiler_the_button_is_dead` — **REWRITE** — Covers profiler adopt button disabled when profiler disconnected, but calls `_recompute()`. Sanctioned path: assert button enabled state without profiler snapshot.
- `TestTheProfilerIsOfferedNeverAdopted.test_a_reading_is_shown_but_not_taken` — **REWRITE** — Covers profiler FWHM advisory display without auto-adoption, but calls `_use_profiler_fwhm` and `_recompute()`. Sanctioned path: emit profiler snapshot and assert FWHM offered in UI without auto-applying.
- `TestTheProfilerIsOfferedNeverAdopted.test_it_says_to_measure_with_the_raster_off` — **KEEP** — Tests advisory label text instructing operator to measure beam width with raster off.
- `TestCapacitanceRefresh.test_refresh_picks_up_a_measurement_taken_after_construction` — **REWRITE** — Covers capacitance reload on button refresh, but calls `_recompute()`. Sanctioned path: update store, click refresh button, and assert table text.
- `TestCapacitanceRefresh.test_on_plates_wins_over_a_later_disconnected_sweep` — **REWRITE** — Covers capacitance priority selection, but accesses private `tab._channel_capacitance`. Sanctioned path: assert selected capacitance priority in table/label.
- `TestCapacitanceRefresh.test_a_disconnected_only_channel_says_which_condition_it_is` — **REWRITE** — Covers disconnected capacitance status reporting, but accesses private `tab._channel_capacitance`. Sanctioned path: assert condition text in UI table.
- `TestGoneForGood.test_no_steerer_picker` — **DELETE** — Asserts `not hasattr(tab, "cmb_steerer")`; churn test asserting negative attribute existence on legacy deleted widget.
- `TestGoneForGood.test_no_single_capacitance_channel_picker` — **DELETE** — Asserts `not hasattr(tab, "cmb_cap_channel")`; churn test checking dead legacy attribute.
- `TestGoneForGood.test_no_lissajous_readout` — **DELETE** — Asserts `not hasattr(tab, "lbl_lissajous")`; churn test checking dead legacy attribute.
- `TestGoneForGood.test_no_bare_unlabelled_kv_readouts` — **DELETE** — Asserts `not hasattr(tab, "lbl_v_x")` and `"lbl_v_y"`; churn test checking dead legacy attributes.
- `TestGoneForGood.test_no_half_width_boxes` — **DELETE** — Asserts `not hasattr(tab, "spn_half_w")` and `"spn_half_h"`; churn test checking dead legacy attributes.
- `TestThePlaneTableIsHonestAtTheJaw.test_the_slit_row_shows_its_own_axis_clipped` — **REWRITE** — Covers plane table clipping display, but calls `_recompute()` and reads `_solution`. Sanctioned path: assert clipped axis values in plane table.
- `TestThePlaneTableIsHonestAtTheJaw.test_an_x_aperture_is_never_applied_to_the_y_axis` — **REWRITE** — Covers aperture axis independence in plane table, but calls `_recompute()`. Sanctioned path: change X aperture and assert Y plane table cells unaffected.
- `TestTheBeamFrameIsNotTheSlitFrame.test_the_beam_centre_defaults_to_concentric` — **KEEP** — Tests default spinbox values for beam center offset are 0.0 mm.
- `TestTheBeamFrameIsNotTheSlitFrame.test_a_beam_offset_moves_only_the_blade_numbers` — **REWRITE** — Covers beam center offset effect on mechanical blade positions, but calls `_recompute()` and reads `_solution`. Sanctioned path: set beam offset spinner and assert blade position table.
- `TestTheBeamFrameIsNotTheSlitFrame.test_the_blade_readout_is_in_the_slits_own_frame` — **REWRITE** — Covers mechanical slit frame blade readout, but accesses `_mechanical_blades`, `_recompute`, and `_solution`. Sanctioned path: assert blade table values in mechanical frame.
- `TestTheBeamFrameIsNotTheSlitFrame.test_an_uneven_y_pair_is_untouched_by_an_x_beam_offset` — **REWRITE** — Covers cross-axis independence of beam frame offsets, but accesses `_mechanical_blades`, `_recompute`, and `_solution`. Sanctioned path: assert Y blade table values unaffected by X beam offset.
- `TestTheBeamFrameIsNotTheSlitFrame.test_apply_commands_the_mechanical_numbers_not_the_beam_frame_ones` — **REWRITE** — Covers Apply Slits commanding mechanical slit coordinates, but calls `_apply_slits` and `_recompute`. Sanctioned path: click Apply Slits and assert commanded positions on `Beamline`.
- `TestTheBeamFrameIsNotTheSlitFrame.test_a_beam_offset_never_moves_the_picture` — **REWRITE** — Covers beamline plot remaining centered when beam moves, but accesses `_ax_line_x` and `_recompute`. Sanctioned path: assert plot lines remain centred via plot canvas or public visual export.
- `TestTheBeamFrameIsNotTheSlitFrame.test_a_patch_offset_DOES_move_the_picture` — **REWRITE** — Covers patch offset updating beamline plot rendering, but accesses `_recompute` and `_solution`. Sanctioned path: set patch offset and assert patch centre in UI table / plot.
- `TestTheBeamFrameIsNotTheSlitFrame.test_a_blade_driven_past_mechanical_centre_is_flagged` — **REWRITE** — Covers mechanical center crossing warning, but calls `_recompute`. Sanctioned path: set excessive offset and assert warning text/color on blade table.
- `TestTheJawPanel.test_it_has_its_own_axes_not_an_inset` — **REWRITE** — Covers jaw panel standalone canvas structure, but reads `tab._ax_jaw_x` and `tab._ax_line_x`. Sanctioned path: assert jaw panel canvas exists as distinct widget in layout.
- `TestTheJawPanel.test_it_draws_the_beam_at_the_jaw_and_at_the_turnaround` — **REWRITE** — Covers drawing beam at jaw and turnaround, but reads `tab._ax_jaw_x` and calls `_recompute`. Sanctioned path: assert jaw plot lines via public plot handles or visual snapshot.
- `TestTheJawPanel.test_it_dimensions_the_overscan_in_beam_widths` — **REWRITE** — Covers overscan dimension annotation in beam widths, but reads `tab._ax_jaw_x` and calls `_recompute`. Sanctioned path: assert overscan dimension label.
- `TestTheJawPanel.test_the_beamline_plot_dimensions_the_patch_at_the_sample` — **REWRITE** — Covers beamline plot sample patch dimension annotation, but reads `tab._ax_line_x` and calls `_recompute`. Sanctioned path: assert patch dimension readout.
- `TestTheJawPanel.test_steerer_mode_has_no_jaw_panel_to_draw` — **REWRITE** — Covers jaw panel hiding in steerer mode, but reads `tab._ax_jaw_x`. Sanctioned path: select steerer mode and assert jaw panel hidden/disabled via public widget state.
- `TestTheJawPanel.test_the_beam_marker_is_a_capped_bar_not_a_filled_box` — **REWRITE** — Covers beam marker visual styling, but reads `tab._ax_line_x` and calls `_recompute`. Sanctioned path: assert beam marker rendering.
- `TestTheJawPanel.test_the_panel_shades_the_OPPOSITE_jaw_when_the_zoom_reaches_it` — **REWRITE** — Covers opposite jaw shading on zoom, but reads `tab._ax_jaw_x`, `_recompute`, and `_solution`. Sanctioned path: assert jaw shading artists on canvas.
- `TestTheJawPanel.test_a_shallow_zoom_shows_only_the_near_jaw` — **REWRITE** — Covers shallow zoom showing near jaw only, but reads `tab._ax_jaw_y` and calls `_recompute`. Sanctioned path: assert jaw plot limits.
- `TestTheJawPanel.test_the_beam_axis_is_marked_so_a_negative_reading_reads` — **REWRITE** — Covers beam axis marker display, but reads `tab._ax_jaw_x` and calls `_recompute`. Sanctioned path: assert beam axis marker line on jaw plot.
- `TestTheJawPanel.test_the_scale_says_it_is_measured_from_the_beam` — **REWRITE** — Covers jaw plot axis measurement label, but reads `tab._ax_jaw_x` and calls `_recompute`. Sanctioned path: assert X-axis label text on jaw plot canvas.

---

## tests/test_recording_panel.py

Production module read: `src/rbl/gui/widgets/recording_panel.py`

- `test_panel_constructs_without_error` — **DELETE** — Asserts `p is not None` on a constructed widget; churn test that verifies no functional invariant and would not catch any real defect.
- `test_start_session_enabled_with_no_camera` — **REWRITE** — Covers START SESSION button enablement when camera is closed, but reads private `p._btn_session`. Sanctioned path: find public button by text 'START SESSION' (or public property) and assert `btn.isEnabled()`.
- `test_record_video_checkbox_disabled_with_camera_closed` — **REWRITE** — Covers 'Record video' checkbox disabled when camera is closed, but reads private `p._chk_video`. Sanctioned path: find checkbox child by text 'Record video' (or public property) and assert `chk.isEnabled()` is False.
- `test_settings_disable_while_recording` — **REWRITE** — Covers settings controls disabling during active recording session, but reads private `_spin_rec_fps`, `_spin_csv`, `_combo_seg`. Sanctioned path: start recording on recorder stub and assert control enablement via public widget queries.
- `test_settings_reenable_after_stop` — **REWRITE** — Covers settings controls re-enabling after session stop, but reads private `_spin_rec_fps`, `_spin_csv`. Sanctioned path: cycle recording start/stop and assert public controls are re-enabled.

---

## tests/test_regulation_monitor.py

Production module read: `src/rbl/services/regulation_monitor.py`

- `TestDebounce.test_single_bad_window_does_not_fire` — **KEEP** — Tests debounce filter: single out-of-regulation window does not emit `fault_detected` signal.
- `TestDebounce.test_two_consecutive_bad_windows_confirm` — **KEEP** — Tests fault confirmation: two consecutive bad windows emit `fault_detected` signal with ('X+', 'amp_off').
- `TestDebounce.test_does_not_refire_every_window_once_confirmed` — **KEEP** — Tests fault latching: confirmed fault does not re-emit signal on every subsequent bad window.
- `TestDebounce.test_a_healthy_window_resets_the_debounce_count` — **KEEP** — Tests debounce reset: an intermittent healthy window clears bad window counter.
- `TestRampCoupling.test_ramping_channel_never_fires` — **KEEP** — Tests ramp decoupling: channel actively ramping under `RampEngine` never trips regulation monitor.
- `TestRampCoupling.test_post_ramp_blank_windows_absorb_settling` — **KEEP** — Tests post-ramp blanking window budget absorbing transient settling voltages.
- `TestRampCoupling.test_fires_after_blank_window_budget_spent` — **KEEP** — Tests fault detection fires once post-ramp blank window budget is exhausted and bad windows persist.
- `TestRampCoupling.test_no_ramp_engine_never_suspends` — **KEEP** — Tests regulation monitor operating without ramp engine never suspends fault evaluation.
- `TestReset.test_reset_one_label_clears_only_that_label` — **REWRITE** — Covers single-channel fault monitor reset, but inspects private `monitor._consec`. Sanctioned path: call `reset('X+')` and assert behavioral reset via `evaluate()` signal emission timing.
- `TestReset.test_reset_all_clears_everything` — **REWRITE** — Covers global monitor reset, but inspects private `monitor._consec`. Sanctioned path: call `reset()` and assert debounce reset behavior across all channels via `evaluate()`.
- `TestIdleAndOk.test_idle_below_arm_threshold_never_fires` — **KEEP** — Tests idle evaluation below arming threshold returns 'idle' and emits no faults.
- `TestIdleAndOk.test_healthy_window_returns_ok` — **KEEP** — Tests healthy window evaluation returns 'ok'.

---

## tests/test_regulation_response.py

Production module read: `src/rbl/services/regulation_response.py`

- `TestRegulationResponder.test_stops_the_channel_on_fault` — **KEEP** — Tests `RegulationResponder` invoking `AmpDrive.output_off(label)` upon receiving fault signal from monitor.
- `TestRegulationResponder.test_logs_operator_answer_and_conditions_to_trip_history` — **KEEP** — Tests trip history logging with operator dialog answer and beamline operating conditions.
- `TestRegulationResponder.test_output_off_failure_does_not_prevent_dialog_and_logging` — **KEEP** — Tests fault handler resilience: hardware `output_off` failure does not prevent operator dialog and trip logging.
- `TestRegulationResponder.test_get_operating_conditions_failure_still_logs_the_core_fields` — **KEEP** — Tests fault handler resilience: failure while querying operating conditions still logs core trip record.

---

## tests/test_scope_acquisition_and_ports.py

Production module read: `src/rbl/gui/profiler_tab.py, src/rbl/hardware/scope_worker.py, src/rbl/hardware/scope_device.py`

- `TestProbeCandidates.test_every_serial_instrument_is_listed` — **KEEP** — Tests serial instrument lookup candidate key table completeness.
- `TestProbeCandidates.test_single_key_narrows_the_scan` — **KEEP** — Tests serial probe filter narrowing candidates for single instrument key.
- `TestProbeCandidates.test_several_keys_keep_table_order` — **KEEP** — Tests serial probe filter preserving table order across multiple keys.
- `TestProbeCandidates.test_an_unknown_key_probes_for_nothing` — **KEEP** — Tests serial probe filter returning empty list for unrecognized instrument key.
- `TestAcquisitionMode.test_starts_idle_not_free_running` — **REWRITE** — Covers initial idle mode on scope worker, but reads `tab._worker`. Sanctioned path: query public worker acquisition state via Beamline / tab interface.
- `TestAcquisitionMode.test_no_shot_is_pending_on_construction` — **REWRITE** — Covers initial pending shot status, but reads `tab._worker`. Sanctioned path: query public shot pending status.
- `TestAcquisitionMode.test_request_shot_latches` — **REWRITE** — Covers single-shot request latching, but reads `tab._worker`. Sanctioned path: call public `request_shot()` and assert signal/status.
- `TestAcquisitionMode.test_a_shot_is_consumed_exactly_once` — **REWRITE** — Covers single-shot consumption, but reads `tab._worker` and `_snapshot_settings`. Sanctioned path: process shot and verify snapshot produced exactly once.
- `TestAcquisitionMode.test_repeated_requests_do_not_queue_up` — **REWRITE** — Covers deduplication of shot requests, but reads `tab._worker` and `_snapshot_settings`. Sanctioned path: request multiple shots and verify single execution.
- `TestAcquisitionMode.test_continuous_can_be_turned_on_and_off` — **REWRITE** — Covers continuous acquisition toggling, but reads `tab._worker` and `_snapshot_settings`. Sanctioned path: toggle continuous mode via public worker interface.
- `TestAcquisitionMode.test_mode_does_not_disturb_analysis_settings` — **REWRITE** — Covers analysis settings preservation across mode changes, but reads `tab._worker` and `_snapshot_settings`. Sanctioned path: change mode and assert public ScopeState analysis settings.
- `TestAcquisitionMode.test_a_shot_survives_a_failed_connect` — **REWRITE** — Covers pending shot survival across failed connection attempts, but reads `tab._worker` and `_snapshot_settings`. Sanctioned path: assert shot persistence via public state.
- `TestAcquisitionMode.test_idle_snapshot_is_connected_but_carries_no_measurement` — **KEEP** — Pure data model test: verifies `ScopeState(connected=True)` defaults.
- `TestAcquisitionMode.test_snapshot_defaults_keep_old_constructions_working` — **KEEP** — Pure data model test: verifies backward compatibility of `ScopeState` constructor.
- `TestRecordSlice.test_full_record_is_the_whole_thing` — **KEEP** — Tests pure mathematics in record slice helper `_slice_for()`.
- `TestRecordSlice.test_centre_anchor_keeps_the_middle` — **KEEP** — Tests pure mathematics in centre anchor slicing in `_slice_for()`.
- `TestRecordSlice.test_start_anchor_keeps_the_left_edge` — **KEEP** — Tests pure mathematics in start anchor slicing in `_slice_for()`.
- `TestRecordSlice.test_slice_length_always_matches_the_request` — **KEEP** — Tests slice length consistency in `_slice_for()`.
- `TestRecordSlice.test_oversized_request_is_clamped_to_the_record` — **KEEP** — Tests record boundary clamping in `_slice_for()`.
- `TestRecordSlice.test_settings_reach_the_snapshot` — **REWRITE** — Covers record slice settings reaching snapshot, but reads `tab._worker` and `_snapshot_settings`. Sanctioned path: set slice parameters and verify in public ScopeState.
- `TestRecordSlice.test_poll_interval_is_settable_and_never_negative` — **REWRITE** — Covers poll interval setting and clamping, but reads `tab._worker` and `_snapshot_settings`. Sanctioned path: set poll interval and verify in public ScopeState.
- `TestProfilerTabDefaults.test_mode_defaults_to_single_shot` — **REWRITE** — Covers mode combobox default selection, but reads private `tab._cb_mode`. Sanctioned path: query combobox by objectName or public UI property.
- `TestProfilerTabDefaults.test_interval_is_disabled_until_continuous` — **REWRITE** — Covers interval dropdown enablement tracking continuous mode, but reads `tab._cb_mode` and `tab._cb_poll`. Sanctioned path: interact via public comboboxes and assert `isEnabled()`.
- `TestProfilerTabDefaults.test_take_shot_is_disabled_until_connected` — **REWRITE** — Covers Take Shot button disabled when disconnected, but reads `tab._btn_shot`. Sanctioned path: query button enablement via public property or child search.
- `TestProfilerTabDefaults.test_take_shot_while_disconnected_says_so_and_does_not_hang` — **REWRITE** — Covers Take Shot click handling while disconnected, but calls `_on_take_shot` and reads `_lbl_status`, `_shot_pending`. Sanctioned path: click Take Shot button and assert `tab.lbl_status.text()`.
- `TestProfilerTabDefaults.test_an_idle_snapshot_does_not_dirty_the_plot` — **REWRITE** — Covers idle snapshot ignoring plot dirty flag, but calls `_on_scope_state` and reads `_plot_dirty`, `_readout_dirty`. Sanctioned path: push idle snapshot via Beamline and assert plot redraw is not triggered.
- `TestProfilerTabDefaults.test_a_measurement_dirties_the_plot_once` — **REWRITE** — Covers measurement snapshot triggering single plot redraw, but calls `_on_scope_state` and reads `_plot_dirty`. Sanctioned path: push measurement snapshot via Beamline and assert plot is updated.
- `TestIdleSnapshotsDoNotEraseTheMeasurement.test_an_idle_snapshot_does_not_erase_the_last_trace` — **REWRITE** — Covers measurement trace preservation during idle pings, but calls `_on_scope_state` and reads `_last_measured`. Sanctioned path: push measurement then idle snapshot via Beamline and assert waveform trace is retained.
- `TestIdleSnapshotsDoNotEraseTheMeasurement.test_a_shot_followed_by_idle_still_draws_the_waveform` — **REWRITE** — Covers waveform plot canvas retention after shot, but calls `_on_scope_state` and reads `_ax_wave`. Sanctioned path: push measurement then idle snapshot and assert waveform lines on canvas.
- `TestIdleSnapshotsDoNotEraseTheMeasurement.test_an_error_snapshot_does_not_arm_an_empty_redraw` — **REWRITE** — Covers error snapshot preventing empty plot redraws, but calls `_on_scope_state` and reads `_plot_dirty`. Sanctioned path: push error snapshot and assert existing plot display is maintained.
- `TestIdleSnapshotsDoNotEraseTheMeasurement.test_idle_snapshots_do_not_pad_the_fwhm_history` — **REWRITE** — Covers FWHM history filtering out idle snapshots, but calls `_on_scope_state` and reads `_fwhm_history`. Sanctioned path: push idle snapshots and query public FWHM history length / plot.
- `TestIdleSnapshotsDoNotEraseTheMeasurement.test_the_widths_survive_the_idle_ping` — **REWRITE** — Covers width label readout survival across idle pings, but calls `_on_scope_state` and reads `_lbl_axis_value`, `_lbl_status`. Sanctioned path: push idle snapshot and assert width readout label text.
- `TestShotCannotStickForever.test_the_measure_step_catches_more_than_transport_errors` — **DELETE** — Performs source-code string inspection using `inspect.getsource(ScopeWorker._run_loop)`; churn test that verifies implementation syntax rather than runtime behavior.
- `TestShotCannotStickForever.test_run_never_lets_an_exception_escape_the_thread` — **KEEP** — Tests worker thread exception safety: forces runtime error in worker loop and asserts exception is caught and error signal emitted.
- `TestShotCannotStickForever.test_an_error_ends_a_pending_shot` — **REWRITE** — Covers error handling resetting pending shot state, but calls `_on_scope_error` and reads `_shot_pending`, `_btn_shot`. Sanctioned path: emit scope error signal and assert shot button is re-enabled.
- `TestShotCannotStickForever.test_the_watchdog_re_arms_a_shot_that_never_landed` — **REWRITE** — Covers watchdog timeout re-arming hung single shot, but calls `_on_shot_timeout` and reads `_shot_pending`, `_btn_shot`, `_lbl_status`. Sanctioned path: trigger watchdog timeout signal and assert status text and button re-enablement.
- `TestShotCannotStickForever.test_the_watchdog_does_not_fire_when_nothing_is_pending` — **REWRITE** — Covers watchdog timeout when idle, but calls `_on_shot_timeout` and reads `_lbl_status`. Sanctioned path: trigger watchdog timeout when idle and assert status text remains unchanged.

---

## tests/test_session_recorder.py

Production module read: `src/rbl/services/session_recorder.py`

- `test_csv_only_session_creates_expected_files` — **KEEP** — Starts session recorder, stops it, and verifies `data.csv`, `events.csv`, and `session.json` exist on disk.
- `test_t_rel_s_is_non_decreasing` — **KEEP** — Verifies monotonicity of elapsed time column (`t_rel_s`) in session output CSV.
- `test_wall_utc_derived_not_sampled` — **REWRITE** — Covers UTC timestamp derivation in session CSV, but reads private `rec._t0_wall`. Sanctioned path: verify timestamps in `data.csv` relative to session start time recorded in `session.json` without inspecting internal `_t0_wall`.
- `test_schema_roll_creates_two_parts` — **REWRITE** — Covers automatic CSV file segmentation when snapshot schema changes, but manually invokes private `rec._write_csv_row()`. Sanctioned path: update snapshot provider callback and let session capture write `data_002.csv`.
- `test_session_json_has_no_instrument_readouts` — **KEEP** — Verifies session manifest JSON format excludes raw instrument stream data.
- `test_add_note_writes_events_csv` — **KEEP** — Calls public `rec.add_note()` and verifies correctly escaped note entry is written to `events.csv`.
- `test_flush_per_row_survives_hard_kill` — **REWRITE** — Covers CSV row flush durability on ungraceful process kill, but calls `_write_csv_row()` and closes private `_csv_writer`, `_events_file`. Sanctioned path: write rows via public timer/snapshot triggers and verify CSV readability.

---

## tests/test_setpoint_sync.py

Production module read: `src/rbl/state/setpoints.py, src/rbl/gui/funcgen_tab.py, src/rbl/gui/overview_tab.py`

- `test_update_signals_only_on_a_real_change` — **KEEP** — Tests `FuncGenSetpoints` state model signal emission on value updates (and suppression on identical values).
- `test_update_axis_refuses_to_touch_phase` — **KEEP** — Tests `FuncGenSetpoints.update_axis()` preserving differential 0 deg / 180 deg phase invariant.
- `test_axis_matched_reports_a_split_pair` — **KEEP** — Tests `FuncGenSetpoints.axis_matched()` detecting split channel amplitudes.
- `test_defaults_are_a_safe_triangle_at_no_volts` — **KEEP** — Tests safe defaults in `FuncGenSetpoints` data model.
- `test_overview_edit_reaches_the_funcgen_panels` — **KEEP** — Exemplary cross-tab agreement test: edits `OverviewTab` axis spinner and asserts updated Vpp in `FuncGenTab` panel spinner.
- `test_funcgen_edit_reaches_the_overview_boxes` — **KEEP** — Exemplary cross-tab agreement test: edits `FuncGenTab` channel spinner and asserts updated Vpeak in `OverviewTab` axis spinner.
- `test_the_unit_conversion_round_trips` — **KEEP** — Cross-tab round-trip agreement: verifies values typed on either tab survive round-trip conversion without drift.
- `test_a_sync_does_not_echo_back_as_an_edit` — **KEEP** — Cross-tab synchronization loop prevention: asserts single signal emission without endless bounce-back.
- `test_offset_and_shape_survive_an_overview_edit` — **KEEP** — Cross-tab parameter preservation: Overview edit preserves waveform shape and DC offset set on FuncGen tab.
- `test_funcgen_tab_still_applies_from_its_own_widgets` — **KEEP** — Verifies `FuncGenTab` panel `get_params()` returns correct native Vpp matching widget.

---

## tests/test_slit_control.py

Production module read: `src/rbl/gui/widgets/slit_control.py`

- `test_move_requests_the_target_box_value` — **KEEP** — Clicks `btn_move` and verifies `move_requested` signal emitted with target spinner value.
- `test_no_step_controls_on_this_widget` — **KEEP** — Design contract test: asserts `SlitControl` on Overview exposes no relative step buttons.
- `test_position_drives_the_bar` — **KEEP** — Calls `set_position()` and verifies public `bar.fraction()` and label text.
- `test_target_box_marks_the_bar_before_anything_moves` — **REWRITE** — Covers target caret marking on bar, but reads private `ctrl.bar.track._target`. Sanctioned path: set target spinner and assert via public bar target fraction property.
- `test_stale_position_reports_no_galil` — **KEEP** — Tests stale position updates status label to 'not connected' and clears bar fraction.
- `test_moving_axis_is_called_out` — **REWRITE** — Covers moving axis status display, but reads private `ctrl.bar.track._color`. Sanctioned path: assert `ctrl.lbl_state.text() == 'moving'` and public bar styling.
- `test_disabled_axis_is_called_out` — **KEEP** — Tests disabled axis updates status label to 'disabled'.
- `test_controls_start_disabled` — **KEEP** — Tests initial disabled state of button and target spinbox on fresh `SlitControl`.
- `test_sync_target_preloads_the_live_position` — **KEEP** — Calls `sync_target_to_position()` and verifies target spinbox is updated with live position.
- `test_sync_target_is_a_noop_without_a_position` — **KEEP** — Tests `sync_target_to_position()` is a no-op when position is stale/missing.

---

## tests/test_slit_cross_screen.py

Production module read: `src/rbl/gui/motor_tab.py, src/rbl/gui/overview_tab.py, src/rbl/state/beamline.py`

- `test_overview_move_reaches_the_command_console` — **KEEP** — Drives move via `Beamline.move_slit()` and asserts move command appears in `MotorTab.console`.
- `test_both_screens_are_told_the_same_achievable_target` — **KEEP** — Cross-screen agreement test: drives move via Beamline and verifies achievable quantised target in spinners on both MotorTab and OverviewTab.
- `test_overview_move_updates_the_stepper_tab_target_box` — **KEEP** — Drives move via Beamline and asserts `MotorTab` axis target spinner is updated.
- `test_overview_move_lands_in_the_stepper_tabs_current_unit` — **KEEP** — Cross-screen unit conversion: Overview move correctly converts to counts when Stepper Motors tab is in counts.
- `test_a_refused_move_is_logged_too` — **KEEP** — Tests refused move when Galil is disconnected is logged in `MotorTab` console.
- `test_stepper_tab_move_marks_the_target_on_the_overview_bar` — **REWRITE** — Covers Stepper Motors tab move updating Overview bar target caret, but calls private `motors.axes["C"]._move_absolute()` and reads `_target`. Sanctioned path: click `motors.axes["C"].btn_move` and assert `overview.slits["Y+"].spn_target.value()` and public bar target fraction.
- `test_stepper_tab_move_still_reaches_the_controller` — **REWRITE** — Covers Stepper Motors tab move dispatch to controller, but calls `_move_absolute()`. Sanctioned path: click `motors.axes["A"].btn_move` and assert Galil moves received.
- `test_stepper_tab_move_is_logged_once_not_twice` — **REWRITE** — Covers console logging deduplication for stepper moves, but calls `_move_absolute()`. Sanctioned path: click `motors.axes["B"].btn_move` and assert single console log entry.
- `test_the_overview_accepts_a_target_the_other_tab_would_accept` — **KEEP** — Tests Overview slit target accepts values up to 22.0 mm matching Stepper Motors tab.

---

## tests/test_video_recorder.py

Production module read: `src/rbl/services/video_recorder.py`

- `test_fps_decimation` — **KEEP** — Offers 300 frames over 10s at record_fps=2 and verifies 20 ± 1 frames are written to video writer.
- `test_no_drift_over_long_run` — **KEEP** — Offers 3600 frames over 3600s at 1 fps and asserts exact frame count written with zero timing drift.
- `test_segment_roll_on_time` — **KEEP** — Verifies segment rolling on time interval and emission of `segment_closed` signals.
- `test_frames_csv_contiguous` — **KEEP** — Verifies `frames.csv` output contains contiguous frame indices and segment frame counters reset on roll.
- `test_size_cap_roll` — **KEEP** — Verifies segment rolling when output file exceeds byte size cap.

---

## tests/test_video_transcoder.py

Production module read: `src/rbl/services/video_transcoder.py`

- `test_find_ffmpeg_returns_none_when_nothing_found` — **KEEP** — Tests `find_ffmpeg()` returns None when ffmpeg binary is not found on PATH or disk.
- `test_find_ffmpeg_no_exception_when_absent` — **DELETE** — Duplicate churn test: calls `find_ffmpeg()` in a try/except, testing nothing beyond the preceding test.
- `test_queue_emits_failed_when_ffmpeg_absent` — **REWRITE** — Covers transcode failure when ffmpeg is missing, but invokes private `tq._transcode(avi)`. Sanctioned path: submit task via public transcode queue interface and assert on `finished_one` signal.
- `test_command_contains_required_flags` — **REWRITE** — Covers ffmpeg subprocess argument construction, but invokes `tq._transcode(avi)`. Sanctioned path: submit task via public queue API and inspect arguments passed to `subprocess.run`.
- `test_failed_transcode_leaves_avi_intact` — **REWRITE** — Covers source AVI preservation when transcode fails, but invokes `tq._transcode(avi)`. Sanctioned path: submit task and assert source file is preserved on disk.
- `test_part_file_renamed_on_success` — **REWRITE** — Covers atomic rename of `.mp4.part` to `.mp4` on success, but invokes `tq._transcode(avi)`. Sanctioned path: submit task and assert completed `.mp4` file.

---

## tests/test_widgets_mini.py

Production module read: `src/rbl/gui/widgets/mini.py`

- `TestValueTile.test_initial_value_is_placeholder` — **KEEP** — Tests `ValueTile` initial placeholder text (`lbl_value.text() == '—'`).
- `TestValueTile.test_set_formats_value_with_unit` — **KEEP** — Tests `ValueTile.set()` formatting value with unit string.
- `TestValueTile.test_set_none_shows_placeholder` — **KEEP** — Tests `ValueTile.set(None)` resets label to placeholder.
- `TestValueTile.test_nan_shows_placeholder_not_a_number` — **KEEP** — Tests `ValueTile.set(float('nan'))` renders placeholder rather than 'nan'.
- `TestValueTile.test_stale_changes_style` — **KEEP** — Tests `ValueTile.set(stale=True)` applies muted theme styling.
- `TestMiniBar.test_value_within_range_sets_fraction` — **REWRITE** — Covers `MiniBar` fraction calculation, but reads private `bar.track._frac`. Sanctioned path: assert via public `bar.fraction()` and `bar.lbl_value.text()`.
- `TestMiniBar.test_value_clamped_below_minimum` — **KEEP** — Tests `MiniBar.set()` clamping negative values to fraction 0.0.
- `TestMiniBar.test_value_clamped_above_maximum` — **KEEP** — Tests `MiniBar.set()` clamping above-maximum values to fraction 1.0.
- `TestMiniBar.test_none_value_has_no_fraction` — **REWRITE** — Covers `MiniBar.set(None)`, but reads private `bar.track._frac`. Sanctioned path: assert `bar.fraction() is None` and `bar.lbl_value.text() == '—'`.
- `TestMiniBar.test_nan_value_has_no_fraction` — **KEEP** — Tests `MiniBar.set(float('nan'))` results in `fraction() is None` and placeholder text.
- `TestMiniBar.test_value_label_carries_unit_and_decimals` — **KEEP** — Tests `MiniBar` value label formatted with custom decimals and unit string.
- `TestMiniBar.test_stale_mutes_the_bar_colour` — **REWRITE** — Covers stale color styling on `MiniBar`, but reads private `bar.track._color`. Sanctioned path: assert via public style/state accessor.
- `TestMiniBar.test_role_overrides_the_bar_colour` — **REWRITE** — Covers role color override on `MiniBar`, but reads private `bar.track._color`. Sanctioned path: assert via public style/state accessor.
- `TestMiniBar.test_target_marks_a_setpoint_on_the_same_scale` — **REWRITE** — Covers setpoint target marker calculation on `MiniBar`, but reads private `bar.track._target`. Sanctioned path: assert via public `bar.target_fraction()` or target property.
- `TestMiniBar.test_target_none_clears_the_marker` — **REWRITE** — Covers clearing target marker on `MiniBar`, but reads private `bar.track._target`. Sanctioned path: assert via public target property.
- `TestMiniBar.test_log_scale_places_a_value_by_decade` — **KEEP** — Tests logarithmic decade scaling in `MiniBar` producing exact 0.5 fraction for middle decade.
- `TestMiniBar.test_log_scale_clamps_below_the_floor` — **KEEP** — Tests log-scale fraction clamping below the floor.
- `TestMiniBar.test_log_scale_handles_zero_and_negative` — **KEEP** — Tests log-scale zero and negative input handling without math errors.
- `TestMiniBar.test_custom_formatter_owns_the_value_text` — **KEEP** — Tests custom formatter lambda controlling `lbl_value` text.
- `TestMiniBar.test_scale_captions_label_both_ends_and_the_middle` — **REWRITE** — Covers scale tick caption generation, but reads private `bar.track._tick_labels`. Sanctioned path: assert via public tick labels property or visual layout.
- `TestSparkline.test_initial_value_is_placeholder` — **KEEP** — Tests `Sparkline` initial placeholder text.
- `TestSparkline.test_push_updates_label_and_history` — **REWRITE** — Covers `Sparkline.push()` updating label and data history, but reads private `spark._values`. Sanctioned path: assert `spark.lbl_value.text()` and public history accessor.
- `TestSparkline.test_push_nan_shows_placeholder` — **KEEP** — Tests `Sparkline.push(float('nan'))` renders placeholder text.
- `TestSparkline.test_history_caps_at_max_points` — **REWRITE** — Covers history buffer capacity capping, but sets `spark._max_points` and reads `spark._values`. Sanctioned path: pass capacity in constructor and query public history length.
- `TestSparkline.test_trace_sees_the_same_history` — **REWRITE** — Covers sparkline trace widget data synchronization, but reads private `spark.trace._values`. Sanctioned path: assert via public trace rendering / accessor.

---

## Would have caught nothing (12 tests)

The following tests earned a `delete` verdict because they would not have caught a real defect and impose pure maintenance churn during codebase refactoring:

1. `tests/test_no_scroll_inputs.py::TestNoScrollComboBox.test_a_plain_combo_box_would_have` — Restates standard PySide6/Qt `QComboBox` wheel handling; tests framework behavior rather than project code.
2. `tests/test_no_scroll_inputs.py::TestQuietDoubleSpinBox.test_a_plain_spin_box_would_have` — Restates standard PySide6/Qt `QDoubleSpinBox` wheel handling; tests framework behavior.
3. `tests/test_overview_tab.py::test_redraw_is_a_noop_while_hidden` — Asserts on the internal private `_visible` guard within private `_redraw()`; breaks on UI restructuring without testing functional behavior.
4. `tests/test_overview_tab.py::test_show_event_starts_timer_and_repaints_immediately` — Asserts on internal `_redraw_timer` start within `showEvent()`; tests Qt timer lifecycle plumbing rather than rendered operator output.
5. `tests/test_raster_planner_tab.py::TestGoneForGood.test_no_steerer_picker` — Asserts `not hasattr(tab, 'cmb_steerer')`; negative attribute existence check on a deleted widget that cannot catch runtime faults.
6. `tests/test_raster_planner_tab.py::TestGoneForGood.test_no_single_capacitance_channel_picker` — Asserts `not hasattr(tab, 'cmb_cap_channel')`; negative attribute check.
7. `tests/test_raster_planner_tab.py::TestGoneForGood.test_no_lissajous_readout` — Asserts `not hasattr(tab, 'lbl_lissajous')`; negative attribute check.
8. `tests/test_raster_planner_tab.py::TestGoneForGood.test_no_bare_unlabelled_kv_readouts` — Asserts `not hasattr(tab, 'lbl_v_x')` / `'lbl_v_y'`; negative attribute check.
9. `tests/test_raster_planner_tab.py::TestGoneForGood.test_no_half_width_boxes` — Asserts `not hasattr(tab, 'spn_half_w')` / `'spn_half_h'`; negative attribute check.
10. `tests/test_recording_panel.py::test_panel_constructs_without_error` — Asserts `p is not None` on newly constructed Python instance; Python instantiation never evaluates to None.
11. `tests/test_scope_acquisition_and_ports.py::TestShotCannotStickForever.test_the_measure_step_catches_more_than_transport_errors` — Performs an `inspect.getsource()` text grep looking for `'except Exception'` in `ScopeWorker._run_loop`; tests source code text formatting instead of exception handling execution.
12. `tests/test_video_transcoder.py::test_find_ffmpeg_no_exception_when_absent` — Calls `find_ffmpeg()` in a try/except without raising; exact duplicate of `test_find_ffmpeg_returns_none_when_nothing_found`.
