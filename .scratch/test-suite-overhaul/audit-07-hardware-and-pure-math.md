# Audit 07 — hardware and pure-mathematics test modules

Ticket: `.scratch/test-suite-overhaul/issues/07-audit-hardware-and-pure-math.md`
Criteria: ADR 0001 vocabulary (`docs/adr/0001-tests-first-and-no-muted-failures.md`), ticket 07's
keep/delete definitions. **No `rewrite` verdict is issued in this batch** (out of scope per the
ticket). **No test file, ADR, or production module was modified while producing this audit.**

## Batch file list (28 files)

The test modules that import only from the hardware and configuration layers and do not import
Qt. This list is exhaustive for ticket 07's batch, recorded here so ticket 11 can confirm the four
audit batches (07/08/09/10) cover every test module with no overlap:

1. `tests/test_raster_model.py`
2. `tests/test_slit_raster_model.py`
3. `tests/test_raster_plan.py`
4. `tests/test_beam_reconstruction.py`
5. `tests/test_profile_fwhm.py`
6. `tests/test_profile_multipeak.py`
7. `tests/test_width_levels.py`
8. `tests/test_bpm_calibration.py`
9. `tests/test_load_model.py`
10. `tests/test_regulation.py`
11. `tests/test_edge_metrics.py`
12. `tests/test_ac_metrics.py`
13. `tests/test_amp_monitor.py`
14. `tests/test_amp_drive.py`
15. `tests/test_amp_trace.py`
16. `tests/test_current_monitor.py`
17. `tests/test_hv_interlock.py`
18. `tests/test_hv_safety_config.py`
19. `tests/test_galil_protocol.py`
20. `tests/test_vgc083_parse.py`
21. `tests/test_xgs600_parse.py`
22. `tests/test_tds_waveform.py`
23. `tests/test_labjack_driver.py`
24. `tests/test_hardware.py`
25. `tests/test_funcgen_driver.py`
26. `tests/test_timebase.py`
27. `tests/test_waveform_ring.py`
28. `tests/test_waveform_period.py`

This is the complement of the Qt-importing and state/services/persistence batches (tickets 08, 09,
10): every `tests/*.py` file that does **not** import Qt (`PySide6`/`QtCore`/`QtWidgets`/`QtGui`/
`qtbot`/`QApplication`) and is **not** one of ticket 08's ~20 state/services/persistence files
(`test_beamline.py`, `test_calibration_config.py`, `test_calibration_config_load.py`,
`test_calibration_writer.py`, `test_conditioning_history.py`, `test_csv_log_writer.py`,
`test_dynamic_adjustment_history.py`, `test_funcgen_safety.py`, `test_hv_interlock_link.py`,
`test_labjack_link.py`, `test_labjack_stream.py`, `test_layering.py`,
`test_load_calibration_store.py`, `test_persistence.py`, `test_snapshot_json.py`,
`test_steerer_geometry.py`, `test_stream_payload_stats.py`, `test_tab_persistence.py`,
`test_trip_history.py`, `test_vacuum_logger.py`) belongs to this batch. 28 + 20 = 48, the full count
of non-Qt test modules (excluding `tests/__init__.py` and the `tests/payloads.py` helper module,
which contain no test functions of their own).

Two files import from `rbl.state`/`rbl.services` despite being scoped into this hardware-math batch
by content rather than strict import layer — flagged explicitly in their sections below:
`tests/test_amp_drive.py` (imports `rbl.services.amp_drive`, but drives it as plain Python through
fake stand-ins, no Qt) and `tests/test_timebase.py` / `tests/test_waveform_period.py` (import
`rbl.state.beamline.Beamline`, used as a harness to get real data into hardware-layer math, or —
in one class — genuinely testing Beamline's own interlock logic; noted per-test).

## Result

**748 test functions audited across 28 files: 723 KEEP, 25 DELETE, 0 REWRITE.**

All tests that pin a formula to the instrument manual's worked example, the laboratory's own
deflection sheet, or invert a planted beam/signal/fiducial trace to recover it, were confirmed as
presumed keepers per the ticket — not re-litigated. All 25 deletes are bloat: either a duplicate of
another test in the same file that would pass or fail together with it (most common), or a
restatement of a guarantee the framework/standard library already makes. None is a churn test in
the "breaks on innocent restructuring" sense beyond that duplication.

| # | File | Total | Keep | Delete |
|---|---|---:|---:|---:|
| 1 | tests/test_raster_model.py | 25 | 25 | 0 |
| 2 | tests/test_slit_raster_model.py | 41 | 41 | 0 |
| 3 | tests/test_raster_plan.py | 33 | 32 | 1 |
| 4 | tests/test_beam_reconstruction.py | 34 | 33 | 1 |
| 5 | tests/test_profile_fwhm.py | 24 | 23 | 1 |
| 6 | tests/test_profile_multipeak.py | 43 | 43 | 0 |
| 7 | tests/test_width_levels.py | 17 | 17 | 0 |
| 8 | tests/test_bpm_calibration.py | 24 | 24 | 0 |
| 9 | tests/test_load_model.py | 13 | 13 | 0 |
| 10 | tests/test_regulation.py | 8 | 8 | 0 |
| 11 | tests/test_edge_metrics.py | 21 | 21 | 0 |
| 12 | tests/test_ac_metrics.py | 51 | 49 | 2 |
| 13 | tests/test_amp_monitor.py | 20 | 20 | 0 |
| 14 | tests/test_amp_drive.py | 29 | 29 | 0 |
| 15 | tests/test_amp_trace.py | 29 | 29 | 0 |
| 16 | tests/test_current_monitor.py | 42 | 40 | 2 |
| 17 | tests/test_hv_interlock.py | 8 | 7 | 1 |
| 18 | tests/test_hv_safety_config.py | 4 | 4 | 0 |
| 19 | tests/test_galil_protocol.py | 52 | 51 | 1 |
| 20 | tests/test_vgc083_parse.py | 20 | 15 | 5 |
| 21 | tests/test_xgs600_parse.py | 13 | 11 | 2 |
| 22 | tests/test_tds_waveform.py | 39 | 39 | 0 |
| 23 | tests/test_labjack_driver.py | 19 | 19 | 0 |
| 24 | tests/test_hardware.py | 26 | 18 | 8 |
| 25 | tests/test_funcgen_driver.py | 57 | 57 | 0 |
| 26 | tests/test_timebase.py | 19 | 18 | 1 |
| 27 | tests/test_waveform_ring.py | 7 | 7 | 0 |
| 28 | tests/test_waveform_period.py | 30 | 30 | 0 |
| | **Total** | **748** | **723** | **25** |

### Flagged for ticket 11 (the review gate), not acted on here

Two `keep` verdicts assert on private state/methods and would normally be `rewrite` candidates —
out of this batch's keep/delete-only scope, so kept here, but named for the reviewer:

- `tests/test_waveform_period.py::TestAlignedWaveHistory::test_empty_windows_do_not_pile_up` —
  drives the real public `push()` API 5000 times but reads the private `hist._windows` deque
  directly (no public accessor for window count exists).
- `tests/test_waveform_period.py::TestBeamlineCycleTraces::test_history_is_dropped_when_the_stream_restarts`
  — real, safety-relevant behaviour, but calls the private `beamline._mark_labjack_disconnected()`
  directly rather than the public `disconnect_labjack()` that calls it in production.

---

## tests/test_raster_model.py

Production module read: `src/rbl/hardware/raster_model.py`.

- `TestAgainstTheManualsWorkedExample::test_deflection_angle` — KEEP — pins `deflection_mrad` to the NEC XY Steerer manual's Section IV worked example (10 kV, 12.5 cm, 3.8 cm, 1 MeV → 0.0164 rad).
- `TestAgainstTheManualsWorkedExample::test_deflection_after_one_metre_of_drift` — KEEP — pins `displacement_mm` to the same manual example's "1.64 cm after 1 m drift" statement.
- `TestAgainstTheLabDeflectionSheet::test_reproduces_the_sheets_proton_row_exactly` — KEEP — pins `displacement_mm` to the lab's own `Hirst RHBL Deflection Information.xlsx` proton row (5.4298 mm), named in the docstring as "the beamline's reference number."
- `TestAgainstTheLabDeflectionSheet::test_displacement_is_theta_times_drift_and_nothing_else` — KEEP — regression guard against re-adding the deliberately-removed l/2 pivot term (see the "NO l/2 PIVOT TERM" note in the production docstring); this is real behaviour, not framework-guaranteed.
- `TestAgainstTheLabDeflectionSheet::test_all_four_species_rows` — KEEP — pins four distinct sheet rows (protons, Al, Ni q=3, Ti q=2) against the lab sheet's own recomputed values; the four cases vary charge state and energy so they do not all fail/pass together on a charge-state-specific bug.
- `TestAgainstTheLabDeflectionSheet::test_per_plate_kv_is_half_the_differential` — KEEP — tests the documented "FACTOR-OF-TWO HAZARD" (per-plate vs differential kV), a real and previously-costly ambiguity per the module docstring.
- `TestDeflection::test_positive_for_positive_voltage` — KEEP — tests the formula's sign directly (hardware-layer math).
- `TestDeflection::test_zero_energy_is_nan` — KEEP — tests the degenerate-input guard branch (`beam_energy_ev <= 0`).
- `TestDeflection::test_linear_in_voltage` — KEEP — tests the formula's linearity in voltage, a real mathematical property.
- `TestDisplacement::test_linear_in_voltage` — KEEP — tests `displacement_mm`'s linearity directly (not merely `deflection_mrad`'s), exercising the drift multiplication too.
- `TestDisplacement::test_zero_energy_is_nan` — KEEP — tests the NaN-propagation branch specific to `displacement_mm` (`math.isnan(theta_rad)` short-circuit).
- `TestRequiredDifferentialKv::test_inverts_displacement_exactly` — KEEP — presumed keeper: inverts `displacement_mm` and recovers the planted half-width exactly.
- `TestRequiredDifferentialKv::test_turnaround_k_is_a_real_parameter` — KEEP — tests that `turnaround_k` actually changes the result (guards against it being ignored).
- `TestDwellUniformity::test_wide_scan_is_uniform_over_narrow_sample` — KEEP — tests real physics output of the dwell-uniformity convolution model.
- `TestDwellUniformity::test_tight_scan_is_less_uniform` — KEEP — comparative physics behaviour, distinct scan width from the previous test.
- `TestDwellUniformity::test_degenerate_inputs_return_nan` — KEEP — tests the degenerate-input guard.
- `TestRequiredDrive::test_full_width_is_halved` — KEEP — tests the documented "WHY FULL WIDTH IN, HALVES INSIDE" behaviour.
- `TestRequiredDrive::test_it_agrees_with_required_differential_kv_on_the_same_geometry` — KEEP — cross-checks two independent production functions agree, a real consistency guarantee.
- `TestRequiredDrive::test_amplitude_actually_sweeps_the_requested_span` — KEEP — round-trips the returned amplitude back through `displacement_mm`.
- `TestRequiredDrive::test_zero_offset_means_zero_dc_and_a_centred_sweep` — KEEP — tests default (no-offset) behaviour of the returned dict.
- `TestRequiredDrive::test_offset_moves_the_interval_without_resizing_it` — KEEP — tests the documented DC-offset design ("WHY AN OFFSET AND NOT TWO AMPLITUDES").
- `TestRequiredDrive::test_the_dc_term_lands_the_centre_where_it_was_asked_for` — KEEP — round-trips the offset term through `displacement_mm`.
- `TestRequiredDrive::test_a_negative_offset_still_costs_headroom` — KEEP — tests the `abs(offset)` behaviour in `peak_kv`, a real asymmetric-cost property.
- `TestRequiredDrive::test_plate_values_are_exactly_half_the_differential` — KEEP — again the factor-of-two hazard, this time for `required_drive`'s returned dict.
- `TestRequiredDrive::test_degenerate_energy_does_not_raise` — KEEP — tests the NaN-safe degenerate branch of the dict-returning function.

**Total: 25 (25 keep, 0 delete)**

---

## tests/test_slit_raster_model.py

Production module read: `src/rbl/hardware/slit_raster_model.py`. This file is unusually tight — every test targets one of the three documented hazards in the module's own docstring (imaging/magnification, jaw-plane overscan margin, sweep-vs-jaw regime naming) or a distinct returned quantity. No near-duplicate cases found.

- `TestMagnification::test_it_is_the_ratio_of_the_two_drifts` — KEEP — tests the formula directly.
- `TestMagnification::test_the_two_axes_do_not_share_one` — KEEP — pins the installed geometry's actual X/Y magnifications (1.6425 / 1.4976), the documented "square jaw does not paint a square" fact.
- `TestMagnification::test_a_square_jaw_opening_does_not_paint_a_square` — KEEP — same fact, expressed as a physical consequence (asymmetric imaged gap) rather than the raw ratios.
- `TestMagnification::test_undefined_before_the_slit_plane` — KEEP — degenerate-input guard.
- `TestTheJawEdgeDoseLaw::test_droop_at_k_beam_widths` (parametrize, 6 cases) — KEEP — presumed keeper: pins `edge_droop_pct` exactly to the module docstring's own table (k=0.5→11.9516% … k=2.0→0.00012408%), the numbers quoted to the operator.
- `TestTheJawEdgeDoseLaw::test_it_scales_with_fwhm_not_with_absolute_mm` — KEEP — tests the scale-invariance property (margin/FWHM, not absolute mm).
- `TestTheJawEdgeDoseLaw::test_the_inverse_agrees_with_the_forward` — KEEP — inverts `overscan_for_droop` and recovers the planted droop percentage.
- `TestTheJawEdgeDoseLaw::test_k_for_a_droop_is_fwhm_independent` — KEEP — tests a distinct invariant (k-form is FWHM-independent) across three FWHM values.
- `TestTheJawEdgeDoseLaw::test_a_reversal_inside_the_jaw_is_below_half_dose` — KEEP — tests the negative-margin (sweep-limited) branch.
- `TestSolveGivesTheRequestedPatch::test_the_patch_is_exactly_the_size_asked_for` — KEEP — core correctness of `solve_axis`, both axes.
- `TestSolveGivesTheRequestedPatch::test_the_blades_are_the_patch_divided_by_the_magnification` — KEEP — tests the blade-sizing formula.
- `TestSolveGivesTheRequestedPatch::test_the_blades_are_narrower_than_the_patch` — KEEP — documents "the whole point" of the module (jaws ≈ 64% of the patch).
- `TestSolveGivesTheRequestedPatch::test_the_design_lands_in_the_jaw_limited_regime` — KEEP — tests the regime classification.
- `TestSolveGivesTheRequestedPatch::test_the_dose_across_the_patch_is_flat` — KEEP — tests the dose-uniformity output.
- `TestSolveGivesTheRequestedPatch::test_the_edge_droop_matches_the_k_that_was_asked_for` — KEEP — cross-checks `solve_axis`'s droop output against the pinned droop-law table value.
- `TestTheMarginIsAtTheJawNotTheSample::test_the_overscan_is_measured_in_slit_plane_millimetres` — KEEP — the core regression this module exists to fix (item 2 of the module docstring).
- `TestTheMarginIsAtTheJawNotTheSample::test_a_wider_beam_costs_more_amplitude` — KEEP.
- `TestTheMarginIsAtTheJawNotTheSample::test_the_beam_width_does_not_change_the_blades` — KEEP — regression guard against "the old bug" (mixing FWHM into blade sizing).
- `TestTheMarginIsAtTheJawNotTheSample::test_more_overscan_costs_transmission` — KEEP.
- `TestTheMarginIsAtTheJawNotTheSample::test_transmission_tends_to_the_duty_cycle_of_the_opening` — KEEP — tests a distinct asymptotic property (transmission → duty cycle).
- `TestForwardAndReverseAgree::test_describe_reproduces_what_solve_designed` — KEEP — round-trips `solve_axis` through `describe_axis`.
- `TestForwardAndReverseAgree::test_an_underdriven_sweep_is_named_sweep_limited` — KEEP — item 3 of the module docstring: the sweep-limited regime must be named, not silently mis-sized.
- `TestForwardAndReverseAgree::test_a_sweep_limited_pass_is_not_uniform` — KEEP — distinct assertion (uniformity, not just regime label) on the same underdriven scenario.
- `TestAsymmetry::test_an_off_centre_patch_opens_one_blade_further` — KEEP.
- `TestAsymmetry::test_the_patch_moves_by_what_was_asked` — KEEP.
- `TestAsymmetry::test_centring_the_sweep_buys_back_amplitude_and_transmission` — KEEP — tests the `use_sweep_offset` feature's benefit.
- `TestAsymmetry::test_the_sweep_offset_does_nothing_when_the_jaws_are_symmetric` — KEEP — the complementary (no-op) case for the same feature.
- `TestAsymmetry::test_a_blade_that_would_cross_centre_is_reported_not_clamped` — KEEP — tests the documented "reported not clamped" design choice.
- `TestGeneratorConversion::test_vpp_equals_the_differential_kv_at_gain_1000` — KEEP — pins the documented gain-1000 coincidence.
- `TestGeneratorConversion::test_the_plate_sees_half_the_differential` — KEEP.
- `TestGeneratorConversion::test_the_dc_term_is_half_the_differential_on_each_channel` — KEEP.
- `TestGeneratorConversion::test_the_peak_is_offset_plus_half_the_amplitude` — KEEP.
- `TestEnvelopeDownTheBeamline::test_upstream_of_the_jaws_nothing_is_clipped` — KEEP.
- `TestEnvelopeDownTheBeamline::test_the_jaw_plane_itself_reports_the_opening_not_the_sweep` — KEEP — an explicit boundary-condition regression ("<=" vs "<") named in the test and the code comment.
- `TestEnvelopeDownTheBeamline::test_downstream_the_commanded_sweep_outruns_the_beam` — KEEP.
- `TestEnvelopeDownTheBeamline::test_the_passed_beam_grows_as_the_jaw_opening_not_the_sweep` — KEEP.
- `TestTheBeamFrameAndTheSlitFrame::test_a_centred_beam_leaves_the_blades_alone` — KEEP.
- `TestTheBeamFrameAndTheSlitFrame::test_an_off_centre_beam_opens_one_blade_and_closes_the_other` — KEEP — pins exact numeric values (0.304/2.740 mm) for a documented beam-offset case.
- `TestTheBeamFrameAndTheSlitFrame::test_the_gap_is_unchanged_by_a_beam_offset` — KEEP — tests the gap-invariant across four offsets in one test function (a single behavioural claim checked at several points, not a set of separately-maintained near-duplicate tests).
- `TestTheBeamFrameAndTheSlitFrame::test_it_is_the_inverse_of_itself` — KEEP — round-trips `mechanical_blades_mm`.
- `TestTheBeamFrameAndTheSlitFrame::test_a_beam_offset_is_not_a_patch_offset` — KEEP — the key conceptual distinction the module exists to keep straight (beam-frame vs. slit-frame offsets).

