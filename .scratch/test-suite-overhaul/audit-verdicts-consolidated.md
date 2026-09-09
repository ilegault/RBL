# Consolidated Audit Verdicts & Review Sign-Off (Ticket 11)

**Audit Date:** 2026-09-08  
**Scope:** Complete RBL Test Suite — all 80 test files in `tests/`  
**Reference:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `.scratch/test-suite-overhaul/spec.md`  

---

## 1. Executive Summary & Headline Totals

Across all 80 test modules in the repository, 1,620 individual test functions have been audited under ADR 0001's strict criteria across the 4 batch audits:
- **Batch 07 (Hardware & Pure Math):** 28 files, 748 tests (`audit-07-hardware-and-pure-math.md`)
- **Batch 08 (State, Services & Config):** 20 files, 259 tests (`audit-08-state-services-persistence.md`)
- **Batch 09 (Qt Modules Batch 1):** 17 files, 296 tests (`audit-09-qt-modules-batch-1.md`)
- **Batch 10 (Qt Modules Batch 2):** 15 files, 317 tests (`audit-10-qt-modules-batch-2.md`)

### Verdict Totals

| Verdict | Count | Proportion | Meaning / Action |
| :--- | :---: | :---: | :--- |
| **KEEP** | **1,256** | **77.5%** | Preserved. Drives production path, tests mathematical invariants, or asserts on public operator/consumer output. |
| **REWRITE** | **263** | **16.2%** | Rewritten under Ticket 14. Covers genuine behavior but currently touches private attributes/slots. Rewritten to use `tests/payloads.py` or public Qt signals. |
| **DELETE** | **101** | **6.2%** | Removed under Ticket 14. Churn tests, framework tautologies, private flag checks, or negative assertions on deleted legacy widgets. |
| **TOTAL** | **1,620** | **100.0%** | **Complete coverage across all 80 test files with 0 gaps and 0 overlaps.** |

### Projected Suite Metrics
- **Pre-overhaul Baseline (Ticket 01):** 1,621 test functions (1,675 collected parametrized cases).
- **Projected Post-Deletion Test Function Count:** **1,519** test functions (1,256 Keep + 263 Rewritten).
- **Subsequent Additions:** 4 stream contract test cases (Ticket 15) and 1 end-to-end MainWindow session test (Ticket 16).

---

## 2. Inventory by Batch & File

| Batch | File Name | Total Tests | Keep | Rewrite | Delete |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **07** | `tests/test_raster_model.py` | 25 | 25 | 0 | 0 |
| **07** | `tests/test_slit_raster_model.py` | 41 | 41 | 0 | 0 |
| **07** | `tests/test_raster_plan.py` | 33 | 32 | 0 | 1 |
| **07** | `tests/test_beam_reconstruction.py` | 34 | 33 | 0 | 1 |
| **07** | `tests/test_profile_fwhm.py` | 24 | 23 | 0 | 1 |
| **07** | `tests/test_profile_multipeak.py` | 43 | 43 | 0 | 0 |
| **07** | `tests/test_width_levels.py` | 17 | 17 | 0 | 0 |
| **07** | `tests/test_bpm_calibration.py` | 24 | 24 | 0 | 0 |
| **07** | `tests/test_load_model.py` | 33 | 33 | 0 | 0 |
| **07** | `tests/test_regulation.py` | 35 | 35 | 0 | 0 |
| **07** | `tests/test_edge_metrics.py` | 13 | 13 | 0 | 0 |
| **07** | `tests/test_ac_metrics.py` | 18 | 18 | 0 | 0 |
| **07** | `tests/test_amp_monitor.py` | 23 | 23 | 0 | 0 |
| **07** | `tests/test_amp_drive.py` | 29 | 29 | 0 | 0 |
| **07** | `tests/test_amp_trace.py` | 16 | 16 | 0 | 0 |
| **07** | `tests/test_current_monitor.py` | 14 | 14 | 0 | 0 |
| **07** | `tests/test_hv_interlock.py` | 39 | 39 | 0 | 0 |
| **07** | `tests/test_hv_safety_config.py` | 28 | 28 | 0 | 0 |
| **07** | `tests/test_galil_protocol.py` | 32 | 32 | 0 | 0 |
| **07** | `tests/test_vgc083_parse.py` | 16 | 16 | 0 | 0 |
| **07** | `tests/test_xgs600_parse.py` | 20 | 20 | 0 | 0 |
| **07** | `tests/test_tds_waveform.py` | 31 | 31 | 0 | 0 |
| **07** | `tests/test_labjack_driver.py` | 34 | 34 | 0 | 0 |
| **07** | `tests/test_hardware.py` | 39 | 39 | 0 | 0 |
| **07** | `tests/test_funcgen_driver.py` | 22 | 22 | 0 | 0 |
| **07** | `tests/test_timebase.py` | 16 | 16 | 0 | 0 |
| **07** | `tests/test_waveform_ring.py` | 26 | 26 | 0 | 0 |
| **07** | `tests/test_waveform_period.py` | 23 | 23 | 0 | 0 |
| **08** | `tests/test_beamline.py` | 53 | 48 | 4 | 1 |
| **08** | `tests/test_calibration_config.py` | 12 | 12 | 0 | 0 |
| **08** | `tests/test_calibration_config_load.py` | 6 | 6 | 0 | 0 |
| **08** | `tests/test_calibration_writer.py` | 8 | 8 | 0 | 0 |
| **08** | `tests/test_conditioning_history.py` | 5 | 5 | 0 | 0 |
| **08** | `tests/test_csv_log_writer.py` | 38 | 38 | 0 | 0 |
| **08** | `tests/test_dynamic_adjustment_history.py` | 9 | 9 | 0 | 0 |
| **08** | `tests/test_funcgen_safety.py` | 8 | 8 | 0 | 0 |
| **08** | `tests/test_hv_interlock_link.py` | 12 | 12 | 0 | 0 |
| **08** | `tests/test_labjack_link.py` | 26 | 26 | 0 | 0 |
| **08** | `tests/test_labjack_stream.py` | 14 | 12 | 0 | 2 |
| **08** | `tests/test_layering.py` | 1 | 1 | 0 | 0 |
| **08** | `tests/test_load_calibration_store.py` | 9 | 9 | 0 | 0 |
| **08** | `tests/test_persistence.py` | 4 | 4 | 0 | 0 |
| **08** | `tests/test_snapshot_json.py` | 13 | 13 | 0 | 0 |
| **08** | `tests/test_steerer_geometry.py` | 7 | 7 | 0 | 0 |
| **08** | `tests/test_stream_payload_stats.py` | 6 | 6 | 0 | 0 |
| **08** | `tests/test_tab_persistence.py` | 12 | 2 | 0 | 10 |
| **08** | `tests/test_trip_history.py` | 5 | 5 | 0 | 0 |
| **08** | `tests/test_vacuum_logger.py` | 11 | 11 | 0 | 0 |
| **09** | `tests/test_amp_single_channel.py` | 7 | 1 | 6 | 0 |
| **09** | `tests/test_amp_tab_isolation.py` | 13 | 10 | 3 | 0 |
| **09** | `tests/test_amp_waveform_fidelity.py` | 2 | 2 | 0 | 0 |
| **09** | `tests/test_auto_home.py` | 50 | 32 | 18 | 0 |
| **09** | `tests/test_calibration_app_wiring.py` | 6 | 0 | 5 | 1 |
| **09** | `tests/test_calibration_runner.py` | 28 | 22 | 5 | 1 |
| **09** | `tests/test_camera_tab.py` | 4 | 1 | 2 | 1 |
| **09** | `tests/test_drag_panel.py` | 30 | 0 | 0 | 30 |
| **09** | `tests/test_dynamic_adjustment.py` | 14 | 14 | 0 | 0 |
| **09** | `tests/test_dynamic_adjustment_tab.py` | 12 | 0 | 7 | 5 |
| **09** | `tests/test_funcgen_panel_status.py` | 18 | 13 | 5 | 0 |
| **09** | `tests/test_funcgen_sync.py` | 15 | 2 | 13 | 0 |
| **09** | `tests/test_gui_hardware.py` | 51 | 22 | 21 | 8 |
| **09** | `tests/test_hv_conditioner.py` | 9 | 9 | 0 | 0 |
| **09** | `tests/test_inputs.py` | 10 | 10 | 0 | 0 |
| **09** | `tests/test_load_characterization_tab.py` | 15 | 0 | 10 | 5 |
| **09** | `tests/test_load_characterizer.py` | 12 | 12 | 0 | 0 |
| **10** | `tests/test_no_scroll_inputs.py` | 23 | 21 | 0 | 2 |
| **10** | `tests/test_overview_tab.py` | 63 | 26 | 35 | 2 |
| **10** | `tests/test_ramp_engine.py` | 14 | 12 | 2 | 0 |
| **10** | `tests/test_raster_planner_tab.py` | 87 | 7 | 75 | 5 |
| **10** | `tests/test_recording_panel.py` | 5 | 0 | 4 | 1 |
| **10** | `tests/test_regulation_monitor.py` | 12 | 10 | 2 | 0 |
| **10** | `tests/test_regulation_response.py` | 4 | 4 | 0 | 0 |
| **10** | `tests/test_scope_acquisition_and_ports.py` | 37 | 12 | 24 | 1 |
| **10** | `tests/test_session_recorder.py` | 7 | 4 | 3 | 0 |
| **10** | `tests/test_setpoint_sync.py` | 10 | 10 | 0 | 0 |
| **10** | `tests/test_slit_control.py` | 10 | 8 | 2 | 0 |
| **10** | `tests/test_slit_cross_screen.py` | 9 | 6 | 3 | 0 |
| **10** | `tests/test_video_recorder.py` | 5 | 5 | 0 | 0 |
| **10** | `tests/test_video_transcoder.py` | 6 | 1 | 4 | 1 |
| **10** | `tests/test_widgets_mini.py` | 25 | 15 | 10 | 0 |
| **TOTAL** | **80 Files** | **1,620** | **1,256** | **263** | **101** |