**Total: 41 (41 keep, 0 delete)**

---

## tests/test_raster_plan.py

Production module read: `src/rbl/hardware/raster_plan.py`. Note: unlike `raster_model`/`slit_raster_model`, the "pinned" values here (e.g. `amplitude_kv == 34.2`) are captured from the pre-refactor widget implementation per the file's own docstring, not from an external manual/sheet — still legitimate regression pins on real computed physics, just not the specific "manual/sheet/planted-beam" presumed-keeper category.

- `TestSteererLimitedSolveKeys::test_all_required_keys_present` — KEEP — the tab reads every one of these ~24 keys; a dropped key is a real, silent GUI regression (downstream consumer reads these).
- `TestSteererLimitedSolveKeys::test_dose_regime_label` — KEEP — operator-visible regime label.
- `TestSteererLimitedSolveKeys::test_painted_full_echoes_input` — KEEP — guards against a parameter mix-up (e.g. passing `centre_mm` where `width_mm` belongs) in the assembled output dict.
- `TestSteererLimitedSolveKeys::test_painted_center_echoes_input` — KEEP — same class of guard, distinct key.
- `TestSteererLimitedSolveKeys::test_fwhm_at_slit_echoes_input` — KEEP — same class of guard, distinct key.
- `TestSteererLimitedSolveKeys::test_nan_fields_for_steerer_mode` — KEEP — tests the documented steerer-limited-mode NaN fields (no jaw clipping in this mode).
- `TestSteererLimitedSolvePinned::test_amplitude_kv` — KEEP — pinned regression value on real computed physics.
- `TestSteererLimitedSolvePinned::test_offset_kv_zero_for_centred` — KEEP.
- `TestSteererLimitedSolvePinned::test_blade_plus_equals_blade_minus_centred` — KEEP.
- `TestSteererLimitedSolvePinned::test_blade_plus_pinned` — KEEP.
- `TestSteererLimitedSolvePinned::test_sweep_half_at_target_pinned` — KEEP.
- `TestSteererLimitedSolvePinned::test_magnification_pinned` — KEEP — pins the real installed-geometry ratio (800/500 mm = 1.6).
- `TestSteererLimitedSolvePinned::test_dose_uniformity_positive` — KEEP — weak but real sanity check on a computed field; not guaranteed to be true by construction (mean_dose could be 0 in principle).
- `TestSteererLimitedSolvePinned::test_dose_profile_arrays_nonempty` — KEEP.
- `TestSteererLimitedSolvePinned::test_dose_dose_normalised` — KEEP — tests the documented normalisation-to-1.0 behaviour.
- `TestSteererLimitedSolveOffset::test_blade_plus_gt_blade_minus_for_positive_offset` — KEEP.
- `TestSteererLimitedSolveOffset::test_blade_plus_pinned` — KEEP.
- `TestSteererLimitedSolveOffset::test_blade_minus_pinned` — KEEP.
- `TestSteererLimitedSolveOffset::test_painted_center_echoes_input` — KEEP — same echo-guard class, exercised on the offset fixture (checks 5.0, not 0.0).
- `TestSteererLimitedSolveOffset::test_amplitude_same_as_centred` — KEEP — tests a real invariant (amplitude depends on width, not centre).
- `TestSteererLimitedSolveSensitivity::test_wider_patch_needs_more_voltage` — KEEP.
- `TestSteererLimitedSolveSensitivity::test_heavier_species_needs_less_voltage` — KEEP — the assertion (`high_e > low_e`, i.e. higher energy needs *more* voltage) is real and matches the docstring's physics explanation, even though the test's own name contradicts its docstring/assertion (a naming bug in the test, not a reason to delete it).
- `TestSteererLimitedSolveSensitivity::test_longer_drift_increases_slit_sweep` — KEEP.
- `TestEnvelopeStatusKeys::test_all_keys_present` — KEEP — `envelope_status`'s dict is what the tab renders.
- `TestEnvelopeStatusKeys::test_walls_has_freq_and_envelope_keys` — KEEP — checks nested `walls` dict keys not covered by the flat `REQUIRED` list in the previous test.
- `TestEnvelopeStatusDecisions::test_low_kv_low_freq_is_inside` — KEEP.
- `TestEnvelopeStatusDecisions::test_very_high_kv_is_outside` — KEEP.
- `TestEnvelopeStatusDecisions::test_freq_above_bandwidth_not_in_envelope` — KEEP — tests the bandwidth-ceiling branch specifically.
- `TestEnvelopeStatusDecisions::test_over_ceiling_axis_listed` — KEEP — tests the per-axis ceiling-list branch with a mixed (one-over, one-under) case.
- `TestEnvelopeStatusDecisions::test_ratio_reflects_worst_channel` — KEEP — tests the actual worst-channel *selection* logic with distinguishing capacitances (100 pF vs 5000 pF), a real, non-trivial behaviour.
- `TestEnvelopeStatusDecisions::test_worst_label_is_amp_label` — **DELETE** — restates a guarantee the implementation already makes structurally: with the standard `caps_500pf` fixture, `envelope_status` can only ever return one of the dict's own keys ("X+"/"X-"/"Y+"/"Y-"), so the membership assertion cannot fail short of a catastrophic bug, and it exercises no selection logic (all four channels are identical). `test_ratio_reflects_worst_channel` already covers the real "which channel wins" behaviour with a discriminating case.
- `TestEnvelopeStatusDecisions::test_ratio_monotone_with_kv` — KEEP — tests a real monotonicity property.
- `TestEnvelopeStatusDecisions::test_custom_axis_of_channel` — KEEP — despite the same weak membership-style assertion as the deleted test above, this is the *only* test exercising the `axis_of_channel` override parameter at all, so it is the sole coverage of that code path rather than a duplicate of default-mapping coverage.

**Total: 33 (32 keep, 1 delete)**

---

## tests/test_beam_reconstruction.py

Production module read: `src/rbl/hardware/beam_reconstruction.py`. Private helpers `_gauss_tail`/`_raster_tail` are imported and tested directly; this is treated as "tests hardware-layer mathematics directly" (the keep criterion's alternate clause) rather than a rewrite-flagged private-attribute test, since these are pure-math building blocks in a Qt-free hardware module, not GUI/widget internal state.

- `TestGaussTail::test_half_the_beam_lies_past_its_own_centre` — KEEP.
- `TestGaussTail::test_tail_shrinks_as_the_edge_moves_away` — KEEP.
- `TestGaussTail::test_wider_beam_puts_more_flux_past_a_fixed_edge` — KEEP.
- `TestGaussTail::test_zero_width_is_a_step` — KEEP — degenerate (`sigma_mm == 0`) branch.
- `TestSolveAxisCentre::test_centred_beam_is_width_independent` (parametrize, 5 sigmas) — KEEP — presumed keeper: tests the documented anchor case the whole reconstruction leans on (equal currents → midpoint, for every width).
- `TestSolveAxisCentre::test_equal_currents_on_asymmetric_slits_find_their_midpoint` — KEEP.
- `TestSolveAxisCentre::test_more_current_on_plus_slit_moves_beam_toward_it` — KEEP.
- `TestSolveAxisCentre::test_more_current_on_minus_slit_moves_beam_toward_it` — KEEP.
- `TestSolveAxisCentre::test_monotonic_in_the_current_ratio` — KEEP — tests monotonicity across 6 ratios as one behavioural claim.
- `TestSolveAxisCentre::test_round_trip_recovers_a_planted_beam` (parametrize, 5 true_c) — KEEP — presumed keeper, explicitly the "invert a planted beam to recover it" case named in the ticket.
- `TestSolveAxisCentre::test_unusable_current_gives_nan` — KEEP — three distinct degenerate-input branches (NaN, NaN, zero) in one function.
- `TestSolveAxisCentre::test_floor_level_current_is_not_signal` — KEEP — tests the `NOISE_FLOOR_A` threshold specifically.
- `TestSolveAxisCentre::test_crossed_slits_are_rejected` — KEEP — tests the `edge_minus_mm >= edge_plus_mm` guard.
- `TestAmbiguityInterval::test_centred_beam_has_no_ambiguity` — KEEP.
- `TestAmbiguityInterval::test_interval_widens_as_the_beam_goes_off_centre` — KEEP.
- `TestAmbiguityInterval::test_nominal_estimate_lies_inside_its_own_interval` — KEEP.
- `TestAmbiguityInterval::test_unsolvable_axis_gives_all_nan` — KEEP.
- `TestAmbiguityInterval::test_raster_mode_widens_the_interval_further` — KEEP.
- `TestRasterTail::test_sweeping_spreads_flux_past_an_outside_edge` — KEEP.
- `TestRasterTail::test_vanishing_sweep_collapses_to_the_static_case` — KEEP — tests the `half_span_mm → 0` limiting behaviour against `_gauss_tail`.
- `TestRasterTail::test_centred_sweep_splits_evenly_about_its_centre` — KEEP.
- `TestRasterTail::test_round_trip_recovers_a_swept_beam` (parametrize, 4 true_c) — KEEP — presumed keeper, planted-beam recovery for the raster (swept) model.
- `TestReconstruct::test_balanced_beam_sits_on_the_axis` — KEEP — drives the full 2-D `reconstruct` production path.
- `TestReconstruct::test_axes_are_solved_independently` — KEEP.
- `TestReconstruct::test_one_dead_slit_invalidates_the_whole_estimate` — KEEP — documented design choice ("a partial picture is easier to misread than no picture").
- `TestReconstruct::test_nan_slit_is_reported_as_bad` — KEEP.
- `TestReconstruct::test_every_bad_slit_is_listed` — KEEP.
- `TestReconstruct::test_missing_slit_position_blocks_reconstruction` — KEEP.
- `TestReconstruct::test_absent_slit_position_key_blocks_reconstruction` — KEEP — distinct code path from the previous test (missing dict key vs. NaN value).
- `TestReconstruct::test_raster_mode_uses_per_axis_sweep_spans` — KEEP.
- `TestReconstruct::test_estimate_repr_is_readable` — **DELETE** — asserts only that `"BeamEstimate" in repr(...)`; `BeamEstimate.__repr__` is not read by any GUI consumer (checked `gui/widgets/beam_indicator.py`, which stores the estimate object but never calls `repr()` on it) and no math is exercised. This is a debug-formatting detail with no operator-visible or downstream-consumer effect and doesn't test hardware math — meets neither keep criterion.
- `TestOverscanFlags::test_all_blades_reached_when_all_carry_current` — KEEP.
- `TestOverscanFlags::test_blade_at_the_floor_is_not_being_reached` — KEEP.
- `TestOverscanFlags::test_missing_channel_counts_as_not_reached` — KEEP.

**Total: 34 (33 keep, 1 delete)**

---

## tests/test_profile_fwhm.py

Production module read: `src/rbl/hardware/profile_fwhm.py` (single-peak section).

- `TestEstimateBaseline::test_flat_signal_returns_value` — KEEP.
- `TestEstimateBaseline::test_edges_higher_than_centre` — KEEP.
- `TestEstimateBaseline::test_dc_offset_recovered` — KEEP.
- `TestEstimateBaseline::test_single_sample` — KEEP — tests the `wing = max(1, ...)` clamp for very short input.
- `TestComputeFwhmSamples::test_symmetric_gaussian_known_fwhm` — KEEP — presumed keeper: recovers the closed-form Gaussian FWHM (`2√(2ln2)·σ`) from a planted synthetic waveform.
- `TestComputeFwhmSamples::test_narrower_gaussian` — KEEP — same recovery at a different σ/sample-count ratio (5 vs 20, looser tolerance), plausibly sensitive to discretisation/interpolation behaviour at a much narrower peak; not a mechanical duplicate of the previous test's scale.
- `TestComputeFwhmSamples::test_baseline_subtraction_applied` — KEEP — tests the `subtract_baseline` flag's actual effect.
- `TestComputeFwhmSamples::test_rectangle_pulse_fwhm` — KEEP — distinct (non-Gaussian) waveform shape, exercises the linear-interpolation crossing finder on a step edge.
- `TestComputeFwhmSamples::test_too_short_raises` — KEEP.
- `TestComputeFwhmSamples::test_non_positive_peak_raises` — KEEP.
- `TestComputeFwhmSamples::test_peak_at_edge_crossing_not_found` — KEEP — distinct error branch (no left crossing) from the previous two.
- `TestComputeFwhmSamples::test_fwhm_is_positive` — **DELETE** — restates a weaker version of the guarantee already established by `test_symmetric_gaussian_known_fwhm` and `test_narrower_gaussian` (both already prove the FWHM is a specific positive value on the same kind of synthetic Gaussian); this test uses no new waveform shape and its `> 0` assertion is strictly implied by either of the pinned-value tests passing.
- `TestComputeFwhmSamples::test_different_amplitudes_same_fwhm` — KEEP — tests a real invariant (amplitude-scale independence).
- `TestComputeFwhmSamples::test_asymmetric_peak_still_converges` — KEEP — a genuinely different (skewed, non-Gaussian) waveform shape, unlike the deleted test above.
- `TestComputeFwhmSeconds::test_fwhm_seconds_scales_with_xincr` — KEEP.
- `TestComputeFwhmSeconds::test_negative_xincr_raises` — KEEP.
- `TestComputeFwhmSeconds::test_zero_xincr_raises` — KEEP — distinct boundary value from the negative case (`xincr <= 0` guard, tests the `== 0` edge specifically).
- `TestGaussianFwhmFit::test_fit_recovers_known_sigma` — KEEP — presumed keeper: recovers a planted σ via `curve_fit`.
- `TestGaussianFwhmFit::test_fit_center_accurate` — KEEP.
- `TestGaussianFwhmFit::test_fit_amplitude_accurate` — KEEP.
- `TestGaussianFwhmFit::test_r_squared_near_one_for_gaussian` — KEEP.
- `TestGaussianFwhmFit::test_fwhm_seconds_consistent` — KEEP — tests the seconds/samples arithmetic relationship in the returned dict.
- `TestGaussianFwhmFit::test_too_short_raises` — KEEP.
- `TestGaussianFwhmFit::test_non_positive_peak_raises` — KEEP.
- `TestGaussianFwhmFit::test_baseline_subtraction_in_fit` — KEEP.

**Total: 24 (23 keep, 1 delete)**

---

## tests/test_profile_multipeak.py

Production module read: `src/rbl/hardware/profile_fwhm.py` (multi-peak section: `find_peaks`, `analyse_profile`, `fit_gaussians`, `best_fwhm`, `upper_envelope`). Every test in this file targets a specific documented real-beamline failure mode (noise-narrowing bias, raster-tooth aliasing, neighbour-crossing, quantum noise floor) or a distinct returned field; no near-duplicate cases found.

- `TestMovingAverage::test_window_one_is_identity` — KEEP.
- `TestMovingAverage::test_length_preserved` — KEEP.
- `TestMovingAverage::test_constant_signal_unchanged` — KEEP.
- `TestMovingAverage::test_spike_is_flattened` — KEEP.
- `TestFindPeaks::test_finds_both_peaks` — KEEP.
- `TestFindPeaks::test_returns_left_to_right` — KEEP.
- `TestFindPeaks::test_threshold_rejects_small_peak` — KEEP.
- `TestFindPeaks::test_min_sep_merges_close_candidates` — KEEP.
- `TestAnalyseProfile::test_two_peaks_measured_independently` — KEEP.
- `TestAnalyseProfile::test_identical_peaks_have_near_zero_spread` — KEEP.
- `TestAnalyseProfile::test_separation_reported` — KEEP.
- `TestAnalyseProfile::test_single_peak_mode_ignores_second` — KEEP.
- `TestAnalyseProfile::test_max_peaks_is_capped` — KEEP.
- `TestAnalyseProfile::test_crossing_never_walks_into_the_neighbour` — KEEP — a named regression (the valley-fence bug).
- `TestAnalyseProfile::test_overlapping_peaks_reported_unresolved_not_raised` — KEEP.
- `TestAnalyseProfile::test_negative_going_signal_is_flipped` — KEEP.
- `TestAnalyseProfile::test_forced_polarity_overrides_auto` — KEEP.
- `TestAnalyseProfile::test_flat_trace_raises` — KEEP.
- `TestAnalyseProfile::test_noise_only_trace_raises` — KEEP — distinct failure mode from the flat-trace case (noise vs. zero signal).
- `TestAnalyseProfile::test_too_short_raises` — KEEP.
- `TestAnalyseProfile::test_smoothing_rejects_a_noise_spike` — KEEP — real documented failure mode (a spike taller than the beam).
- `TestAnalyseProfile::test_noise_biases_half_max_narrow_and_smoothing_fixes_it` — KEEP — presumed keeper, "the measured-on-hardware failure mode, pinned down as a test."
- `TestFitGaussians::test_fit_recovers_both_widths` — KEEP.
- `TestFitGaussians::test_two_gaussian_model_beats_one` — KEEP — named regression ("r² on the real beam was 0.14" before the sum-of-Gaussians model).
- `TestFitGaussians::test_fit_measures_peaks_the_half_max_cannot` — KEEP.
- `TestFitGaussians::test_best_fwhm_prefers_the_fit_when_r2_is_good` — KEEP.
- `TestFitGaussians::test_best_fwhm_falls_back_when_r2_is_poor` — KEEP — the complementary branch to the previous test.
- `TestFitGaussians::test_best_fwhm_without_a_fit` — KEEP — distinct code path (`fit=None`).
- `TestRasterEnvelope::test_raw_trace_measures_a_raster_tooth` — KEEP — named real-beamline failure mode.
- `TestRasterEnvelope::test_envelope_recovers_the_beam_width` — KEEP.
- `TestRasterEnvelope::test_envelope_rescues_the_fit` — KEEP.
- `TestRasterEnvelope::test_window_wider_than_the_ripple_reads_high` — KEEP.
- `TestRasterEnvelope::test_envelope_off_by_default` — KEEP.
- `TestRasterEnvelope::test_upper_envelope_preserves_length` — KEEP.
- `TestRasterEnvelope::test_upper_envelope_is_never_below_the_peaks_it_spans` — KEEP.
- `TestClippingAndNoiseFloor::test_flat_bottom_is_reported_as_clipped` — KEEP.
- `TestClippingAndNoiseFloor::test_clean_trace_is_not_flagged` — KEEP.
- `TestClippingAndNoiseFloor::test_quantum_floors_the_noise_estimate` — KEEP — named real bug (signal-to-noise "ran to 1e12").
- `TestAxisLabels::test_peaks_are_labelled_x_and_y` — KEEP.
- `TestAxisLabels::test_per_axis_widths_are_exposed` — KEEP.
- `TestAxisLabels::test_ratio_is_one_for_equal_axes` — KEEP.
- `TestAxisLabels::test_ratio_tracks_an_elliptical_beam` — KEEP.
- `TestAxisLabels::test_labels_are_configurable` — KEEP.

**Total: 43 (43 keep, 0 delete)**

---

## tests/test_width_levels.py

Production module read: `src/rbl/hardware/profile_fwhm.py` (width-ladder section: `measure_width_levels`, `gaussian_width_ratio`, `gaussian_width_from_sigma`, `level_label`). Every test targets one of the four documented failure modes in the file's own header (Gaussian-only test blindness, silent truncation at the peak fence, noise-floor contamination, and the fit-fallback-produces-a-fake-1.8226 trap) or a distinct math constant. No duplication found.

- `test_the_gaussian_ratios_are_what_the_maths_says` — KEEP — presumed keeper: pins `sqrt(ln(1/f)/ln2)` to exact numeric values for all three ladder levels.
- `test_the_1_over_e2_width_is_exactly_four_sigma` — KEEP — pins the documented optics convention.
- `test_the_half_maximum_width_from_sigma_is_the_usual_2_3548` — KEEP.
- `test_a_level_outside_zero_to_one_is_refused` (parametrize, 4 values) — KEEP — degenerate-input guard across the boundary and outside-range cases.
- `test_levels_are_named_the_way_an_operator_would` — KEEP — operator-facing labels.
- `test_a_gaussian_beam_recovers_the_gaussian_ratios` — KEEP — presumed keeper: a planted Gaussian must recover the exact Gaussian ratios (1.8226, 1.6986).
- `test_every_level_is_measured_not_derived_from_the_fwhm` — KEEP — the central design guarantee of the module: two traces with the same core but different tails must give different FWTM (rules out deriving levels from FWHM via the fixed ratio).
- `test_a_haloed_beam_reads_heavier_than_gaussian` — KEEP — the "consequential direction" named in the module docstring.
- `test_a_flat_topped_beam_reads_lighter_than_gaussian` — KEEP — the complementary direction.
- `test_a_low_level_that_meets_the_neighbour_is_unresolved_not_truncated` — KEEP — named failure mode ("quietly TRUNCATED at the fence").
- `test_an_unmeasurable_level_is_offered_the_fit_and_labelled_as_such` — KEEP.
- `test_the_tail_ratio_has_no_fit_fallback` — KEEP — "the subtle one," explicitly the reason the file exists per its own docstring.
- `test_a_level_under_the_noise_floor_is_refused_with_a_reason` — KEEP — named failure mode (noise floor).
- `test_a_generous_noise_guard_refuses_more_levels` — KEEP — tests the `noise_guard` parameter's actual effect.
- `test_measuring_does_not_mutate_the_profile_result` — KEEP — a real no-mutation contract test (copies state *before* the call as a comparison baseline, which is standard regression-test practice, not "writing private state and reading it back" — no state is written by the test itself, only read and compared).
- `test_two_traces_measured_in_a_row_do_not_influence_each_other` — KEEP — guards against shared/leaked state between calls, a real correctness concern given no caching is intended.
- `test_the_default_levels_are_the_three_on_the_tab` — KEEP — ties the `DEFAULT_LEVELS` constant to what the GUI tab actually shows the operator; would catch an accidental reorder/drop/add to the default ladder.

**Total: 17 (17 keep, 0 delete)**

---

## tests/test_bpm_calibration.py

Production module: `src/rbl/hardware/bpm_calibration.py`.

- `test_mm_per_second_is_spacing_over_separation` — KEEP — pins the one-division formula (`mm/s = spacing_mm / separation_s`) directly.
- `test_a_non_positive_separation_is_refused` (parametrize: 0.0, -1e-3, nan) — KEEP — each of the 3 cases exercises a genuinely different guard: zero, a negative value, and a NaN (a naive `<= 0` check alone would silently pass the NaN case), so they would not all fail together on the same defect.
- `test_seconds_to_mm_is_nan_without_a_scale` — KEEP — asserts the NaN-not-zero contract an uncalibrated tab depends on; drives the production function directly.
- `test_the_tallest_peak_is_dropped_as_the_trigger` — KEEP — pins the trigger-selection rule against a concrete 3-peak trace.
- `test_the_trigger_is_dropped_wherever_it_sits` — KEEP — checks the trigger is found at either end or the middle of the peak list, a distinct code path from the "tallest" test above (position-independence).
- `test_a_trigger_that_barely_stands_out_is_reported_unconfident` — KEEP — pins the `confident=False` / note-text safety behaviour for a near-tie, which the module docstring calls out by name.
- `test_two_peaks_are_usable_but_never_confident` — KEEP — distinct branch (only 2 peaks found, no trigger to identify).
- `test_one_peak_is_refused_outright` — KEEP — distinct error branch (fewer than 2 peaks).
- `test_an_override_is_taken_as_given` — KEEP — exercises the operator-override path, a separate branch from auto-selection.
- `test_an_override_off_the_end_is_refused` — KEEP — distinct validation branch (bad override indices).
- `test_extra_furniture_keeps_the_two_tallest_survivors` — KEEP — distinct branch (more than 3 peaks on the trace).
- `test_refine_apex_finds_the_vertex_between_samples` — KEEP — pins the sub-sample parabolic-fit formula against an exact analytic parabola (worked example).
- `test_refine_apex_declines_at_the_edges_and_on_a_flat_top` — KEEP — distinct degenerate branches (edge-of-record, collinear/flat).
- `test_a_clean_fiducial_trace_recovers_the_scale` — KEEP — inverts a planted synthetic fiducial trace to recover the known separation/scale; the "invert a planted beam" presumed-keeper category.
- `test_the_trigger_peak_is_never_one_of_the_two_measured` — KEEP — the module's own stated "failure this whole module exists to prevent"; presumed keeper.
- `test_noise_does_not_move_the_scale_by_more_than_a_percent` — KEEP — distinct robustness claim (additive noise) not covered elsewhere.
- `test_the_scale_does_not_depend_on_the_timebase` — KEEP — distinct invariant (two different `xincr` values must agree), a real regression class of its own.
- `test_an_override_measures_the_pair_the_operator_chose` — KEEP — end-to-end override path through `analyse_fiducials`, distinct from the unit-level override test on `select_fiducial_peaks`.
- `test_a_flat_trace_yields_no_calibration` — KEEP — distinct degenerate-input branch (no measurable peaks at all).
- `test_a_non_positive_xincr_is_refused` — KEEP — distinct validation branch (bad timebase).
- `test_a_different_head_spacing_scales_the_answer` — KEEP — pins that `spacing_mm` linearly scales the result, a distinct parameter from separation.
- `test_calibrations_are_stored_and_read_back_per_bpm` — KEEP — drives the real `calibration_entry`/`load_calibrations`/`active_mm_per_second` persistence path a tab consumer reads; not private state, this is the documented on-disk contract.
- `test_an_active_name_with_no_entry_reads_as_uncalibrated` — KEEP — pins the specific "must not inherit another BPM's scale" safety behaviour called out in the module docstring.
- `test_an_empty_or_broken_config_reads_as_uncalibrated` — KEEP — distinct set of malformed-config shapes (missing key, wrong type, non-numeric, negative) feeding the same safe-fallback, worth keeping as one guard over several corrupt-config shapes.

**Total: 24 (24 keep, 0 delete)**

---

## tests/test_load_model.py

Production module: `src/rbl/hardware/load_model.py`.

- `TestShapeKCrossCheck::test_matches_calibration_config` (parametrize: sine/triangle/ramp/square) — KEEP — guards the exact drift the module docstring warns about (`_SHAPE_K` silently diverging from `calibration_config.ac_shape_k`); each shape is a distinct constant, not a repeated case.
- `TestShapeKCrossCheck::test_default_shape_is_covered` — KEEP — distinct check that the default shape constant has a table entry at all.
- `TestAdmittanceFromFundamentals::test_pure_capacitor_has_zero_conductance` — KEEP — pins the admittance formula at the exact 90° pure-capacitor case (worked example: I_pk from 2πfCV).
- `TestAdmittanceFromFundamentals::test_phase_off_90_shows_up_as_conductance` — KEEP — distinct physical case (leaky channel), the diagnostic the module exists for.
- `TestAdmittanceFromFundamentals::test_degenerate_inputs_return_nan` (parametrize: v=0, freq=0, i=nan) — KEEP — each case trips a different guard clause (`v_fund_kv<=0`, `freq_hz<=0`, `not isfinite`), not the same one repeated.
- `TestCapacitanceFromCharge::test_recovers_known_capacitance_from_a_rectangular_pulse` — KEEP — inverts a planted rectangular charge pulse to recover a known capacitance (worked example).
- `TestCapacitanceFromCharge::test_sign_of_step_does_not_matter` — KEEP — distinct invariant (sign-independence of the |Q/dV| result).
- `TestCapacitanceFromCharge::test_degenerate_inputs_return_nan` — KEEP — distinct guard branches (too-short array, zero dV).
- `TestEnvelopeWalls::test_reproduces_section_1_6_table` (parametrize: 4 (v_kv, f_hz) pairs) — KEEP — pins the safety plan's own worked table (Section 1.6); presumed keeper, and each pair checks a distinct table row/frequency, not a repeated point.
- `TestEnvelopeWalls::test_voltage_wall_is_flat_at_max_kv` — KEEP — distinct wall (voltage wall, not current wall).
- `TestEnvelopeWalls::test_envelope_is_nan_past_bandwidth` — KEEP — distinct behaviour (NaN past the bandwidth wall).
- `TestEnvelopeWalls::test_rejects_non_positive_inputs` — KEEP — distinct validation branch.
- `TestEnvelopeWalls::test_unknown_shape_raises` — KEEP — distinct validation branch (bad shape name).

**Total: 13 (13 keep, 0 delete)**

---

## tests/test_regulation.py

Production module: `src/rbl/hardware/regulation.py`.

- `TestRegulationRatio::test_healthy_ratio` — KEEP — pins the basic division.
- `TestRegulationRatio::test_near_zero_commanded_is_nan` — KEEP — distinct guard (division-by-~0 branch).
- `TestClassify::test_idle_when_commanded_near_zero` — KEEP — distinct classification branch.
- `TestClassify::test_ok_when_following` — KEEP — distinct branch (healthy amplifier).
- `TestClassify::test_amp_off_when_both_monitors_are_zero` — KEEP — distinct branch, the specific failure mode the module exists to catch (dead channel).
- `TestClassify::test_current_limited_when_voltage_low_current_at_limit` — KEEP — distinct branch, the other specific failure mode (LIMIT-mode operation) the module exists to catch; also checks the reason text says "invalid".
- `TestClassify::test_classification_is_sign_independent` — KEEP — distinct invariant (AC swing sign must not flip classification).
- `TestClassify::test_below_arm_threshold_is_idle_even_if_ratio_looks_bad` — KEEP — distinct branch (arm-threshold override of a bad-looking ratio).

**Total: 8 (8 keep, 0 delete)**

---

## tests/test_edge_metrics.py

Production module: `src/rbl/hardware/edge_metrics.py`.

- `TestOvershoot::test_clean_first_order_rise_has_no_overshoot` — KEEP — pins the clean-rise case against an analytic first-order exponential.
- `TestOvershoot::test_overshooting_rise_is_positive` — KEEP — distinct case (deliberately overshooting trace).
- `TestOvershoot::test_zero_final_is_nan` — KEEP — distinct guard branch.
- `TestOvershoot::test_empty_trace_is_nan` — KEEP — distinct guard branch (empty input, separate check from zero-final).
- `TestSettlingTime::test_settles_quickly_for_a_clean_rise` — KEEP — pins expected settle time for a known time constant.
- `TestSettlingTime::test_never_settling_is_nan` — KEEP — distinct branch (trace stuck outside tolerance).
- `TestSettlingTime::test_within_tolerance_from_the_start_returns_first_sample_time` — KEEP — distinct branch (already-settled trace).
- `TestSettlingTime::test_tighter_tolerance_never_settles_shorter_tolerance_may` — KEEP — distinct invariant (monotonic relationship between tolerance and settle time).
- `TestFlatTopCreep::test_undercompensated_creeps_positive` — KEEP — pins the sign convention that is the pot-steering signal (module's core purpose).
- `TestFlatTopCreep::test_overcompensated_creeps_negative` — KEEP — the complementary sign case, not redundant since it validates the opposite branch of the same steering signal.
- `TestFlatTopCreep::test_zero_final_is_nan` — KEEP — distinct guard branch.
- `TestRiseTime::test_positive_for_a_rising_step` — KEEP — basic sanity on the 10-90% metric.
- `TestRiseTime::test_zero_final_is_nan` — KEEP — distinct guard branch.
- `TestCurrentMetrics::test_peak_current_is_the_worst_magnitude` — KEEP — pins the metric against a known exponential+offset trace.
- `TestCurrentMetrics::test_empty_trace_is_nan` — KEEP — distinct guard branch.
- `TestCurrentMetrics::test_tail_duration_positive_for_a_decaying_pulse` — KEEP — distinct behaviour (decaying pulse case).
- `TestCurrentMetrics::test_flat_trace_has_zero_tail` — KEEP — distinct branch (no-excursion case, `peak == 0`).
- `TestCurrentMetrics::test_never_settling_current_is_nan` — KEEP — distinct branch (current that never returns to baseline).
- `TestFigureOfMerit::test_clean_trial_scores_lower_than_a_poor_one` — KEEP — pins the ranking behaviour the score exists to provide.
- `TestFigureOfMerit::test_nan_components_are_dropped_not_poisoning` — KEEP — distinct behaviour (partial-NaN handling), pins the exact expected sum.
- `TestFigureOfMerit::test_all_nan_is_nan` — KEEP — distinct branch (fully-NaN case).

**Total: 21 (21 keep, 0 delete)**

---

## tests/test_ac_metrics.py

Production module: `src/rbl/hardware/ac_metrics.py`.

- `TestWholeCycleSamples::test_exact_multiple` — KEEP — distinct branch (already a whole number of cycles).
- `TestWholeCycleSamples::test_truncates_partial_cycle` — KEEP — distinct branch (partial-cycle truncation, the function's whole reason for existing).
- `TestWholeCycleSamples::test_too_few_cycles_returns_zero` — KEEP — distinct branch (below `MIN_CYCLES`).
- `TestWholeCycleSamples::test_exactly_min_cycles_nonzero` — KEEP — distinct boundary (exactly at `MIN_CYCLES`), complementary to the "too few" test.
- `TestWholeCycleSamples::test_zero_freq_returns_zero` — KEEP — distinct guard branch.
- `TestWholeCycleSamples::test_negative_freq_returns_zero` — KEEP — distinct guard branch from zero-freq (different comparison operand).
- `TestWholeCycleSamples::test_zero_samples_returns_zero` — KEEP — distinct guard branch.
- `TestWholeCycleSamples::test_zero_sample_rate_returns_zero` — KEEP — distinct guard branch.
- `TestFundamental::test_sine_amplitude` — KEEP — pins the DFT amplitude against the analytic Fourier coefficient for a sine (worked example).
- `TestFundamental::test_triangle_amplitude` — KEEP — distinct analytic constant (8/π²), a different physical shape.
- `TestFundamental::test_square_amplitude` — KEEP — distinct analytic constant (4/π).
- `TestFundamental::test_sine_phase_is_minus_pi_over_2` — KEEP — distinct claim (phase, not amplitude).
- `TestFundamental::test_cosine_phase_is_zero` — KEEP — distinct signal (cosine reference) validating the phase convention from the other direction.
- `TestFundamental::test_empty_returns_nan` — KEEP — distinct guard branch.
- `TestFundamental::test_too_short_returns_nan` — KEEP — distinct guard branch (nonzero but sub-`MIN_CYCLES` length).
- `TestFundamental::test_dc_input_freq_zero_returns_nan` — KEEP — distinct guard branch (freq_hz=0 on `fundamental()` itself, vs. the dict-level DC tests below).
- `TestFundamental::test_returns_float_tuple` — KEEP — guards the explicit `float()` cast in the return value, which real code depends on (e.g. JSON logging of `np.float64` fails where plain `float` would not); not a tautology since the cast could be dropped by a refactor.
- `TestFundamental::test_amplitude_nonnegative` — **DELETE** — the amplitude is computed as `abs(coeff)`, so this only restates the guarantee Python's `abs()` already makes (a magnitude cannot be negative); it adds no detection power beyond `test_sine/triangle/square_amplitude`, which already pin the actual values.
- `TestFundamental::test_list_input_accepted` — KEEP — guards the documented "accepts plain Python list" input contract of the module's scope.
- `TestFundamental::test_all_nan_samples_returns_nan` — KEEP — distinct branch (the `np.isfinite` filtering path when the retained segment is entirely NaN).
- `TestAcMetrics::test_all_keys_present` — KEEP — pins the dict shape every downstream consumer of `ac_metrics()` reads by key.
- `TestAcMetrics::test_empty_all_nan` — KEEP — distinct branch (empty-input dict), checked against every key.
- `TestAcMetrics::test_dc_setpoint_fund_fields_nan` — KEEP — distinct branch on the full `ac_metrics()` dict (as opposed to `fundamental()` alone).
- `TestAcMetrics::test_dc_setpoint_rms_still_computed` — KEEP — distinct claim (non-fundamental fields stay valid on a DC setpoint), the "one code path serves both modes" behaviour named in the docstring.
- `TestAcMetrics::test_sine_crest_factor` — KEEP — pins crest factor against the analytic constant √2.
- `TestAcMetrics::test_triangle_crest_factor` — KEEP — distinct analytic constant √3.
- `TestAcMetrics::test_square_crest_factor` — KEEP — distinct analytic constant 1.0.
- `TestAcMetrics::test_sine_rms_is_a_over_sqrt2` — KEEP — pins RMS against the analytic constant A/√2.
- `TestAcMetrics::test_triangle_rms_is_a_over_sqrt3` — KEEP — distinct analytic constant A/√3.
- `TestAcMetrics::test_square_rms_is_a` — KEEP — distinct analytic constant A.
- `TestAcMetrics::test_sine_mean_near_zero` — KEEP — distinct claim (DC/mean field on a symmetric signal).
- `TestAcMetrics::test_dc_offset_measured` — KEEP — distinct claim (mean correctly recovers an added DC offset), the complementary case to near-zero mean.
- `TestAcMetrics::test_peak_abs_at_least_peak` — **DELETE** — `peak` is `np.percentile(absx, 99.9)` and `peak_abs` is `np.max(absx)`; any percentile ≤100 of an array is guaranteed by numpy's definition of `percentile` to be ≤ its max, so this restates that library guarantee rather than testing `ac_metrics`' own logic — it would hold even if `PEAK_PERCENTILE` were set incorrectly.
- `TestAcMetrics::test_peak_below_true_amplitude` — KEEP — distinct, concrete claim tied to the known amplitude A of the test signal, not a tautology.
- `TestAcMetrics::test_fund_ratio_near_one_for_sine` — KEEP — distinct physical claim (sine energy concentrated at the fundamental).
- `TestAcMetrics::test_fund_ratio_less_than_one_for_triangle` — KEEP — distinct physical claim (triangle has real harmonic content), not redundant with the sine case.
- `TestAcMetrics::test_n_samples_correct` — KEEP — distinct metadata field a downstream consumer reads.
- `TestAcMetrics::test_n_cycles_positive` — KEEP — distinct metadata field, not the same one as n_samples.
- `TestAcMetrics::test_fund_amp_consistent_with_fundamental` — KEEP — guards the two entry points (`ac_metrics()` and `fundamental()`) from drifting apart.
- `TestAcMetrics::test_list_input_accepted` — KEEP — guards the same list-input contract as `TestFundamental::test_list_input_accepted`, but on the separate public entry point `ac_metrics()`.
- `TestPhaseDifferenceDeg::test_capacitive_lead_90` — KEEP — the physical claim this whole phase function exists to support (I leads V by 90° on a capacitive load), checked for two distinct waveform shapes in one function (matching the module's own self-test loop); presumed keeper.
- `TestPhaseDifferenceDeg::test_zero_difference` — KEEP — distinct point on the wrap function.
- `TestPhaseDifferenceDeg::test_exact_90` — KEEP — distinct point, sign +.
- `TestPhaseDifferenceDeg::test_exact_minus_90` — KEEP — distinct point, sign -, catches a sign-flip bug the +90 case alone would not.
- `TestPhaseDifferenceDeg::test_wraps_near_plus_180` — KEEP — distinct wrap-boundary case, positive side.
- `TestPhaseDifferenceDeg::test_wraps_near_minus_180` — KEEP — distinct wrap-boundary case, negative side, catches an asymmetric-modulo bug the positive case alone would not.
- `TestPhaseDifferenceDeg::test_nan_input_returns_nan` — KEEP — distinct guard branch (3 NaN combinations checked inside one function).
- `TestPhaseDifferenceDeg::test_inf_input_returns_nan` — KEEP — distinct guard branch (inf, not covered by the NaN test).
- `TestPhaseDifferenceDeg::test_antisymmetric` — KEEP — distinct algebraic invariant.
- `TestPhaseDifferenceDeg::test_result_in_valid_range` — KEEP — distinct invariant (range bound) checked at 3 angle pairs inside one function.
- `TestNoiseRejection::test_fundamental_more_robust_than_peak` — KEEP — the core claim of the entire module (the +181%-high peak vs. <2%-off fundamental result cited in the module docstring); presumed keeper.

**Total: 51 (49 keep, 2 delete)**

---

## tests/test_amp_monitor.py

Production module: `src/rbl/hardware/amp_monitor.py`.

- `TestVoltageMonitor::test_scale` (parametrize: 0.0, 1.0, 2.5, 4.0, 5.0, -5.0) — KEEP — pins the EEL5000 manual's stated 1000:1 voltage-monitor ratio (p.1-3), including the rated-maximum boundary (5.0) and a sign check (-5.0); presumed keeper (manual worked example).
- `TestVoltageMonitor::test_beyond_rating_is_nan` — KEEP — distinct branch (the plausibility clamp past `AMP_MAX_KV*1.1`), separate from the scale itself.
- `TestVoltageMonitor::test_nan_in_nan_out` — KEEP — distinct guard branch.
- `TestCurrentMonitor::test_scale` (parametrize: 0.0, 0.1, 1.0, 2.0, -2.0, 10.0) — KEEP — pins the manual's stated 1 V = 10 mA current-monitor ratio, including the DC rating (2.0) and 4 ms peak rating (10.0) boundaries and a sign check; presumed keeper.
- `TestCurrentMonitor::test_nan_in_nan_out` — KEEP — distinct guard branch.
- `TestStatus::test_voltage_within_rating_ok` — KEEP — distinct classification branch.
- `TestStatus::test_voltage_over_rating` — KEEP — distinct classification branch.
- `TestStatus::test_current_dc_band_ok` — KEEP — distinct classification band.
- `TestStatus::test_current_peak_band` — KEEP — distinct classification band, the "legal only as a <4ms transient" band called out in the docstring.
- `TestStatus::test_current_over` — KEEP — distinct classification band (beyond peak rating / NaN).
- `TestFormatting::test_kv_above_one` — KEEP — distinct display branch.
- `TestFormatting::test_kv_below_one_shows_volts` — KEEP — distinct display branch (auto-scale to V).
- `TestFormatting::test_ma_above_one` — KEEP — distinct display branch.
- `TestFormatting::test_ma_below_one_shows_microamps` — KEEP — distinct display branch (auto-scale to µA).
- `TestFormatting::test_nan_dash` — KEEP — distinct branch (both formatters' NaN handling).
- `TestChannelMapIntegrity::test_amp_ains_do_not_overlap_log_amp_ains` — KEEP — the specific "whole no-interference guarantee" the class docstring names; a config-integrity contract an operator would notice as ghost readings if it broke.
- `TestChannelMapIntegrity::test_eight_amp_channels` — KEEP — distinct count invariant.
- `TestChannelMapIntegrity::test_twelve_total_channels_no_duplicates` — KEEP — distinct invariant (total count + uniqueness), not the same property as the 8-channel count.
- `TestChannelMapIntegrity::test_every_amp_has_both_monitors` — KEEP — distinct structural invariant (per-amp map completeness).
- `TestChannelMapIntegrity::test_every_amp_has_a_color` — KEEP — distinct invariant tying config to what an operator sees on screen (plot colour).

**Total: 20 (20 keep, 0 delete)**

---

## tests/test_amp_drive.py

Production module: `src/rbl/services/amp_drive.py` (services-layer, but exercised here as pure non-Qt code driven through `FakeGen`/`FakeRampEngine` stand-ins — no Qt, no widget — so it is audited under the "tests hardware-layer mathematics directly" carve-out per the ticket's explicit scoping note).

- `TestRampedCommands::test_command_dc_ramped_requires_attached_engine` — KEEP — distinct guard (no engine attached).
- `TestRampedCommands::test_command_dc_ramped_converts_kv_to_generator_volts` — KEEP — pins the kV→generator-volts conversion (`*1000/_AMP_GAIN`) that is sent toward hardware.
- `TestRampedCommands::test_command_dc_ramped_clamps_to_max_kv` — KEEP — distinct branch (clamp applied before ramping).
- `TestRampedCommands::test_command_ac_amplitude_ramped_requires_attached_engine` — KEEP — distinct guard, AC path.
- `TestRampedCommands::test_command_ac_amplitude_ramped_converts_peak_kv_to_vpp` — KEEP — pins the distinct peak-kV→Vpp conversion (`*2*1000/_AMP_GAIN`) for the AC ramp path.
- `TestRampedCommands::test_command_ac_amplitude_ramped_clamps_negative_to_zero` — KEEP — distinct branch (negative-amplitude clamp, AC-specific since DC allows negative but AC amplitude does not).
- `TestCommandDc::test_normal_value_passes_through` — KEEP — pins the unramped DC conversion sent to the generator.
- `TestCommandDc::test_clamps_above_max_kv` — KEEP — distinct branch (positive-direction clamp).
- `TestCommandDc::test_clamps_below_negative_max_kv` — KEEP — distinct branch (negative-direction clamp; a `min`/`max` bug could clamp only one side).
- `TestCommandDc::test_shape_is_dc` — KEEP — distinct assertion (waveform shape argument sent to hardware).
- `TestCommandDc::test_output_on_called` — KEEP — distinct assertion (output actually enabled).
- `TestCommandDc::test_re_raises_on_gen_exception` — KEEP — distinct branch (hardware exception propagation, safety-relevant).
- `TestCommandSine::test_exact_vpp_at_max_kv` — KEEP — pins the exact Vpp value at the rated maximum, cross-checked against `MAX_AMP_VPP`.
- `TestCommandSine::test_clamps_peak_above_max_kv` — KEEP — distinct branch (clamp above rating).
- `TestCommandSine::test_shape_is_Sine` — KEEP — distinct assertion (waveform shape).
- `TestCommandSine::test_freq_is_passed_through` — KEEP — distinct assertion (frequency argument).
- `TestCommandSine::test_offset_is_zero` — KEEP — distinct assertion (no DC offset on an AC command).
- `TestCommandSine::test_output_on_called` — KEEP — distinct assertion (output enabled).
- `TestCommandSine::test_negative_peak_clamped_to_zero` — KEEP — distinct branch (negative-peak clamp is unique to the AC path).
- `TestCommandSquare::test_shape_is_Square` — KEEP — distinct assertion for a third waveform shape.
- `TestCommandSquare::test_vpp_correct` — KEEP — distinct assertion (Vpp conversion, cross-checked independently of the sine test).
- `TestZeroAndOffAll::test_all_four_channels_zeroed` — KEEP — pins the safety-critical shutdown sequence's zeroing step.
- `TestZeroAndOffAll::test_all_four_outputs_off` — KEEP — distinct assertion (outputs disabled) from zeroing.
- `TestZeroAndOffAll::test_continues_past_one_set_waveform_fault` — KEEP — the specific fault-tolerance guarantee named in the method's own docstring (a failure on one channel must not block the others); safety-relevant.
- `TestRestoreAll::test_never_calls_output_on` — KEEP — the specific "restore does not re-arm the amplifier" safety behaviour named in the docstring.
- `TestRestoreAll::test_restores_waveform_parameters` — KEEP — distinct assertion (parameters actually restored, `None` entries skipped).
- `TestRestoreAll::test_skips_error_entries` — KEEP — distinct branch (an `"error"` snapshot entry must not be replayed to hardware).
- `TestSnapshotAll::test_returns_dict_for_all_labels` — KEEP — distinct structural assertion.
- `TestSnapshotAll::test_failed_get_state_returns_none` — KEEP — distinct branch (read failure on one channel does not propagate/crash).

**Total: 29 (29 keep, 0 delete)**

---

## tests/test_amp_trace.py

Production module: `src/rbl/hardware/amp_trace.py`.

- `TestDecimate::test_empty_returns_empty_tuple` — KEEP — distinct guard branch.
- `TestDecimate::test_none_returns_empty_tuple` — KEEP — distinct guard branch (None, not just empty array).
- `TestDecimate::test_short_wave_all_samples_returned` — KEEP — distinct branch (input shorter than `WAVE_POINTS`).
- `TestDecimate::test_long_wave_capped_at_wave_points` — KEEP — distinct branch (output length cap).
- `TestDecimate::test_returns_tuple_of_floats` — KEEP — distinct assertion (output type contract a consumer relies on).
- `TestDecimate::test_applies_kv_scaling` — KEEP — pins the `VOLTAGE_MONITOR_KV_PER_VOLT` conversion applied inside decimate.
- `TestDecimate::test_uniform_stride_preserves_shape_not_envelope` — KEEP — pins the specific "uniform stride, not min/max" algorithm choice the module docstring explains is load-bearing (shared time base for a pair).
- `TestDecimate::test_exactly_wave_points_samples_not_strided` — KEEP — distinct boundary (input length exactly equal to `WAVE_POINTS`).
- `TestDecimate::test_list_input_accepted` — KEEP — distinct input-type contract.
- `TestDecimate::test_single_sample` — KEEP — distinct boundary (single-sample input, checked against the exact scaled value).
- `TestStateManagement::test_initial_history_empty` — KEEP — distinct baseline state assertion.
- `TestStateManagement::test_push_accumulates_history` — KEEP — distinct behaviour (accumulation across windows).
- `TestStateManagement::test_clear_resets_history` — KEEP — distinct behaviour (reset), safety-relevant on a stream restart per the docstring.
- `TestStateManagement::test_push_missing_waveform_key` — KEEP — distinct branch (malformed channel dict silently skipped).
- `TestStateManagement::test_push_absent_channel_skipped` — KEEP — distinct branch (channel simply absent from the payload).
- `TestTracesStructure::test_no_history_returns_empty_dict` — KEEP — distinct branch (no data yet).
- `TestTracesStructure::test_returns_dict_keyed_by_amp_labels` — KEEP — distinct structural field (dict keys).
- `TestTracesStructure::test_each_entry_is_three_tuple` — KEEP — distinct structural field (tuple arity), what the Overview unpacks.
- `TestTracesStructure::test_wave_is_tuple` — KEEP — distinct structural field (element 0 type).
- `TestTracesStructure::test_wave_not_empty` — KEEP — distinct structural field (non-emptiness), separate from type.
- `TestTracesStructure::test_wave_length_at_most_wave_points` — KEEP — distinct structural field (length bound), separate from non-emptiness.
- `TestTracesStructure::test_span_positive` — KEEP — distinct structural field (element 1).
- `TestTracesStructure::test_x_pair_both_present` — KEEP — distinct behavioural claim (paired channels both surface).
- `TestTracesStructure::test_y_pair_absent_when_not_pushed` — KEEP — distinct claim (unpushed axis does not fabricate data), complementary to the X-pair test.
- `TestTracesStructure::test_after_clear_returns_empty` — KEEP — distinct branch (post-clear state).
- `TestTracesStructure::test_single_channel_only_in_history` — KEEP — distinct branch (solo-channel profile, the `_groups` fallback path named in the docstring).
- `TestTracesStructure::test_freq_finite_after_enough_cycles` — KEEP — distinct field (element 2, the caption frequency) under a condition where it should be measurable.
- `TestTracesStructure::test_payload_without_sample_period_uses_window_samples` — KEEP — distinct branch (the `sample_period`-omitted fallback path in `traces()`).
- `TestTracesStructure::test_flat_signal_does_not_raise` — KEEP — distinct branch (no-cycle-found fallback to raw window).

**Total: 29 (29 keep, 0 delete)**

---

## tests/test_current_monitor.py

Production module: `src/rbl/hardware/current_monitor.py`.

- `TestVoltageToCurrentModel0_6V::test_lower_endpoint_is_1nA` — KEEP — pins the log-amp formula at one calibrated endpoint (worked example: 0 V = 1 nA).
- `TestVoltageToCurrentModel0_6V::test_midpoint_is_1uA` — KEEP — distinct point on the log curve (a bug in the log-scaling constant would not necessarily show at the endpoints but would show at the midpoint).
- `TestVoltageToCurrentModel0_6V::test_upper_endpoint_is_1mA` — KEEP — distinct calibrated endpoint (6 V = 1 mA).
- `TestVoltageToCurrentModel0_6V::test_below_range_is_nan` — KEEP — distinct guard branch.
- `TestVoltageToCurrentModel0_6V::test_above_range_is_nan` — KEEP — distinct guard branch (opposite side).
- `TestVoltageToCurrentModel0_6V::test_just_within_lower_tolerance` — KEEP — pins the exact 0.5 V tolerance-band boundary (inside).
- `TestVoltageToCurrentModel0_6V::test_just_outside_lower_tolerance` — KEEP — pins the same boundary from just outside; together the two pin the exact threshold, not a repeat of either alone.
- `TestVoltageToCurrentModel0_6V::test_monotonic_ascending` — KEEP — distinct invariant (monotonicity across the whole range) that no single-point test covers.
- `TestVoltageToCurrentModel9_3V::test_v9_is_1nA` — KEEP — verifies the same formula generalizes correctly to the lab's other real calibration variant (descending 9-3V models), a genuinely different code path (`v_min`/`v_max` swap) from the ascending model.
- `TestVoltageToCurrentModel9_3V::test_v6_is_1uA` — KEEP — distinct point on the descending curve.
- `TestVoltageToCurrentModel9_3V::test_v3_is_1mA` — KEEP — distinct calibrated endpoint.
- `TestVoltageToCurrentModel9_3V::test_below_min_is_nan` — KEEP — distinct guard branch on the descending model.
- `TestVoltageToCurrentModel9_3V::test_above_max_is_nan` — KEEP — distinct guard branch, opposite side.
- `TestVoltageToCurrentModel9_3V::test_monotonic_descending` — KEEP — distinct invariant (descending monotonicity), not covered by the ascending model's test.
- `TestFormatCurrent::test_nA_range` — KEEP — pins the nA display bucket.
- `TestFormatCurrent::test_uA_range` — KEEP — pins the µA display bucket.
- `TestFormatCurrent::test_mA_range` — KEEP — pins the mA display bucket.
- `TestFormatCurrent::test_nan_shows_dash` — KEEP — distinct guard branch.
- `TestFormatCurrent::test_none_shows_dash` — KEEP — distinct guard branch (None, not NaN).
- `TestFormatCurrent::test_boundary_1uA_is_not_nA` — KEEP — pins the exact bucket boundary at 1e-6, a value shared with `test_uA_range` but asserting the complementary/negative fact (excludes the wrong unit label), which is real boundary documentation not covered by the positive check alone.
- `TestFormatCurrent::test_boundary_1mA_is_not_uA` — KEEP — pins the exact bucket boundary at 1e-3 from the other side.
- `TestFormatCurrent::test_small_nA_values` — **DELETE** — duplicates `test_nA_range`: both simply assert `"nA" in s` for a value deep inside the nA bucket (5e-10 vs 1e-9), nowhere near the 1e-6 boundary already pinned by `test_boundary_1uA_is_not_nA`; they would pass or fail together on any change to that branch and this case adds no new detection power.
- `TestFormatCurrent::test_mid_uA_values` — **DELETE** — duplicates `test_uA_range`: both simply assert `"µA"/"uA"/"µ" in s` for a value deep inside the µA bucket (50e-6 vs 1e-6), adding nothing beyond what `test_uA_range` and the boundary tests already establish.
- `TestBeamCentering::test_equal_currents_is_zero` — KEEP — pins the centered case.
- `TestBeamCentering::test_plus_dominant_is_positive` — KEEP — distinct branch (sign of imbalance).
- `TestBeamCentering::test_minus_dominant_is_negative` — KEEP — distinct branch, opposite sign.
- `TestBeamCentering::test_antisymmetric` — KEEP — distinct algebraic invariant, not implied by the two sign tests alone.
- `TestBeamCentering::test_all_on_plus_is_plus1` — KEEP — distinct boundary (full saturation), not the same as the general "positive" test.
- `TestBeamCentering::test_all_on_minus_is_minus1` — KEEP — distinct boundary, opposite saturation.
- `TestBeamCentering::test_zero_total_is_nan` — KEEP — distinct guard branch (division-by-zero).
- `TestBeamCentering::test_nan_input_plus` — KEEP — distinct guard branch.
- `TestBeamCentering::test_nan_input_minus` — KEEP — distinct guard branch, the other argument.
- `TestBeamCentering::test_result_bounded` — KEEP — distinct invariant (output range) checked across several ratios in one function.
- `TestRollingBuffer::test_empty_snapshot` — KEEP — distinct baseline state.
- `TestRollingBuffer::test_single_append` — KEEP — distinct basic behaviour.
- `TestRollingBuffer::test_capacity_capped` — KEEP — distinct behaviour (overflow past capacity).
- `TestRollingBuffer::test_oldest_samples_overwritten` — KEEP — distinct behaviour (ring-buffer wraparound specifics), separate from the capacity-cap count.
- `TestRollingBuffer::test_snapshot_sorted_by_time` — KEEP — distinct behaviour (sort-by-time despite out-of-order wraparound insertion — real correctness property for a live plot).
- `TestRollingBuffer::test_latest_returns_most_recent` — KEEP — distinct method (`latest()`, not `snapshot()`).
- `TestRollingBuffer::test_latest_empty_is_nan` — KEEP — distinct guard branch on `latest()`.
- `TestRollingBuffer::test_thread_safety` — KEEP — distinct concern (the module's stated "thread-safe" contract, concurrent writers).
- `TestRollingBuffer::test_values_match_times` — KEEP — distinct correctness property (values stay paired with their correct time after wraparound/sorting).

**Total: 42 (40 keep, 2 delete)**

---

## tests/test_hv_interlock.py

Production module: `src/rbl/hardware/hv_interlock.py`.

- `TestMaxPermittedKv::test_ladder` — KEEP — pins `max_permitted_kv` against each distinct tier of the `HV_PRESSURE_LIMITS` ladder (5 parametrize cases, each a different config tier that could independently break).
- `TestMaxPermittedKv::test_lockout_boundary_is_inclusive` — **DELETE** — exact duplicate of `test_ladder`'s `(1e-3, 0.0)` parametrize case: `HV_PRESSURE_ABSOLUTE_LOCKOUT_TORR == 1e-3`, so this calls `max_permitted_kv(1e-3)` expecting `0.0`, the identical input/output/branch already asserted by the parametrized test — no hypothetical bug would be caught by one and not the other.
- `TestInterlockStatus::test_ok_within_ceiling` — KEEP — exercises the "ok" branch of `interlock_status` (commanded within ceiling).
- `TestInterlockStatus::test_warn_above_ceiling_below_lockout` — KEEP — exercises the "warn" branch (over ceiling, under lockout).
- `TestInterlockStatus::test_block_at_absolute_lockout` — KEEP — exercises the absolute-lockout "block" branch with a non-zero commanded voltage.
- `TestInterlockStatus::test_block_at_absolute_lockout_even_with_zero_commanded` — KEEP — distinct from the previous test: it guards specifically against a plausible future defect that special-cases `commanded_kv == 0` as always safe (the module docstring calls this out by name: "never merely warn... even at 0 kV commanded"); a bug conditioning the block on `commanded_kv > 0` would pass the 0.5 kV test but fail only this one.
- `TestInterlockStatus::test_stale_reading_blocks_regardless_of_pressure_value` — KEEP — exercises the `pressure_known=False` block branch with a non-zero commanded voltage, and additionally asserts the reason text mentions "stale"/"unavailable".
- `TestInterlockStatus::test_stale_reading_blocks_even_zero_command` — KEEP — distinct hazard from the previous test: guards against a defect that treats a zero-kV command as exempt from the staleness block (the docstring: "the absence of a reading is never treated as evidence of anything"); not caught by the non-zero-commanded test.

**Total: 8 (7 keep, 1 delete)**

---

## tests/test_hv_safety_config.py

Production module: `src/rbl/config/hv_safety_config.py`.

- `test_ladder_is_sorted_by_decreasing_permitted_voltage` — KEEP — asserts a real invariant on the config data (`HV_PRESSURE_LIMITS` ordering) that `max_permitted_kv`'s scan loop implicitly depends on.
- `test_ladder_never_exceeds_amplifier_rating` — KEEP — asserts a genuine safety ceiling (5 kV amplifier rating) on the config data; not a framework guarantee.
- `test_lockout_is_looser_than_every_ladder_requirement` — KEEP — asserts the lockout pressure is never tighter than any ladder step, a real relational invariant between two separate constants.
- `test_stale_timeout_is_positive` — KEEP — guards against a degenerate/regressed timeout value; this constant gates a safety block path in `hv_interlock`.

**Total: 4 (4 keep, 0 delete)**

---

## tests/test_galil_protocol.py

Production module: `src/rbl/hardware/galil_driver.py`.

- `TestCmdProtocol::test_success_returns_stripped_text` — KEEP — verifies the ':'-terminated success reply is decoded/stripped correctly.
- `TestCmdProtocol::test_success_appends_carriage_return` — KEEP — verifies the wire framing (`\r` appended) sent to the socket.
- `TestCmdProtocol::test_empty_success_response` — KEEP — edge case: empty body between prefix and terminator.
- `TestCmdProtocol::test_error_raises_galil_error_with_code` — KEEP — verifies error-code extraction from the TC1 text.
- `TestCmdProtocol::test_error_sends_tc1` — KEEP — verifies the driver follows up a `?` with `TC1` per protocol.
- `TestCmdProtocol::test_error_without_numeric_code_has_none_code` — KEEP — edge case: non-numeric TC1 reason text.
- `TestCmdProtocol::test_closed_connection_raises_connection_error` — KEEP — socket-closed edge case.
- `TestCmdProtocol::test_timeout_raises_connection_error` — KEEP — socket-timeout edge case.
- `TestCmdProtocol::test_cmd_when_not_connected_raises` — KEEP — guards the "not connected" precondition.
- `TestReads::test_get_position_parses_negative_float` — KEEP — verifies negative-value parsing/rounding of `MG _RPx`.
- `TestReads::test_get_position_rounds` — KEEP — distinct rounding-direction case (`.7` rounds up), not covered by the negative-value case.
- `TestReads::test_is_moving_true` — KEEP — with `test_is_moving_false`, forms the minimum pair needed to pin the `>0.5` threshold direction; deleting either loses the ability to catch a flipped comparison.
- `TestReads::test_is_moving_false` — KEEP — see above.
- `TestReads::test_is_motor_off_true` — KEEP — same threshold-direction pairing as `is_moving`.
- `TestReads::test_is_motor_off_false` — KEEP — see above.
- `TestReads::test_soft_limits` — KEEP — verifies `FL`/`BL` are read into the correct dict keys.
- `TestReads::test_a_tripped_switch_reads_low` — KEEP — the documented, bench-observed "reads LOW when tripped" polarity fact (mixed fwd-tripped/rev-clear/home-tripped case), the load-bearing invariant of this driver's switch reading.
- `TestReads::test_a_clear_axis_reads_clear_on_every_switch` — KEEP — the counter-observation that caught the original defect (all-clear axis reads "Idle").
- `TestReads::test_the_polarity_is_configurable_not_hard_coded` — KEEP — verifies polarity is driven by `hardware_config.LIMIT_SWITCH_TRIPPED_IS_LOW`, not hard-coded, via monkeypatch.
- `TestReads::test_switches_all_open` — **DELETE** — duplicates `test_a_clear_axis_reads_clear_on_every_switch` byte-for-byte (same all-"1" responses, same all-`False` expected dict), only the axis letter changes (A vs D); `get_switch_states` does not branch on which axis letter is used (axis is only string-interpolated into the command name), so no hypothetical defect would be caught by one and not the other.
- `TestMotionCommands::test_move_absolute_prefix_and_begin` — KEEP — parametrized over all 4 axes; each axis exercises a genuinely different computed prefix (`"," * "ABCD".index(axis)`), so an off-by-one in the index mapping would show up on only some axes, not all together.
- `TestMotionCommands::test_move_relative_prefix_and_begin` — KEEP — same reasoning as above, distinct command (`PR`).
- `TestMotionCommands::test_jog_start_positive` — KEEP — verifies `JG`/`BG` wire format for a positive speed.
- `TestMotionCommands::test_jog_start_negative_on_axis_c` — KEEP — distinct: negative speed and a non-zero-index axis prefix together.
- `TestMotionCommands::test_stop_single_axis` — KEEP.
- `TestMotionCommands::test_define_zero` — KEEP.
- `TestMotionCommands::test_enable_disable` — KEEP — verifies both `SH` and `MO` in one call.
- `TestMotionCommands::test_set_speed` — KEEP.
- `TestMotionCommands::test_set_accel_sets_both_ac_and_dc` — KEEP — verifies both `AC` and `DC` are sent from one call.
- `TestMotionCommands::test_begin_home_sequence` — KEEP — pins the full 5-command HM sequence and order.
- `TestMotionCommands::test_begin_home_sets_the_two_stages_separately` — KEEP — verifies `fine_speed` produces a distinct `HV` value from `SP`, a real two-stage-homing behavior documented in the driver.
- `TestMotionCommands::test_home_velocity_is_positional_per_axis` — KEEP — verifies `set_home_velocity`'s own prefix handling (separate code path from `begin_home`).
- `TestMotionCommands::test_begin_home_jog_is_negative` — KEEP — documents/guards the specific "JG does not steer direction" design fact on a second axis; thin overlap with `test_begin_home_sequence`'s embedded `"JG -900"` check but retained since it carries its own documented rationale and is a single test, not a repeated batch.
- `TestAbort::test_abort_sends_ab` — KEEP.
- `TestAbort::test_abort_never_raises_even_on_error` — KEEP — verifies the e-stop swallows `GalilError`.
- `TestAbort::test_abort_never_raises_when_disconnected` — KEEP — verifies the e-stop swallows disconnection too.
- `TestStartupSequence::test_full_sequence_order_and_content` — KEEP — pins the full documented startup command order.
- `TestStartupSequence::test_sequence_scales_to_two_axes` — KEEP — verifies per-axis vector length scales with axis count.
- `TestStartupSequence::test_sequence_aborts_on_error` — KEEP — verifies a mid-sequence error propagates rather than being swallowed.
- `TestLifecycle::test_disconnect_closes_socket` — KEEP.
- `TestLifecycle::test_disconnect_is_idempotent` — KEEP.
- `TestLifecycle::test_thread_safety_serializes_commands` — KEEP — drives real concurrent threads against the lock and asserts `max_active == 1`; a genuine thread-safety regression test, not framework-guaranteed.
- `TestAxisVector::test_all_four_axes` — KEEP — pins the pure `axis_vector` helper's wire format directly.
- `TestAxisVector::test_a_subset_keeps_its_positions` — KEEP — three sub-cases (AC/B/D) each pin a distinct positional-comma pattern.
- `TestAxisVector::test_negative_values` — KEEP — distinct: verifies negative numbers are not mishandled by the string join.
- `TestMultiAxisMotion::test_begin_home_multi_is_one_hm_and_one_bg` — KEEP — pins the full multi-axis home sequence.
- `TestMultiAxisMotion::test_begin_home_multi_defaults_hv_to_the_search_speed` — KEEP — distinct default-value behavior.
- `TestMultiAxisMotion::test_jog_start_multi` — KEEP.
- `TestMultiAxisMotion::test_move_relative_multi` — KEEP.
- `TestMultiAxisMotion::test_define_zero_multi` — KEEP.
- `TestMultiAxisMotion::test_speed_restores_are_vectors_too` — KEEP — verifies two related multi-axis setters in one call.
- `TestMultiAxisMotion::test_stop_takes_an_axis_mask` — KEEP.

**Total: 52 (51 keep, 1 delete)**

---

## tests/test_vgc083_parse.py

Production module: `src/rbl/hardware/vgc083_driver.py`.

- `TestParseReading::test_normal_ig_reading` — KEEP — canonical "OK" numeric-parse path through `_parse_reading`.
- `TestParseReading::test_normal_cg_reading` — **DELETE** — duplicates `test_normal_ig_reading`: `_parse_reading` does not branch on `channel` at all (it is stored unchanged into the returned dataclass), and the numeric conversion is a plain `float(raw)` with no channel- or exponent-sign-specific logic, so this exercises the identical code path with no hypothetical defect distinguishable from the IG case.
- `TestParseReading::test_sentinel_ig_never_float` — KEEP — canonical, most-documented sentinel test ("THE most important test").
- `TestParseReading::test_sentinel_cg1_never_float` — **DELETE** — duplicate of `test_sentinel_ig_never_float`; the sentinel check (`raw == _SENTINEL`) runs identically regardless of `channel`, so this and the IG version would pass or fail together for any change to the sentinel logic.
- `TestParseReading::test_sentinel_cg2_never_float` — **DELETE** — same duplication as above, against the same channel-agnostic branch.
- `TestParseReading::test_sentinel_ai_never_float` — **DELETE** — same duplication as above.
- `TestParseReading::test_sentinel_raw_preserved` — KEEP — distinct assertion (the `raw` field, not `pressure`/`state`) not covered by the other sentinel tests.
- `TestParseReading::test_unknown_text` — KEEP — distinct "ERROR" branch (unparseable, non-sentinel text).
- `TestVgc083Driver::test_normal_reading` — KEEP — end-to-end through `Vgc083.read_channel` and the 13-char frame decoder, not just `_parse_reading`.
- `TestVgc083Driver::test_sentinel_via_driver` — KEEP — end-to-end sentinel guard through the driver/framing layer, a different code path from the unit-level `_parse_reading` tests.
- `TestVgc083Driver::test_invalid_reply` — KEEP — `'?'`-prefix protocol error path.
- `TestVgc083Driver::test_timeout_raises` — KEEP — timeout-to-exception mapping.
- `TestVgc083Driver::test_wrong_length_response_logs_warning` — KEEP — real protocol diagnostic (RS-485-vs-RS-232 warning) tied to a documented wiring trap.
- `TestVgc083Driver::test_ig_status_on` — KEEP.
- `TestVgc083Driver::test_ig_status_off` — KEEP — with the previous test, the minimum true/false pair for the `body.startswith("1")` predicate.
- `TestVgc083Driver::test_ig_fault_ok` — KEEP — the "00 -> ST_OK" no-fault case.
- `TestVgc083Driver::test_ig_fault_codes` — KEEP — loops over all 8 documented RSIG bitmask codes, but each code exercises a *different* dict entry/bit position in `_RSIG_CODES`; a wrong mapping for one code would not be caught by another, so this is not the "all pass/fail together" pattern despite being a hand-written loop.
- `TestVgc083Driver::test_degas_on` — KEEP.
- `TestVgc083Driver::test_degas_off` — KEEP — true/false pair with the previous test.
- `TestSentinelNeverFloat::test_sentinel_all_channels` — **DELETE** — a `pytest.mark.parametrize` over the same 4 channels already covered by `TestParseReading`'s four sentinel tests, asserting the identical `pressure is None` / `state == "OFF_OR_OVERRANGE"` / `raw == _SENTINEL` outcomes; doubly redundant since it repeats ground already covered twice (by design) once per channel, none of which the parser actually distinguishes.

**Total: 20 (15 keep, 5 delete)**

---

## tests/test_xgs600_parse.py

Production module: `src/rbl/hardware/xgs600_driver.py`.

- `TestParsePressure::test_ok_scientific` — KEEP — canonical numeric-OK path.
- `TestParsePressure::test_ok_negative_exponent` — **DELETE** — duplicates `test_ok_scientific`: `_parse_pressure`'s numeric path is a bare `float(raw)` with no custom exponent-sign handling, so a different exponent value exercises no different code in the module (only Python's own `float()` parser, which is a framework guarantee, not this module's logic).
- `TestParsePressure::test_off_state` — KEEP — distinct `_STATE_MAP` entry.
- `TestParsePressure::test_under_state` — KEEP — distinct `_STATE_MAP` entry.
- `TestParsePressure::test_over_state` — KEEP — distinct `_STATE_MAP` entry.
- `TestParsePressure::test_no_cable` — KEEP — distinct `_STATE_MAP` entry ("NO CABLE" alt-spelling).
- `TestParsePressure::test_error_code` — KEEP — distinct regex branch (`E\d+`).
- `TestParsePressure::test_never_coerce_text_to_zero` — **DELETE** — re-loops the exact same 5 inputs (`"OFF", "UNDER", "NO CABLE", "OVER", "E01"`) already individually covered by `test_off_state`, `test_under_state`, `test_no_cable`, `test_over_state`, and `test_error_code`; the added `p != 0.0` check is trivially implied by `p is None` (`None != 0.0` is always true in Python) and adds no independent signal.
- `TestReadAll::test_three_board_dump` — KEEP — real multi-channel `read_all()` integration, distinct from the unit-level `_parse_pressure` tests (exercises the field/channel zip).
- `TestReadAll::test_dump_with_off_and_under` — KEEP — mixed numeric + text-state fields through the same zip/alignment logic.
- `TestReadAll::test_protocol_error_ff` — KEEP.
- `TestReadAll::test_timeout_raises_xgs_timeout` — KEEP.
- `TestReadAll::test_field_count_mismatch_raises` — KEEP — exercises the re-discovery-then-raise logic, a real, non-trivial protocol edge case.

**Total: 13 (11 keep, 2 delete)**

---

## tests/test_tds_waveform.py

Production module: `src/rbl/hardware/tds2012_driver.py`.

- `TestDecodeIeee488Block::test_single_digit_length` — KEEP — single-digit length-field header.
- `TestDecodeIeee488Block::test_four_digit_length` — KEEP — distinct code path: multi-digit length field (`int(raw[2:2+n_digits])` with `n_digits=4`), not exercised by the single-digit case.
- `TestDecodeIeee488Block::test_leading_whitespace_ignored` — KEEP — distinct: the `raw.find(b"#")` prefix-skip.
- `TestDecodeIeee488Block::test_missing_hash_raises` — KEEP.
- `TestDecodeIeee488Block::test_indefinite_block_raises` — KEEP — distinct `#0` branch.
- `TestDecodeIeee488Block::test_truncated_header_raises` — KEEP — distinct branch (header shorter than declared).
- `TestDecodeIeee488Block::test_non_numeric_length_raises` — KEEP — distinct branch (`int()` ValueError).
- `TestDecodeIeee488Block::test_truncated_payload_raises` — KEEP — distinct branch (payload shorter than declared length).
- `TestDecodeIeee488Block::test_extra_bytes_after_payload_ignored` — KEEP — distinct: trailing garbage must not affect the returned payload.
- `TestParsePreamble::test_typical_preamble` — KEEP — pins the parser against a realistic instrument response (values from the manual-style example).
- `TestParsePreamble::test_float_fields_are_float` — KEEP — asserts the actual Python type of the coerced fields, a distinct contract from the numeric-value check in `test_typical_preamble`.
- `TestParsePreamble::test_int_fields_are_int` — KEEP — same reasoning, for the int-typed fields.
- `TestParsePreamble::test_empty_string_raises` — KEEP.
- `TestParsePreamble::test_no_colon_tokens_skipped` — KEEP — distinct: a malformed token must be skipped, not raise.
- `TestSamplesToVolts::test_zero_raw_gives_yzero` — KEEP — isolates the base case (all terms zero/no-op).
- `TestSamplesToVolts::test_positive_raw_byte` — KEEP — isolates the `ymult` scaling term.
- `TestSamplesToVolts::test_yoff_subtracted` — KEEP — isolates the `yoff` subtraction term.
- `TestSamplesToVolts::test_yzero_offset` — KEEP — isolates the `yzero` addition term.
- `TestSamplesToVolts::test_negative_signed_byte` — KEEP — isolates signed-byte decoding.
- `TestSamplesToVolts::test_unsigned_byte` — KEEP — isolates `BN_FMT=RP` unsigned decoding, a distinct branch from signed.
- `TestSamplesToVolts::test_two_byte_msb` — KEEP — isolates 2-byte MSB endianness branch.
- `TestSamplesToVolts::test_two_byte_lsb` — KEEP — isolates 2-byte LSB endianness branch, distinct from MSB.
- `TestSamplesToVolts::test_multiple_samples` — KEEP — verifies the list comprehension over more than one sample.
- `TestSamplesToVolts::test_unsupported_byt_nr_raises` — KEEP — error branch for `BYT_NR` other than 1/2.
- `TestSamplesToVolts::test_typical_scope_preamble_scaling` — KEEP — explicitly reproduces the TDS 2012 manual's worked scaling example (presumed keeper per the ticket's manual-example carve-out).
- `TestTds2012Driver::test_identify_ok` — KEEP.
- `TestTds2012Driver::test_identify_timeout` — KEEP.
- `TestTds2012Driver::test_identify_not_tektronix_raises` — KEEP — distinct validation branch on the identity string.
- `TestTds2012Driver::test_query_error_response_raises` — KEEP.
- `TestTds2012Driver::test_read_preamble_ok` — KEEP.
- `TestTds2012Driver::test_acquire_waveform_returns_correct_payload` — KEEP — verifies the driver's own IEEE-488.2 read/assembly loop returns the exact bytes sent.
- `TestTds2012Driver::test_acquire_waveform_voltage_conversion` — KEEP — integration check that the acquired payload and preamble are wired correctly into `samples_to_volts` (distinct from the unit-level `TestSamplesToVolts` cases, which construct their preamble/data by hand rather than through the driver).
- `TestTds2012Driver::test_acquire_waveform_timeout_raises` — KEEP.
- `TestTds2012Driver::test_curve_missing_hash_raises` — KEEP.
- `TestTds2012Driver::test_zero_nr_pt_raises` — KEEP — distinct guard (bad preamble caught before the CURVE? transfer).
- `TestTds2012Driver::test_single_acquisition_sends_correct_commands` — KEEP.
- `TestTds2012Driver::test_run_continuous_sends_correct_commands` — KEEP — distinct command sequence from single-acquisition.
- `TestIdentifyIsQuiet::test_identify_does_not_log_at_info` — KEEP — regression test for a real documented incident (idle keepalive flooding the log at INFO).
- `TestIdentifyIsQuiet::test_the_keepalive_interval_is_not_walked_back` — KEEP — reads a private module constant (`scope_worker._KEEPALIVE_S`) but guards a real, documented incident (log flooding from an overly short keepalive); a downstream operator directly experiences the effect of this constant being walked back.

**Total: 39 (39 keep, 0 delete)**

---

## tests/test_labjack_driver.py

Production module: `src/rbl/hardware/labjack_driver.py`.

- `TestConnect::test_connect_opens_t7` — KEEP.
- `TestConnect::test_connect_configures_fourteen_ains` — KEEP — verifies all 14 AIN configuration writes and their names.
- `TestConnect::test_connect_sets_pm10v_range` — KEEP — verifies the specific ±10 V range value, which the driver's docstring ties to a real clipping hazard (100 mA/4 ms current-monitor transient).
- `TestConnect::test_connect_single_ended_negative_channel_is_199` — KEEP — verifies the specific single-ended negative-channel value.
- `TestConnect::test_reconnect_closes_previous_handle` — KEEP — real resource-leak guard on reconnect.
- `TestConnect::test_connect_raises_when_library_unavailable` — KEEP.
- `TestRead::test_read_channels_returns_named_dict` — KEEP.
- `TestRead::test_read_channels_is_single_batched_call` — KEEP — guards the single-round-trip design the module docstring calls load-bearing (two `eReadNames` calls from two threads would collide).
- `TestRead::test_read_custom_channel_subset` — KEEP — distinct: a non-default channel subset.
- `TestRead::test_read_channels_raises_when_not_connected` — KEEP.
- `TestSerialAndDisconnect::test_serial_number_when_connected` — KEEP.
- `TestSerialAndDisconnect::test_serial_number_dash_when_disconnected` — KEEP — distinct disconnected-sentinel branch.
- `TestSerialAndDisconnect::test_serial_number_handles_read_error` — KEEP — distinct exception-swallowing branch.
- `TestSerialAndDisconnect::test_disconnect_closes_and_clears_handle` — KEEP.
- `TestSerialAndDisconnect::test_disconnect_when_not_connected_is_safe` — KEEP — idempotency guard.
- `TestSerialAndDisconnect::test_disconnect_swallows_close_errors` — KEEP — distinct exception-swallowing branch on `close()`.
- `TestSerialAndDisconnect::test_disconnect_stops_stream_before_closing` — KEEP — explicit regression test for a documented real defect (closing while streaming strands the T7 until unplugged).
- `TestSerialAndDisconnect::test_stop_stream_swallows_not_running` — KEEP — distinct exception-swallowing branch on `eStreamStop`.
- `TestSerialAndDisconnect::test_stop_stream_when_not_connected_is_safe` — KEEP — distinct no-handle guard.

**Total: 19 (19 keep, 0 delete)**

---

## tests/test_hardware.py

Production modules read: `src/rbl/hardware/galil_driver.py`, `src/rbl/hardware/labjack_driver.py`, `src/rbl/config/hardware_config.py`.

- `TestGalilControllerLifecycle::test_not_connected_by_default` — KEEP — tests the real initial value of the `connected` property (`sock is None`), a lifecycle contract other code (Beamline's connect/reconnect guards) relies on.
- `TestGalilControllerLifecycle::test_disconnect_when_not_connected_is_safe` — KEEP — pins the "disconnect never raises" contract, load-bearing for shutdown/teardown ordering (CLAUDE.md §4).
- `TestGalilControllerLifecycle::test_cmd_raises_when_not_connected` — KEEP — direct test of `cmd()`'s own `if not self.connected: raise ConnectionError` guard, the actual source of the check.
- `TestGalilControllerLifecycle::test_get_position_raises_when_not_connected` — **DELETE** — `get_position()` has no connectivity check of its own; it calls `self.cmd(...)` immediately, so this exercises the exact same guard clause as `test_cmd_raises_when_not_connected` and would fail or pass together with it. Pure duplication through a zero-logic wrapper.
- `TestGalilControllerLifecycle::test_is_moving_raises_when_not_connected` — **DELETE** — same duplication: `is_moving()` also just delegates to `self.cmd(...)` with no independent check; duplicates `test_cmd_raises_when_not_connected`.
- `TestGalilError::test_error_stores_code` — KEEP — tests the exception's real constructor contract (`.code` attribute), used when a Galil error surfaces to an operator/log.
- `TestGalilError::test_error_stores_msg` — KEEP — distinct field (`.msg`) from the same constructor contract.
- `TestGalilError::test_str_representation_includes_command` — KEEP — pins that the command that failed is visible in the string an operator/log would see, distinct from the field-level tests.
- `TestGalilError::test_code_can_be_none` — KEEP — pins the default (`code=None`) as distinct from `code=0`, a real distinction downstream code can check.
- `TestGalilControllerMocked::test_get_position_parses_integer` — KEEP — exercises real response parsing (`"12345.000"` → `12345`) through the full mocked-socket wire protocol.
- `TestGalilControllerMocked::test_is_moving_true_when_1` — KEEP — real threshold parsing (`>0.5`) on one boundary.
- `TestGalilControllerMocked::test_is_moving_false_when_0` — KEEP — the other side of the same threshold; not a duplicate of the `_true_` case since it exercises the opposite branch.
- `TestGalilControllerMocked::test_connected_property_true_with_socket` — KEEP — complements the default-False test with the "socket present" state.
- `TestGalilControllerMocked::test_disconnect_clears_socket` — KEEP — distinct state transition (connected → disconnected clears `.sock`), not covered by the other two `connected`-property tests.
- `TestSlitConfig::test_four_axis_letters` — **DELETE** — asserts `len(AXIS_LETTERS) == 4` against a hardcoded 4-entry dict (`AXIS_NAMES`); no computation, restates the module's own literal.
- `TestSlitConfig::test_axis_names_match_letters` — **DELETE** — tautological: `AXIS_LETTERS = list(AXIS_NAMES.keys())` in the source, so "every letter in AXIS_LETTERS is a key of AXIS_NAMES" is guaranteed by that assignment itself, not by any logic under test.
- `TestSlitConfig::test_labjack_channel_map_not_empty` — **DELETE** — asserts a hardcoded 4-entry dict literal is non-empty; no computation, restates the literal.
- `TestSlitConfig::test_counts_to_mm_round_trip` — KEEP — real formula math (`mm_to_counts` + `counts_to_mm`) with a physically-justified tolerance (one motor step), matches the ticket's "pin a formula" category.
- `TestSlitConfig::test_zero_counts_is_physical_gap_offset` — KEEP — pins the `MM_ZERO_OFFSET` physical fact through the real `counts_to_mm` formula.
- `TestSlitConfig::test_positive_counts_positive_mm` — KEEP — catches a sign-convention regression that the round-trip test could not (a wholesale sign flip in `STEPS_PER_MM` would still round-trip against itself).
- `TestSlitConfig::test_default_speed_positive` — **DELETE** — asserts a hardcoded numeric literal (`DEFAULT_SPEED_COUNTS_PER_SEC`) is `>0`; no computation, restates the constant's own definition.
- `TestSlitConfig::test_default_accel_positive` — **DELETE** — same pattern as above for `DEFAULT_ACCEL_COUNTS_PER_SEC2`.
- `TestSlitConfig::test_slit_labels_in_channel_map` — KEEP — checks the actual wiring-map *values* (`{"X+","X-","Y+","Y-"}`) against expected physical labels, protecting against a mislabelled slit (a real, operator-visible defect class), distinct from the bare presence/length checks above.
- `TestLabJackT7Lifecycle::test_not_connected_by_default` — KEEP — real initial-state contract for `.connected`, used by Beamline's connect guard.
- `TestLabJackT7Lifecycle::test_disconnect_when_not_connected_is_safe` — KEEP — pins "disconnect never raises," load-bearing for shutdown ordering.
- `TestLabJackT7Lifecycle::test_read_channels_raises_when_not_connected` — **DELETE** — the `try/except Exception: pass` swallows any exception, and the non-exception branch only checks `isinstance(result, dict)` (not that it's empty, despite the comment). The actual driver always raises `LabJackError`, which this test happily accepts — but it would equally accept a return of an arbitrary non-empty dict of fabricated voltages, so it cannot distinguish correct behaviour from a real regression.

**Total: 26 (18 keep, 8 delete)**

---

## tests/test_funcgen_driver.py

Production module: `src/rbl/hardware/funcgen_driver.py`.

- `TestDiscover::test_returns_empty_when_pyvisa_unavailable` — KEEP — distinct guard branch (`PYVISA_AVAILABLE` False).
- `TestDiscover::test_returns_empty_when_resource_manager_raises` — KEEP — distinct exception-handling branch around `ResourceManager()`.
- `TestDiscover::test_skips_non_usb_resources` — KEEP — distinct filter (`res.upper().startswith("USB")`).
- `TestDiscover::test_skips_instruments_that_fail_to_open` — KEEP — distinct per-resource exception handling inside the scan loop.
- `TestDiscover::test_skips_non_dg1022z_instruments` — KEEP — distinct IDN filter branch.
- `TestDiscover::test_finds_dg1022z_and_extracts_serial` — KEEP — core positive path plus the serial-extraction regex.
- `TestDiscover::test_finds_multiple_instruments` — KEEP — exercises loop accumulation across several resources, which a single-match test would not catch (e.g. a `return` instead of `append/continue` bug).
- `TestDiscover::test_falls_back_to_resource_string_when_serial_unparseable` — KEEP — distinct regex-fallback branch.
- `TestLifecycle::test_init_raises_when_pyvisa_unavailable` — KEEP — distinct guard in `__init__`.
- `TestLifecycle::test_init_queries_idn` — KEEP — real behaviour: `__init__` caches `*IDN?`.
- `TestLifecycle::test_close_closes_session` — KEEP — distinct lifecycle behaviour.
- `TestLifecycle::test_close_swallows_errors` — KEEP — distinct branch (close() must not raise, since the instrument keeps state after close per module docstring).
- `TestLifecycle::test_write_and_query_passthrough` — KEEP — distinct passthrough contract.
- `TestSetWaveform::test_sine_sends_apply_sinusoid` — KEEP — distinct branch of the shape dispatch (`if/elif` chain); a defect in this branch would not break the other shapes.
- `TestSetWaveform::test_triangle_sends_ramp_with_50pct_symmetry` — KEEP — distinct branch, plus the RIGOL-has-no-TRIANGLE-keyword workaround (extra `RAMP:SYMMetry 50` command).
- `TestSetWaveform::test_square_sends_apply_square` — KEEP — distinct branch.
- `TestSetWaveform::test_pulse_sends_apply_pulse` — KEEP — distinct branch.
- `TestSetWaveform::test_dc_sends_apply_dc_with_offset_only` — KEEP — distinct branch with different argument semantics (offset_v is the held voltage).
- `TestSetWaveform::test_unknown_shape_raises_value_error` — KEEP — explicit regression test named in the module for a real historical bug (silently sending nothing for a bad shape).
- `TestSetWaveform::test_amplitude_above_max_is_clamped_with_warning` — KEEP — safety-critical clamp (`MAX_AMP_VPP`), asserts both the clamped value sent and the warning text.
- `TestSetWaveform::test_offset_above_max_is_clamped_with_warning` — KEEP — distinct field (offset vs. amplitude) of the same safety clamp.
- `TestSetWaveform::test_amplitude_below_negative_max_is_clamped` — KEEP — distinct boundary (negative side of the clamp `max(-MAX,...)`), not covered by the positive-side test.
- `TestSetWaveform::test_within_range_values_are_not_clamped` — KEEP — distinct branch: confirms the "no clamp" (`warn == ""`) path, which the clamp-triggering tests don't exercise.
- `TestRampPrimitives::test_set_amplitude_sends_voltage_not_apply` — KEEP — pins the safety-relevant distinction (`:VOLTage` vs `:APPLy:`) the module docstring calls out as "the single most likely way to get [the ramp] wrong."
- `TestRampPrimitives::test_set_amplitude_clamps_with_warning` — KEEP — distinct clamp branch for this primitive.
- `TestRampPrimitives::test_set_amplitude_clamps_negative` — KEEP — distinct negative-boundary branch.
- `TestRampPrimitives::test_set_offset_sends_voltage_offset_not_apply` — KEEP — same `:APPLy:`-avoidance safety property, for offset.
- `TestRampPrimitives::test_set_offset_clamps_with_warning` — KEEP — distinct clamp branch for offset.
- `TestRampPrimitives::test_write_fast_skips_error_poll` — KEEP — distinct performance/behaviour contract (no extra `query()` round trip), directly tied to the ramp engine's timing requirement.
- `TestOutputControl::test_output_on` — KEEP — distinct SCPI string.
- `TestOutputControl::test_output_off` — KEEP — distinct SCPI string.
- `TestOutputControl::test_set_output_load_default_infinity` — KEEP — distinct default-argument branch, safety-relevant (wrong load setting halves the delivered voltage per docstring).
- `TestOutputControl::test_set_output_load_explicit_value` — KEEP — distinct explicit-value branch.
- `TestGetState::test_parses_normal_response` — KEEP — real parsing of the documented `"SIN 1000.0,...,"` layout, all four numeric fields plus shape/output/load.
- `TestGetState::test_output_off_parsed_as_false` — KEEP — distinct boolean branch.
- `TestGetState::test_returns_error_dict_on_visa_failure` — KEEP — distinct exception-handling branch.
- `TestGetState::test_malformed_numeric_payload_does_not_raise` — KEEP — distinct fallback-to-0.0 branch, the exact historical bug class the docstring describes (a campaign's sidecars recorded 0.0 for a live setting).
- `TestPassthroughMethods::test_set_phase` — KEEP — distinct SCPI string; a typo in this literal would not be caught by any other test.
- `TestPassthroughMethods::test_set_duty` — KEEP — distinct SCPI string.
- `TestPassthroughMethods::test_set_ramp_symmetry` — KEEP — distinct SCPI string.
- `TestPassthroughMethods::test_sweep_on_off` — KEEP — distinct SCPI strings (two, on/off).
- `TestPassthroughMethods::test_set_sweep` — KEEP — distinct multi-command sequence.
- `TestPassthroughMethods::test_burst_on_off` — KEEP — distinct SCPI strings.
- `TestPassthroughMethods::test_set_burst` — KEEP — distinct multi-command sequence.
- `TestPassthroughMethods::test_align_phase` — KEEP — distinct SCPI string.
- `TestPassthroughMethods::test_set_reference_clock_internal` — KEEP — distinct branch (explicit "INTernal").
- `TestPassthroughMethods::test_set_reference_clock_external` — KEEP — distinct branch (explicit "EXTernal").
- `TestPassthroughMethods::test_set_reference_clock_defaults_internal` — KEEP — protects the safety-relevant default value in the signature itself (a caller passing nothing must land on internal, not external, or two units could both drive the shared clock line and damage each other per docstring); a distinct source location from the explicit-argument test even though the resulting SCPI call is the same.
- `TestPassthroughMethods::test_set_reference_clock_accepts_short_forms` — KEEP — distinct branch (`"ext"`/`"int"` short-form parsing).
- `TestPassthroughMethods::test_set_reference_clock_rejects_bad_source` — KEEP — distinct branch (`ValueError` on an unrecognized source).
- `TestPassthroughMethods::test_get_reference_clock_returns_int` — KEEP — distinct branch of `get_reference_clock`.
- `TestPassthroughMethods::test_get_reference_clock_returns_ext` — KEEP — distinct branch.
- `TestPassthroughMethods::test_get_reference_clock_raises_on_unexpected_response` — KEEP — distinct branch; the docstring is explicit this method must "never return None silently."
- `TestPassthroughMethods::test_verify_external_lock_returns_true_when_locked` — KEEP — distinct branch of a safety-relevant confirmation (PLL actually locked).
- `TestPassthroughMethods::test_verify_external_lock_returns_false_on_fallback` — KEEP — distinct branch (silent INT fallback correctly reported as not-locked).
- `TestPassthroughMethods::test_beep` — KEEP — distinct SCPI string.
- `TestPassthroughMethods::test_get_error` — KEEP — distinct passthrough contract.

**Total: 57 (57 keep, 0 delete)**

---

## tests/test_timebase.py

Note on scope: `TestSharedTimebase` drives `rbl.state.beamline.Beamline` directly with mocked `dg_a`/`dg_b` drivers. Unlike `TestPairCorrelation`/`TestWaveDecimation` (which test pure functions in `rbl.hardware.amp_monitor`/`amp_trace`), this class is genuinely testing **Beamline's own business logic** — the both-EXT interlock and the lock/unlock state machine — not hardware-layer math. It is still `keep`-eligible per the ticket's carve-out because it asserts on the method's public return contract (`(ok, msg)`) and on `command_failed`/`funcgens_changed`, the actual signals other screens consume; see the per-test notes below for the one exception.

- `TestPairCorrelation::test_mirror_images_correlate_at_minus_one` — KEEP — core formula case (perfect anti-phase → -1).
- `TestPairCorrelation::test_amplitude_does_not_change_the_answer` — KEEP — distinct property (scale invariance); catches an unnormalized-covariance implementation that `test_mirror_images...` alone would not.
- `TestPairCorrelation::test_in_phase_channels_correlate_at_plus_one` — KEEP — distinct branch/value (+1).
- `TestPairCorrelation::test_a_quarter_cycle_slip_lands_between` — KEEP — distinct partial-correlation case.
- `TestPairCorrelation::test_a_flat_channel_has_no_phase` — KEEP — distinct branch (`var <= 0` → NaN), a real safety property (no invented "0" for a dead plate).
- `TestPairCorrelation::test_too_few_samples_is_unknown` — KEEP — distinct guard branch (`n < 2`), two sub-cases of the same guard.
- `TestPairCorrelation::test_nan_samples_are_unknown` — KEEP — distinct guard branch (NaN detection).
- `TestWaveDecimation::test_two_channels_share_one_grid` — KEEP — real composed behaviour (decimated grids stay comparable), the actual property the pair-correlation panel depends on.
- `TestWaveDecimation::test_point_count_is_bounded` — KEEP — distinct branch (`WAVE_POINTS` cap).
- `TestWaveDecimation::test_a_short_window_is_passed_through` — KEEP — distinct branch (short-input passthrough).
- `TestWaveDecimation::test_no_waveform_is_empty_not_zeros` — KEEP — distinct guard covering two falsy inputs (`None` and `[]`) that could plausibly follow different code paths.
- `TestSharedTimebase::test_enabling_sets_only_gen_b_external` — KEEP — asserts Beamline's own state-machine behaviour (not hardware math), but on the safety-critical property that Gen A is never told EXT (both-EXT damages the instruments) and on the public return value; flagged as Beamline-behaviour rather than hardware-math per the note above.
- `TestSharedTimebase::test_both_ext_is_refused_before_anything_is_written` — KEEP — distinct branch, and specifically asserts the ordering guarantee (guard fires before any write) that the docstring calls out as safety-critical.
- `TestSharedTimebase::test_a_failed_lock_reports_failure` — KEEP — distinct branch (verify_external_lock returns False → command reports failure, doesn't lie about the lock state).
- `TestSharedTimebase::test_disabling_returns_both_to_internal` — KEEP — distinct branch (`enabled=False`).
- `TestSharedTimebase::test_one_generator_alone_cannot_share_anything` — KEEP — distinct guard (one generator missing).
- `TestSharedTimebase::test_locked_only_for_the_one_correct_configuration` — **DELETE** — asserts on Beamline's internal `timebase_locked` property. A repo-wide grep confirms this property has **no readers anywhere in `src/`** outside its own definition — the Overview tab (`overview_tab.py:859`) renders `funcgens.timebase` (the raw dict), not this property. This test exercises internal state with no downstream consumer, and the actually-consumed pipeline is already covered by `test_the_cached_clock_is_published_on_funcgen_state` below.
- `TestSharedTimebase::test_the_cached_clock_is_published_on_funcgen_state` — KEEP — asserts on `FuncGenState.timebase` delivered via the `funcgens_changed` signal, which is exactly what `overview_tab.py` reads to render the lock indicator; the genuine downstream-consumer test for this feature.
- `TestSharedTimebase::test_a_driver_error_is_reported_not_raised` — KEEP — real robustness contract (a bad VISA call must reach `command_failed`, not crash the event loop, per CLAUDE.md §4's threading contract).

**Total: 19 (18 keep, 1 delete)**

---

## tests/test_waveform_ring.py

Production module: `src/rbl/hardware/waveform_ring.py`.

- `TestDecimationPreservesPeaks::test_triangle_apex_not_flipped` — KEEP — explicit regression test for the "somersault" bug named in the module docstring; pins envelope preservation, x-monotonicity, and apex placement.
- `TestDecimationPreservesPeaks::test_descending_ramp_not_reversed` — KEEP — distinct scenario (monotonic descending ramp) covering the other historical bug class (backward x-steps on a falling edge) the docstring names separately from the apex case.
- `TestDecimationPreservesPeaks::test_short_series_returned_unchanged` — KEEP — distinct branch (`n <= max_points` passthrough).
- `TestWaveformRing::test_empty_ring_has_no_latest` — KEEP — real initial-state contract of the public API (`latest_t()`, `series()`).
- `TestWaveformRing::test_store_and_series_round_trip` — KEEP — exercises the public `store()`/`series()` contract with a real reconstructed-time-base assertion, not private state.
- `TestWaveformRing::test_old_chunks_are_dropped` — KEEP — distinct branch: the retention/cutoff logic that bounds memory during a long run.
- `TestWaveformRing::test_clear_empties_all_channels` — KEEP — distinct branch (`clear()` across multiple channels).

**Total: 7 (7 keep, 0 delete)**

---

## tests/test_waveform_period.py

Note on scope: `TestBeamlineCycleTraces` drives `rbl.state.beamline.Beamline` through `ingest_labjack_window` — a full production ingestion path — and asserts on the resulting `AmpState.channels[...].wave_kv` / `.wave_span_s` / `.wave_freq_hz` fields. A grep of `src/rbl/gui/overview_tab.py` (lines 873-878, 1049-1052) confirms these exact fields are what the Overview tab renders, so Beamline here is used as the lightweight harness the ticket describes ("gets real data into the pure-math function under test"), not as the subject under test itself — this class is `keep`-eligible as driving the production path and asserting on what an operator sees, per the ticket's explicit criterion, even though the assertions pass through Beamline rather than calling `waveform_period` functions directly.

- `TestPeriodEstimate::test_recovers_a_sine_period` — KEEP — parametrized over 3 periods (whole and non-integer); pins the sub-sample interpolation accuracy, not restated elsewhere.
- `TestPeriodEstimate::test_recovers_a_triangle_period` — KEEP — distinct waveform shape; the module docstring explains autocorrelation was chosen specifically because a triangle's harmonics can outrank its fundamental, so this is a load-bearing case, not a restatement of the sine case.
- `TestPeriodEstimate::test_recovers_a_square_period` — KEEP — distinct shape.
- `TestPeriodEstimate::test_survives_noise_on_the_monitor` — KEEP — distinct robustness property (additive noise).
- `TestPeriodEstimate::test_flat_channel_has_no_period` — KEEP — distinct guard branch (`min_rms`), two sub-cases (zero and constant-nonzero).
- `TestPeriodEstimate::test_noise_alone_has_no_period` — KEEP — distinct branch (`MIN_CORRELATION` guard on pure noise).
- `TestPeriodEstimate::test_under_two_cycles_is_refused` — KEEP — distinct guard (`MIN_CYCLES_FOR_ESTIMATE`).
- `TestPeriodEstimate::test_a_drive_far_below_the_noise_floor_is_refused` — KEEP — distinct guard (amplitude vs. `min_rms`), different failure mode from the flat-channel case.
- `TestCycleWindow::test_two_cycles_when_the_record_is_long` — KEEP — base case of `cycle_span_samples`.
- `TestCycleWindow::test_falls_back_to_one_cycle` — KEEP — pins the exact boundary (140 vs 150 samples) where the fallback-to-one-cycle behaviour flips; a real edge case named in both the module docstring and the test's own comment.
- `TestCycleWindow::test_no_whole_cycle_available` — KEEP — distinct branch (zero-cycle case, both `cycle_span_samples` and `cycle_slice`).
- `TestCycleWindow::test_no_period_means_no_window` — KEEP — distinct branch (NaN period input).
- `TestCycleWindow::test_window_starts_on_a_rising_edge_whatever_the_phase` — KEEP — parametrized over 4 phases; pins the trigger behaviour independent of input phase, which is the entire point of `cycle_slice`.
- `TestCycleWindow::test_a_pair_keeps_one_grid` — KEEP — distinct property: the same slice indices applied to both members of a push-pull pair.
- `TestAlignedWaveHistory::test_tail_spans_several_windows` — KEEP — real behaviour: tail concatenation across multiple pushed windows via the public API.
- `TestAlignedWaveHistory::test_channels_stay_index_aligned` — KEEP — distinct property (multi-channel alignment on one grid).
- `TestAlignedWaveHistory::test_a_missing_channel_breaks_the_run` — KEEP — distinct, explicitly safety-relevant behaviour (a paused channel truncates rather than splicing across a gap).
- `TestAlignedWaveHistory::test_old_windows_are_dropped` — KEEP — distinct branch (memory-bound eviction).
- `TestAlignedWaveHistory::test_empty_windows_do_not_pile_up` — KEEP (flagged) — drives the real `push()` API 5000 times (real production code path, catching a genuine unbounded-memory-growth defect class relevant to 12-hour runs per CLAUDE.md), but the assertion reads the private `hist._windows` deque directly, since there is no public accessor for window count. This would normally be a `rewrite` candidate (assert via a public accessor instead), which is out of scope for this batch; keeping it is the closer of the two available verdicts given it covers real, otherwise-unverifiable behaviour and does not fit the "writes private state then reads it back" delete criterion (it only *reads* private state after driving real production calls). See "Flagged for ticket 11" above.
- `TestAlignedWaveHistory::test_clear_drops_everything` — KEEP — distinct branch (`clear()`), public API throughout.
- `TestBeamlineCycleTraces::test_trace_holds_at_least_one_whole_cycle` — KEEP — parametrized over 4 frequencies (1000/137/20/3 Hz); the central behaviour the whole module exists for, asserted on the operator-visible `AmpState` fields.
- `TestBeamlineCycleTraces::test_a_slow_drive_reaches_back_past_one_stream_window` — KEEP — distinct scenario (cross-window history reach-back at 3 Hz).
- `TestBeamlineCycleTraces::test_a_fast_drive_zooms_in_instead_of_aliasing` — KEEP — distinct scenario, explicit anti-aliasing regression (1 kHz).
- `TestBeamlineCycleTraces::test_the_pair_shares_one_window` — KEEP — distinct property (shared grid/sign relationship across a push-pull pair).
- `TestBeamlineCycleTraces::test_the_trace_holds_still_between_frames` — KEEP — distinct, operator-visible quality property (no frame-to-frame jitter).
- `TestBeamlineCycleTraces::test_axes_are_windowed_independently` — KEEP — distinct property (X and Y axes windowed independently at different drive rates).
- `TestBeamlineCycleTraces::test_an_undriven_pair_falls_back_to_the_raw_window` — KEEP — distinct fallback branch (NaN frequency, raw window span).
- `TestBeamlineCycleTraces::test_a_channel_with_no_waveform_gets_no_window` — KEEP — distinct branch: scalar-only (no `"waveform"` key) payloads, a backward-compatibility case named in the test's own docstring.
- `TestBeamlineCycleTraces::test_a_lone_streamed_plate_still_gets_a_window` — KEEP — distinct branch (single-channel profile, no pair to compare).
- `TestBeamlineCycleTraces::test_history_is_dropped_when_the_stream_restarts` — KEEP (flagged) — real, safety-relevant behaviour (history must not stitch across a sample-rate seam), but it invokes `beamline._mark_labjack_disconnected()` directly rather than the public `disconnect_labjack()` that calls it in production. This is a private-method call that would ordinarily be a `rewrite` candidate (call the public method instead); out of scope for this batch, and the behaviour itself is real and otherwise uncovered, so `keep` is the closer of the two available verdicts. See "Flagged for ticket 11" above.

**Total: 30 (30 keep, 0 delete)**