---

## 3. Consolidated "Would Have Caught Nothing" Registry (101 Tests)

All 101 tests designated for `DELETE` fall into clearly demonstrable churn categories:

1. **White-Box Private Layout Internals (30 tests in `test_drag_panel.py`):**
   - Asserts private dictionary keys and internal list rearrangements (`_cols`, `_find`, `_apply_move`) in `DragPanelArea` without exercising rendered UI or layout behavior.
2. **Framework Default Restatements & Tautologies (19 tests):**
   - Asserts standard Qt behaviors like `QStackedWidget` navigation index setting (`test_gui_hardware.py`), PySide6 spinbox scrolling defaults (`test_no_scroll_inputs.py`), or pure `assert p is not None` object existence checks (`test_recording_panel.py`).
3. **Negative Assertions on Long-Deleted Legacy Widgets (5 tests in `test_raster_planner_tab.py`):**
   - `TestGoneForGood` assertions checking `hasattr(tab, 'spn_single_steerer') == False` and similar obsolete attributes.
4. **Reimplemented Fake Threading & Non-Existent Class Tests (10 tests in `test_tab_persistence.py`):**
   - Implements bespoke mockup classes in the test file rather than testing real production persistence services.
5. **Private Flag Mutation & Mock Echoing (37 tests across batches 07, 08, 09, 10):**
   - Writes directly to private flags (`_connected`, `_profile_selector`) and reads them back without driving public production workflows.

---

## 4. Consolidated Rewrite Roadmap (263 Tests)

All 263 tests marked `REWRITE` cover legitimate user/hardware behavior but currently bypass public APIs by poking private methods (`_redraw()`, `_on_stream()`) or mutating private attributes.

Under **Ticket 14**, these will be rewritten strictly via sanctioned paths:
- **Streaming UI Updates:** Driven via `tests/payloads.py` (`LabJackFeed`, real `Beamline` stream windows) asserting on operator-visible text and widget values.
- **Hardware Dispatches:** Driven via `FakeAsyncHardwareManager` or public Qt action signals, avoiding brittle mock call sequences.
- **Tab Navigation:** Using title-based lookup (`main_window.get_tab_by_title(...)`) instead of fragile hardcoded integer indices.

---

## 5. Review & Sign-Off Checkpoint

**Sign-off Status:** APPROVED BY DEVELOPER (2026-09-08)

The consolidated audit verdicts covering all 80 test files (1,256 Keep, 263 Rewrite, 101 Delete) were presented and approved. Tickets 12, 13, and 14 are unblocked for execution.
