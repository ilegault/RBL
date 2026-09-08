# Audit — state, services and persistence test modules (ticket 08)

**Verdicts only. No test file, ADR, or production module was modified while producing this
audit.** Criteria are ADR 0001's vocabulary, as restated in ticket 08:

- **Keep** — drives the production path and asserts on something an operator could see or a
  downstream consumer reads, or tests hardware-layer mathematics directly.
- **Rewrite** — covers real behaviour but reaches it by calling a private method or reading a
  private attribute. Names the sanctioned path the rewrite would use.
- **Delete** — a churn test (fails on restructuring with no behaviour change, would not have
  caught a real defect), or restates a guarantee the framework already makes, or writes the
  private state it then reads back.

## Batch file list (20 files)

Exactly the complement ticket 07's audit recorded: every `tests/*.py` file that does not import
Qt and is not one of ticket 07's 28 hardware/pure-math files. Cross-checked directly against
`.scratch/test-suite-overhaul/audit-07-hardware-and-pure-math.md`'s "Batch file list" section —
same 20 names, so the two batches partition the 48 non-Qt test modules with no overlap and no
gap (28 + 20 = 48). Verified independently here: an AST-based per-file count (not the naive
`def test_` grep ticket 07 flagged a false positive from) gives 259 test functions total across
these 20 files, and none of the 20 files import `PySide6`/`QtCore`/`QtWidgets`/`QtGui`/`qtbot`/
`QApplication`.

1. `tests/test_beamline.py`
2. `tests/test_calibration_config.py`
3. `tests/test_calibration_config_load.py`
4. `tests/test_calibration_writer.py`
5. `tests/test_conditioning_history.py`
6. `tests/test_csv_log_writer.py`
7. `tests/test_dynamic_adjustment_history.py`
8. `tests/test_funcgen_safety.py`
9. `tests/test_hv_interlock_link.py`
10. `tests/test_labjack_link.py`
11. `tests/test_labjack_stream.py`
12. `tests/test_layering.py`
13. `tests/test_load_calibration_store.py`
14. `tests/test_persistence.py`
15. `tests/test_snapshot_json.py`
16. `tests/test_steerer_geometry.py`
17. `tests/test_stream_payload_stats.py`
18. `tests/test_tab_persistence.py`
19. `tests/test_trip_history.py`
20. `tests/test_vacuum_logger.py`

## Result

**259 test functions audited across 20 files: 242 KEEP, 4 REWRITE, 13 DELETE.**

`tests/test_layering.py` is verdicted `keep` as the explicit template this project wants more
of (spec: "one rule, checked across every module") — one test, parametrized over the whole
source tree via `scripts/check_layers.py`, with an explicit tracked-exception list for the
three known violations `docs/IMPROVE_CODEBASE_ARCHITECTURE.md` §1.4 already documents.

| # | File | Total | Keep | Rewrite | Delete |
|---|---|---:|---:|---:|---:|
| 1 | tests/test_beamline.py | 53 | 48 | 4 | 1 |
| 2 | tests/test_calibration_config.py | 12 | 12 | 0 | 0 |
| 3 | tests/test_calibration_config_load.py | 6 | 6 | 0 | 0 |
| 4 | tests/test_calibration_writer.py | 8 | 8 | 0 | 0 |
| 5 | tests/test_conditioning_history.py | 5 | 5 | 0 | 0 |
| 6 | tests/test_csv_log_writer.py | 38 | 38 | 0 | 0 |
| 7 | tests/test_dynamic_adjustment_history.py | 9 | 9 | 0 | 0 |
| 8 | tests/test_funcgen_safety.py | 8 | 8 | 0 | 0 |
| 9 | tests/test_hv_interlock_link.py | 12 | 12 | 0 | 0 |
| 10 | tests/test_labjack_link.py | 26 | 26 | 0 | 0 |
| 11 | tests/test_labjack_stream.py | 14 | 12 | 0 | 2 |
| 12 | tests/test_layering.py | 1 | 1 | 0 | 0 |
| 13 | tests/test_load_calibration_store.py | 9 | 9 | 0 | 0 |
| 14 | tests/test_persistence.py | 4 | 4 | 0 | 0 |
| 15 | tests/test_snapshot_json.py | 13 | 13 | 0 | 0 |
| 16 | tests/test_steerer_geometry.py | 7 | 7 | 0 | 0 |
| 17 | tests/test_stream_payload_stats.py | 6 | 6 | 0 | 0 |
| 18 | tests/test_tab_persistence.py | 12 | 2 | 0 | 10 |
| 19 | tests/test_trip_history.py | 5 | 5 | 0 | 0 |
| 20 | tests/test_vacuum_logger.py | 11 | 11 | 0 | 0 |
| | **Total** | **259** | **242** | **4** | **13** |

The 13 deletes split two ways: 10 in `test_tab_persistence.py` test threading logic the test
itself reimplemented rather than the real production classes (one of the two production classes
they claim to cover does not exist at all), and 3 elsewhere write private state and read it
straight back without exercising the behaviour that state actually drives. The 4 rewrites are
all in `test_beamline.py`'s HV-interlock tests, which reach the interlock's private pressure
cache directly when a public ingestion path (`on_vacuum_changed_for_interlock`) exists and is
already exercised, unprimed, by two sibling tests in the same class.

### Flagged for the reviewer, not acted on here

A few `keep` verdicts below are judgment calls on the private-access line, kept rather than
reached-for-rewrite because a genuine sanctioned public alternative doesn't exist without adding
new test machinery (mocking LJM, spinning a real `QThread`). Named here so ticket 11 can weigh
in:

- **`tests/test_stream_payload_stats.py` (all 6 tests) and `tests/test_labjack_stream.py`'s
  `TestSingleChannelPayload` (3 tests)** — construct `LabJackStreamWorker.__new__(...)`, set the
  private `_profile_name` attribute directly, and call the private `_build_payload(...)` method.
  Kept as `keep`, not `rewrite`, because `_build_payload` is pure array math with no I/O and its
  own module docstring documents this exact technique as the sanctioned way to reach it:
  `labjack_stream_worker.py`'s own `if __name__ == "__main__":` self-test block (lines 433-506)
  does the identical `__new__` + `_profile_name` + `_build_payload(...)` sequence, and the
  method's docstring says the `sample_period` parameter "is optional so pure payload-math
  callers (self-test, unit tests) need not supply it" — i.e. the module's own author designed
  this as the unit-test seam, not as an accidental leak of internals. This differs from the
  Qt-widget private-access pattern ADR 0001 targets (poking a live widget's internal state); the
  only alternative would be running `LabJackStreamWorker.run()` against a mocked LJM stream,
  which is a materially heavier test for the same pure-math coverage. Matches the spec's Seams
  section: "Hardware mathematics: direct function calls. No seam required."
- **`tests/test_beamline.py::TestDeviceOwnership::test_disconnect_labjack_stops_stream_before_closing_handle`**
  — injects a `MagicMock()` directly into the private `beamline._lj_worker` attribute (no public
  setter exists; a real worker requires a real `QThread` and LJM) and, after calling
  `disconnect_labjack()`, asserts both mock call order (`worker.stop()` before
  `beamline.lj.disconnect()` — the documented teardown order from CLAUDE.md §4) and that
  `beamline._lj_worker is None` afterward. The call-order assertions are legitimate behaviour
  checks on injected collaborators; the final `is None` check is the one private-attribute read,
  kept because there is no cheaper public observation of "the stale worker reference was
  dropped."
- **`tests/test_beamline.py::TestDeviceOwnership::test_labjack_connected_signal_carries_serial`**
  — overrides the private `beamline._start_stream_worker` to a no-op lambda so `connect_labjack`
  can be exercised without a real background stream thread. The assertion itself
  (`labjack_connected` signal carries the serial number) is on public, downstream-visible
  behaviour; only the setup reaches for a private method, and only to suppress a side effect
  that would otherwise need a mocked LJM handle.
- **The shared `beamline` fixture in `test_beamline.py` (lines 21-39), used by ~40 of the file's
  53 tests** — writes `_hv_pressure_torr`, `_hv_pressure_at`, and `_hv_interlock_gauge_keys`
  directly so the HV interlock reads as healthy and doesn't block unrelated tests (motor/labjack/
  funcgen ingestion, the command surface). A public path exists —
  `beamline.on_vacuum_changed_for_interlock(vacuum_state)` (production's own consumer of
  `vacuum_changed`) together with `beamline.set_interlock_gauges(keys)` — and would remove this
  private write in one place for every consuming test at once. Not verdicted per-test because
  the ~40 consuming tests exercise real, unrelated production behaviour through fully public
  entry points; the private reach lives only in the shared fixture. Left as a systemic note
  rather than forcing 40 individual `rewrite` verdicts for one shared piece of setup — a single
  fixture rewrite (in whichever ticket executes rewrites) fixes all of them together.
- **`tests/test_snapshot_json.py::test_no_numpy_repr_strings_in_output`** — one of its three
  assertions, `assert "e-0" not in raw.replace('"', "")[:0] or True`, is a tautology: slicing to
  `[:0]` always yields `""`, so the left side is always `True` before the `or True` even applies.
  It can never fail. Not a reason to delete the test — the other two assertions
  (`"..." not in raw`, `"_unserializable" not in raw`) are real and are what the test's docstring
  describes — but worth a maintainer's cleanup pass independent of this audit.

---

## tests/test_layering.py

Production module read: `src/rbl/hardware/labjack_stream_worker.py` N/A here — this test wraps
`scripts/check_layers.py` directly (also read).

- `test_no_new_layer_violations` — **KEEP** — the explicit template named in ticket 08: one rule
  (imports flow `config → hardware → state → services → gui` only), checked across every module
  in `src/rbl/`, with an explicit, tracked exception list for the three violations
  `docs/IMPROVE_CODEBASE_ARCHITECTURE.md` §1.4 already documents as known debt. This is exactly
  the shape the spec asks the rest of the suite to grow into (`tests/test_layering.py` is named
  in the spec's own "Prior art" section).

**Total: 1 (1 keep, 0 rewrite, 0 delete)**

---

## tests/test_persistence.py

Production module read: `src/rbl/config/persistence.py`.

- `test_load_config_missing_file_returns_empty_dict` — KEEP — public `load_config()`, a real
  first-run/no-file behaviour a caller depends on.
- `test_save_then_load_round_trips` — KEEP — round-trips the actual on-disk JSON format through
  the public API.
- `test_save_creates_parent_directory` — KEEP — tests the documented "survives a fresh
  `~/.config/rbl/` that doesn't exist yet" behaviour.
- `test_load_config_corrupt_file_returns_empty_dict` — KEEP — tests the broad `except Exception`
  fallback with a real corrupt-file scenario, not a framework guarantee (this behaviour is this
  module's own design choice, not something `json`/`open` gives for free).

**Total: 4 (4 keep, 0 rewrite, 0 delete)**

---

## tests/test_calibration_config.py

Production module read: `src/rbl/config/calibration_config.py`.

- `TestSweepPoints::test_up_is_ascending_and_bracketed` — KEEP — pins the "up" pass's length,
  bracketing zeros, direction, and max-step invariants directly against the sweep-generation
  formula.
- `TestSweepPoints::test_down_is_reverse_of_up_modulo_zeros` — KEEP — tests a real relationship
  between two independently-generated sequences, not restated elsewhere.
- `TestSweepPoints::test_random_seed_is_deterministic` — KEEP — tests the documented
  reproducible-seed guarantee the metadata sidecar depends on.
- `TestSweepPoints::test_random_different_seed_differs` — KEEP — the complementary case; without
  it a constant-output bug in `sweep_points("random", ...)` would pass the determinism test.
- `TestSweepPoints::test_every_pass_brackets_zero` (parametrize, 3 pass types) — KEEP — the
  zero-drift-tracker property, checked per pass type since each pass type builds its sequence
  differently (`_positive_half()`/`_base_ladder()`, different concatenation order).
- `TestSweepPoints::test_no_point_exceeds_cal_max_kv` (parametrize, 3 pass types) — KEEP — the
  hardware safety ceiling, checked per pass type for the same reason.
- `TestSweepPoints::test_unknown_pass_type_raises` — KEEP — tests the `ValueError` guard branch.
- `TestSweepPoints::test_up_down_visit_twice_random_visits_once` — KEEP — this is the test that
  correctly distinguishes `up`/`down` (each rung visited twice) from `random` (each rung visited
  once) — exactly the traversal-shape distinction ADR 0001 calls out as one of the eleven
  muted-test defects it fixed (decision: "the expectations must be corrected per pass type").
- `TestConstants::test_cal_max_kv_within_gen_ceiling` — KEEP — the same safety assertion the
  module's own `assert` at import time makes, but as an independently-runnable test rather than
  an import-time side effect; a real safety invariant, not a framework guarantee.
- `TestConstants::test_uncertainty_budget_is_positive` — KEEP — a real invariant on a constant
  whose sign flip would silently invert every uncertainty-band comparison downstream.
- `TestConstants::test_cal_profile_exists_and_carries_all_amp_ains` — KEEP — cross-checks two
  independent config modules agree (`CAL_PROFILE` is a real key in `STREAM_PROFILES` and covers
  every amp AIN) — a real integration invariant, not restated elsewhere.
- `TestConstants::test_load_condition_enum_has_exactly_two_members` — KEEP — guards the
  `LoadCondition` enum's exact membership, which the drift-duration guard and every CSV metadata
  sidecar depend on.

**Total: 12 (12 keep, 0 rewrite, 0 delete)**

---

## tests/test_calibration_config_load.py

Production module read: `src/rbl/config/calibration_config.py` (the `amp_label` load-resolution
path) and `src/rbl/config/load_calibration_store.py`.

- `TestLoadPfResolution::test_explicit_load_pf_wins_over_everything` — KEEP — tests the
  documented fallback-chain priority (explicit `load_pf` beats a stored measurement).
- `TestLoadPfResolution::test_amp_label_uses_stored_measurement_when_present` — KEEP — tests the
  next priority tier.
- `TestLoadPfResolution::test_unmeasured_amp_label_falls_back_to_global_constant` — KEEP — tests
  the bottom of the fallback chain, and cross-checks it agrees with the no-label call.
- `TestLoadPfResolution::test_no_amp_label_and_no_load_pf_uses_global_constant` — KEEP — the
  simplest case in the chain, distinct from the previous (no `amp_label` at all vs. an
  unmeasured one).
- `TestLoadPfResolution::test_ac_max_peak_kv_uses_measured_capacitance_for_labelled_channel` —
  KEEP — tests that the resolution path actually reaches `ac_max_peak_kv`, not just
  `ac_peak_current_ma`.
- `TestLoadPfResolution::test_measurement_never_alters_calibration_config_constant` — KEEP —
  the module docstring is explicit that `CAL_LOAD_CAP_PF` must never be corrected by a
  measurement; this is the test that would catch a regression into "apply" behaviour the module
  docstring explicitly disclaims.

**Total: 6 (6 keep, 0 rewrite, 0 delete)**

---

## tests/test_conditioning_history.py

Production module read: `src/rbl/services/conditioning_history.py`.

- `TestAppendAndLoad::test_missing_file_reads_back_empty` — KEEP — first-run behaviour a caller
  depends on.
- `TestAppendAndLoad::test_round_trips_and_preserves_order` — KEEP — the on-disk JSONL format
  and ordering guarantee, both real.
- `TestAppendAndLoad::test_corrupt_line_is_skipped_not_fatal` — KEEP — tests the module's own
  resilience design (one bad line doesn't lose the rest of a session's history).
- `TestSessionsFor::test_filters_to_one_channel_preserving_order` — KEEP — the per-channel query
  path a tab would use.
- `TestSessionsFor::test_no_sessions_for_unmeasured_channel` — KEEP — the empty-result branch.

**Total: 5 (5 keep, 0 rewrite, 0 delete)**

---

## tests/test_trip_history.py

Production module read: `src/rbl/services/trip_history.py`.

- `TestAppendAndLoad::test_missing_file_reads_back_empty` — KEEP.
- `TestAppendAndLoad::test_round_trips_and_preserves_order` — KEEP.
- `TestAppendAndLoad::test_creates_parent_directories` — KEEP — first-run-on-a-fresh-machine
  behaviour.
- `TestAppendAndLoad::test_corrupt_line_is_skipped_not_fatal` — KEEP.
- `TestAppendAndLoad::test_append_never_raises_on_unwritable_path` — KEEP — tests the "never
  crash the app over a logging nicety" design, a real and safety-relevant guarantee for a trip
  logger (a trip must not itself take down the run).

**Total: 5 (5 keep, 0 rewrite, 0 delete)**

---

## tests/test_dynamic_adjustment_history.py

Production module read: `src/rbl/services/dynamic_adjustment_history.py`.

- `TestAppendAndLoad::test_missing_file_reads_back_empty` — KEEP.
- `TestAppendAndLoad::test_round_trips_and_preserves_order` — KEEP.
- `TestAppendAndLoad::test_corrupt_line_is_skipped_not_fatal` — KEEP.
- `TestTrialsFor::test_filters_to_one_channel_preserving_order` — KEEP.
- `TestTrialsFor::test_no_trials_for_unmeasured_channel` — KEEP.
- `TestWinnerFor::test_picks_lowest_figure_of_merit` — KEEP — the actual selection logic an
  operator relies on to pick a pot position.
- `TestWinnerFor::test_no_trials_returns_none` — KEEP — the empty-input branch.
- `TestWinnerFor::test_nan_scores_are_excluded` — KEEP — a real, non-obvious filtering rule (a
  NaN figure-of-merit must not silently win by comparing false against every real number).
- `TestWinnerFor::test_all_nan_scores_returns_none` — KEEP — the degenerate case of the same
  rule (nothing left after exclusion), distinct code path from the previous test (empty list vs.
  all-filtered list).

**Total: 9 (9 keep, 0 rewrite, 0 delete)**

---

## tests/test_calibration_writer.py

Production module read: `src/rbl/services/calibration_writer.py`.

- `TestCsvHeader::test_header_matches_documented_columns` — KEEP — the CSV header is the
  contract every downstream analysis script depends on; pins it to the module's own
  `CSV_COLUMNS` list.
- `TestCrashSurvival::test_rows_survive_simulated_crash_mid_run` — KEEP — tests the module
  docstring's central guarantee (flush-after-every-row) via a real second file handle, exactly
  as a post-mortem inspection would read the file.
- `TestSidecar::test_sidecar_round_trips_and_has_load_condition` — KEEP — tests the sidecar JSON
  a year-later reader depends on, including the one field (`load_condition`) the module docstring
  says nothing else records.
- `TestSidecar::test_close_is_idempotent` — KEEP — a real safety property for a writer that may
  be closed from more than one code path (normal completion and an abort handler).
- `TestSidecar::test_write_row_after_close_raises` — KEEP — tests the guard against silently
  losing rows written after close.
- `TestConfigSnapshotAndGit::test_config_snapshot_has_known_constants` — KEEP — tests that the
  sidecar actually carries the constants a CSV needs to be re-interpreted later, including the
  `Path`→`str` conversion needed for JSON-serializability.
- `TestConfigSnapshotAndGit::test_missing_git_binary_does_not_raise` — KEEP — tests the "a
  provenance nicety must never crash a run" guarantee with a real missing-binary simulation.
- `TestConfigSnapshotAndGit::test_git_commit_hash_returns_string_normally` — KEEP — the
  complementary normal-path case; without it, a bug that always returned `""` (masking the
  except-path test) would go unnoticed.

**Total: 8 (8 keep, 0 rewrite, 0 delete)**

---

## tests/test_csv_log_writer.py

Production module read: `src/rbl/services/csv_log_writer.py`. The schema-roll behaviour exists
because silently dropping a late-connecting instrument's columns was a real data-loss bug on
8-hour irradiation runs, per the module docstring — this is a correctness guarantee, not
incidental plumbing.

- `TestFlatten::test_flat_dict_unchanged` — KEEP — the identity case of the pure `flatten()`
  helper's real contract.
- `TestFlatten::test_nested_dict_dot_separated` — KEEP — the core dot-path flattening rule a CSV
  column name depends on.
- `TestFlatten::test_deeply_nested` — KEEP — the recursive case, distinct from one level of
  nesting (a shallow-recursion-only bug would pass the previous test and fail this one).
- `TestFlatten::test_short_list_indexed` — KEEP — the list-indexing rule.
- `TestFlatten::test_short_list_boundary_8` — KEEP — pins the exact documented cutoff (8
  elements still flattened) — an off-by-one here silently drops or keeps the wrong data.
- `TestFlatten::test_long_list_skipped` — KEEP — the complementary case one element past the
  cutoff (waveform-sample lists must be silently skipped, not flattened into thousands of
  columns).
- `TestFlatten::test_nan_float_becomes_string` — KEEP — the same NaN-corruption class of bug
  `test_snapshot_json.py` guards for JSON, guarded here for CSV.
- `TestFlatten::test_positive_inf_becomes_string` — KEEP — the `+inf` case.
- `TestFlatten::test_negative_inf_becomes_string` — KEEP — the `-inf` case, a distinct branch
  from `+inf` in a naive sign-unaware implementation.
- `TestFlatten::test_normal_float_passed_through` — KEEP — the non-special-value baseline; without
  it a bug that stringified every float (not just NaN/Inf) would pass the three tests above.
- `TestFlatten::test_string_value_passed_through` — KEEP — a distinct value type from numeric.
- `TestFlatten::test_int_passed_through` — KEEP — a distinct value type from float (ints have no
  NaN/Inf state to special-case, and a bug that only handled `float` would miss this).
- `TestFlatten::test_none_passed_through` — KEEP — `None` must not become the string `"None"` or
  be dropped; a real and easy-to-get-wrong case for a generic flattener.
- `TestFlatten::test_prefix_prepended` — KEEP — the recursive `prefix` parameter, which is how
  `test_nested_dict_dot_separated` and `test_deeply_nested` actually get their dotted names —
  this is the one test that exercises the parameter directly rather than only its effect.
- `TestFlatten::test_tuple_treated_like_list` — KEEP — a distinct sequence type from `list`; a
  `isinstance(x, list)`-only implementation would silently mis-handle this.
- `TestFlatten::test_nested_dict_in_list` — KEEP — the composition of the two branches above
  (dict-in-list), not implied by either alone.
- `TestFlatten::test_out_dict_accumulates` — KEEP — the `out=` accumulator parameter, used
  internally for the recursive case; tests that the same dict object is returned and mutated.
- `TestFlatten::test_empty_dict` — KEEP — the empty-input degenerate case.
- `TestFlatten::test_empty_list` — KEEP — the empty-list degenerate case, distinct from an empty
  dict (a list-length check bug could pass one and fail the other).
- `TestCsvLogWriterParts::test_first_part_named_data_csv` — KEEP — the default file-naming
  contract a log-parsing script depends on.
- `TestCsvLogWriterParts::test_custom_base_name` — KEEP — the caller-supplied base-name path,
  distinct from the default.
- `TestCsvLogWriterParts::test_second_part_gets_002_suffix` — KEEP — the roll-numbering scheme,
  the actual mechanism the "don't lose the late-connecting instrument's columns" guarantee
  depends on.
- `TestCsvLogWriterParts::test_third_part_gets_003_suffix` — KEEP — that the numbering continues
  correctly past the second roll, not just increments once and stops.
- `TestCsvLogWriterParts::test_parts_property_lists_all_basenames` — KEEP — the public `parts`
  property a session recorder would read to know what files exist.
- `TestCsvLogWriterParts::test_no_roll_when_schema_unchanged` — KEEP — the no-false-positive case
  (a writer that always rolled would still pass the naming tests above but would defeat the
  point of a single growing log file).
- `TestSchemaRoll::test_roll_on_new_column` — KEEP — the actual data-loss bug this module exists
  to prevent (per its own module docstring), verified end to end: old file's rows don't gain the
  new column, new file's rows have it.
- `TestSchemaRoll::test_data_from_row_that_triggered_roll_is_in_new_file` — KEEP — the specific
  and easy-to-get-wrong edge: the very row that causes the roll decision must not itself be
  dropped or misfiled.
- `TestSchemaRoll::test_new_part_has_correct_header` — KEEP — the new file's header must reflect
  its own (wider) schema, not the old file's.
- `TestSchemaRoll::test_no_roll_on_subset_keys` — KEEP — the complementary case to the roll tests:
  a row with fewer keys than the current schema must NOT trigger a roll (only a genuinely new key
  should), and missing values gap-fill rather than corrupt column alignment.
- `TestSchemaRoll::test_missing_keys_gap_filled` — KEEP — the gap-fill mechanics specifically,
  for more than one missing key at once.
- `TestSchemaRoll::test_multiple_rolls` — KEEP — that the roll mechanism composes correctly
  across three distinct schemas in sequence, not just a single roll in isolation.
- `TestCsvLogWriterIO::test_row_count_tracks_all_parts` — KEEP — the `row_count` property must
  count across a roll boundary, not reset per file part.
- `TestCsvLogWriterIO::test_file_readable_without_close` — KEEP — the flush-after-every-row
  guarantee (same class of guarantee as `calibration_writer`'s crash-survival test), read through
  a second, independent file handle.
- `TestCsvLogWriterIO::test_close_is_idempotent` — KEEP — a real safety property for a writer
  that may be closed from more than one shutdown path.
- `TestCsvLogWriterIO::test_header_written_to_first_row` — KEEP — the header-on-open contract.
- `TestCsvLogWriterIO::test_all_rows_readable_after_close` — KEEP — that ten sequential rows
  survive intact and in order after a normal close, the baseline correctness case underlying
  every other test in this class.
- `TestCsvLogWriterIO::test_first_write_determines_schema` — KEEP — that the schema comes from
  the first row written, not from some other source (e.g. a declared/expected schema) — the
  actual mechanism the whole schema-roll design is built on.
- `TestCsvLogWriterIO::test_ieee_special_values_written_as_strings` — KEEP — the integration of
  `flatten()`'s NaN/Inf string-conversion with `CsvLogWriter.write()`, end to end through a real
  written and re-read file — distinct from the unit-level `TestFlatten` NaN/Inf tests (this one
  proves the two pieces are actually wired together correctly).

**Total: 38 (38 keep, 0 rewrite, 0 delete)**

---

## tests/test_stream_payload_stats.py

Production module read: `src/rbl/hardware/labjack_stream_worker.py`.

- `TestMeanStd::test_mean_of_simple_array` — KEEP — see "Flagged for the reviewer" above for the
  `_build_payload`/`__new__` technique's rationale (module-sanctioned self-test seam for pure
  array math).
- `TestMeanStd::test_std_of_constant_array_is_zero` — KEEP — same rationale; degenerate-input
  case.
- `TestMeanStd::test_bipolar_symmetric_mean_zero_rms_nonzero` — KEEP — same rationale; the
  specific real bug class this feature was added for (RMS is the wrong DC estimator for a
  bipolar sweep, per the module docstring).
- `TestMeanStd::test_existing_keys_unchanged` — KEEP — regression guard that adding `mean`/`std`
  didn't disturb `peak`/`pk_pk`/`rms`, a real cross-field invariant.
- `TestMeanStd::test_std_matches_numpy_reference` — KEEP — pins the implementation against numpy
  on a realistic noisy signal, not a toy array.
- `TestMeanStd::test_stats_are_raw_volts_not_converted` — KEEP — guards against a units bug (this
  module must never scale to kV/mA; that conversion happens exactly once, downstream, per
  CLAUDE.md's "One conversion" invariant).

**Total: 6 (6 keep, 0 rewrite, 0 delete)**

---

## tests/test_steerer_geometry.py

Production module read: `src/rbl/config/steerer_geometry.py`.

- `test_it_is_the_steerer_actually_on_the_beamline` — KEEP — pins the installed unit's model/
  serial; a real fact about a physical, single-instance device.
- `test_plate_geometry_matches_the_lab_deflection_sheet` — KEEP — pins five dimensions to the
  lab's own `Hirst RHBL Deflection Information.xlsx`, cited by cell reference in the test.
- `test_drift_to_sample_matches_the_sheets_beamline_table` — KEEP — pins a summed beamline-length
  figure to the sheet's own component table, cited by range.
- `test_differential_rating_is_twice_the_per_plate_rating` — KEEP — tests the push-pull relation
  directly.
- `test_dimensions_are_positive` — KEEP — a real sanity invariant on physical dimensions that a
  unit/sign error could silently violate; not guaranteed by the language or a framework.
- `test_describe_names_the_part_and_the_gap` — KEEP — tests the operator-facing `describe()`
  string actually contains the identifying facts.
- `test_there_is_no_picker_left_to_get_wrong` — KEEP — a real regression guard: this module
  documents (per its own docstring) that it replaced a multi-geometry picker with one hardcoded
  unit; this test fails if that picker is ever reintroduced.

**Total: 7 (7 keep, 0 rewrite, 0 delete)**

---

## tests/test_funcgen_safety.py

Production module read: `src/rbl/hardware/funcgen_safety.py`.

- `TestChannelPeakVolts::test_ac_shape_combines_offset_and_half_amplitude` — KEEP — the core peak-
  volts formula, the ±5 V interlock's whole basis.
- `TestChannelPeakVolts::test_dc_shape_ignores_amplitude` — KEEP — a distinct branch (DC ignores
  `amp_vpp` entirely).
- `TestChannelPeakVolts::test_negative_offset_uses_magnitude` — KEEP — the sign-handling branch;
  a bug here would silently double the effective safety margin on negative offsets.
- `TestChannelPeakVolts::test_zero_offset_is_half_amplitude` — KEEP — the zero-offset degenerate
  case.
- `TestThresholds::test_warn_below_max` — KEEP — a real ordering invariant between two constants
  the GUI colours by (per CLAUDE.md's theme-role convention).
- `TestThresholds::test_max_matches_generator_ceiling` — KEEP — cross-checks two independently
  defined constants (interlock ceiling and generator hardware ceiling) agree.
- `TestChannelRole::test_x_axis_channels_share_generator_a` — KEEP — the channel-to-axis mapping
  a mis-wired steerer would violate.
- `TestChannelRole::test_y_axis_channels_share_generator_b` — KEEP — the complementary mapping.

**Total: 8 (8 keep, 0 rewrite, 0 delete)**

---

## tests/test_hv_interlock_link.py

Production module read: `src/rbl/state/hv_interlock_link.py` (`chamber_pressure_torr`).

- `TestChamberPressureTorr::test_no_gauges_connected_is_nan` — KEEP — the default/no-data branch.
- `TestChamberPressureTorr::test_single_vgc_reading` — KEEP.
- `TestChamberPressureTorr::test_single_xgs_reading` — KEEP — distinct gauge type from the
  previous test (this function merges two independent gauge families).
- `TestChamberPressureTorr::test_takes_the_worst_reading_across_both_gauges` — KEEP — the
  safety-critical "worst case wins" selection rule across both gauge families at once.
- `TestChamberPressureTorr::test_disconnected_gauge_is_ignored_even_with_stale_readings_present`
  — KEEP — a real and dangerous-if-wrong branch: a stale/garbage reading from a disconnected
  gauge must not be allowed to falsely lower (or raise) the reported pressure.
- `TestChamberPressureTorr::test_non_numeric_channels_are_skipped_not_treated_as_zero` — KEEP —
  a real interpretation choice (`None` pressure ≠ 0 Torr) that would be dangerous if inverted.
- `TestChamberPressureTorr::test_all_channels_non_numeric_is_nan` — KEEP — the degenerate case of
  the previous rule.
- `TestChamberPressureGaugeSelection::test_selected_key_filters_to_single_gauge` — KEEP — the
  operator gauge-selection feature (Overview tab), a real filtering behaviour.
- `TestChamberPressureGaugeSelection::test_selected_key_filters_cross_instrument` — KEEP —
  distinct case: filtering across the two gauge families, not just within one.
- `TestChamberPressureGaugeSelection::test_empty_selection_returns_nan` — KEEP — "no gauge
  selected" must block HV per the module's own safety design, not silently pick one.
- `TestChamberPressureGaugeSelection::test_none_selection_uses_all` — KEEP — the legacy/default
  mode, distinct from an explicit empty selection.
- `TestChamberPressureGaugeSelection::test_selected_key_no_match_returns_nan` — KEEP — a stale
  selection (e.g. a gauge that was unplugged) must not silently fall back to "use everything."

**Total: 12 (12 keep, 0 rewrite, 0 delete)**

---

## tests/test_load_calibration_store.py

Production module read: `src/rbl/config/load_calibration_store.py`.

- `TestSaveAndLoad::test_missing_store_is_empty` — KEEP — first-run behaviour across three public
  entry points at once.
- `TestSaveAndLoad::test_round_trips_one_channel_one_condition` — KEEP — the core round-trip,
  and that an unmeasured condition for a measured channel stays `None`.
- `TestSaveAndLoad::test_other_channels_are_unaffected` — KEEP — a real isolation guarantee
  (saving one channel must not leak into another).
- `TestSaveAndLoad::test_record_carries_method_and_timestamp` — KEEP — tests the provenance
  fields a later reviewer depends on to judge a measurement's trustworthiness.
- `TestBothConditionsCoexist::test_saving_one_condition_does_not_erase_the_other` — KEEP — "the
  whole point of the decomposition comparison" per the class docstring; a real and specifically
  named feature requirement (Section 2.4).
- `TestBothConditionsCoexist::test_capacitance_pf_for_without_condition_returns_most_recent` —
  KEEP — a distinct, non-obvious resolution rule (recency, not condition-order) from the
  previous test.
- `TestBothConditionsCoexist::test_comparison_for_returns_both_and_the_difference` — KEEP — the
  decomposition-comparison feature's actual output an operator reads.
- `TestBothConditionsCoexist::test_comparison_for_missing_side_has_no_diff` — KEEP — the
  one-sided-data branch (no fabricated difference from a missing measurement).
- `TestBothConditionsCoexist::test_comparison_for_unmeasured_channel_is_all_none` — KEEP — the
  fully-empty branch, distinct from the one-sided case above.

**Total: 9 (9 keep, 0 rewrite, 0 delete)**

---

## tests/test_vacuum_logger.py

Production module read: `src/rbl/services/vacuum_logger.py`. The test file's `_Fake*` dataclasses
are structurally-compatible stand-ins for `rbl.state.snapshots` types (same field names/shapes),
not access into `VacuumLogger`'s own internals — `VacuumLogger` consumes them through its normal
public `write_row(state)` parameter, so this is ordinary test-double construction, not a private-
access shortcut.

- `TestVacuumLogger::test_row_count` — KEEP — the basic write-count contract.
- `TestVacuumLogger::test_none_pressure_is_empty_field` — KEEP — regression-shaped: pins that a
  `None` pressure becomes an empty CSV field across three distinct causes (periodic OFF gauge,
  a mid-run OVER state, and the sentinel-consumption case below) in one assertion pass.
- `TestVacuumLogger::test_state_columns_populated` — KEEP — every row must be diagnosable from
  its state columns alone, a real operator-facing guarantee.
- `TestVacuumLogger::test_sentinel_never_appears_as_number` — KEEP — this is the specific
  regression the module docstring names: the driver's `"1.10E+03"` off-or-overrange sentinel
  must never be written as if it were a real pressure reading.
- `TestVacuumLogger::test_json_sidecar_keys` — KEEP — the sidecar contract a later reviewer reads.
- `TestVacuumLogger::test_close_is_idempotent` — KEEP.
- `TestVacuumLogger::test_write_row_after_close_raises` — KEEP.
- `TestVacuumLogger::test_label_mismatch_returns_false` — KEEP — the schema-mismatch guard (a
  gauge set change mid-run must not silently corrupt the CSV's columns).
- `TestVacuumLogger::test_fixed_columns_present` — KEEP — the fixed-column contract downstream
  tooling depends on.
- `TestVacuumLogger::test_comment_header_written` — KEEP — the human-readable provenance header.
- `TestVacuumLogger::test_custom_metadata` — KEEP — tests that caller-supplied metadata actually
  reaches the sidecar.

**Total: 11 (11 keep, 0 rewrite, 0 delete)**

---

## tests/test_labjack_link.py

Production module read: `src/rbl/state/labjack_link.py`, via `Beamline.ingest_labjack_window`
(all tests go through `tests/payloads.py`'s `LabJackFeed`/`window_payload`, the sanctioned
production path per that module's own docstring).

- `TestLogAmpConversion::test_midpoint_voltage_gives_1ua` — KEEP — pins the log-amp calibration
  formula's midpoint.
- `TestLogAmpConversion::test_lower_boundary_gives_1na` — KEEP — the lower calibration boundary.
- `TestLogAmpConversion::test_upper_boundary_gives_1ma` — KEEP — the upper calibration boundary.
- `TestLogAmpConversion::test_out_of_range_low_gives_nan` — KEEP — the open-input-detection
  branch below the calibrated range.
- `TestLogAmpConversion::test_out_of_range_high_gives_nan` — KEEP — the same branch above range.
- `TestLogAmpConversion::test_all_four_channels_converted_independently` — KEEP — a real
  cross-channel independence guarantee (one channel's conversion must not leak into another's).
- `TestLogAmpConversion::test_logamp_volts_field_holds_pre_conversion_mean` — KEEP — the
  raw-volts-beside-the-converted-value contract `CLAUDE.md`'s snapshot invariant requires.
- `TestLogAmpConversion::test_absent_channel_is_nan_not_zero` — KEEP — "paused ≠ zero," the same
  class of safety-relevant interpretation choice as the vacuum-gauge tests above.
- `TestLogAmpConversion::test_t_passes_through` — KEEP — a real plumbing guarantee a history
  buffer depends on.
- `TestLogAmpConversion::test_connected_flag_true` — KEEP — the `connected` field CLAUDE.md
  requires every snapshot to carry, checked at the actual conversion boundary.
- `TestAmpWaveformConversion::test_window_kv_scales_by_kv_per_volt` — KEEP — the voltage-monitor
  conversion factor, the "one conversion, in one place" invariant's whole reason to exist.
- `TestAmpWaveformConversion::test_window_ma_scales_by_ma_per_volt` — KEEP — the current-monitor
  conversion factor, independently.
- `TestAmpWaveformConversion::test_raw_v_is_mean_of_voltage_waveform` — KEEP — pins the specific
  statistic (mean, not RMS or peak) a meter reading must use.
- `TestAmpWaveformConversion::test_peak_kv_from_sine_waveform` — KEEP — recovers an analytic
  sine's peak through the real conversion pipeline.
- `TestAmpWaveformConversion::test_rms_kv_from_sine_waveform` — KEEP — the analytic sine-RMS
  relation (amplitude/√2) through the real pipeline, distinct statistic from the peak test.
- `TestAmpWaveformConversion::test_v_live_true_when_channel_present` — KEEP.
- `TestAmpWaveformConversion::test_v_live_false_when_channel_absent` — KEEP — the complementary
  branch; together these pin the "paused" flag correctly in both directions.
- `TestAmpWaveformConversion::test_i_live_true_when_current_channel_present` — KEEP — the
  current-channel analogue, independent of `v_live` (one waveform present, the other absent).
- `TestAmpWaveformConversion::test_i_live_false_when_current_channel_absent` — KEEP.
- `TestAmpWaveformConversion::test_window_kv_none_when_channel_absent` — KEEP — the array-level
  analogue of the `v_live` flag (no fabricated array for a paused channel).
- `TestAmpWaveformConversion::test_window_ma_none_when_current_absent` — KEEP — same, current
  side.
- `TestAmpWaveformConversion::test_all_four_amp_labels_present_in_snapshot` — KEEP — a real
  completeness guarantee (a paused channel is `None`-valued, but the key must still exist so a
  tab doesn't KeyError).
- `TestAmpWaveformConversion::test_t_passes_through_to_amp_state` — KEEP.
- `TestAmpWaveformConversion::test_active_profile_passes_through` — KEEP — a field a tab uses to
  decide which channels it can expect data for.
- `TestAmpWaveformConversion::test_connected_flag_true_on_amp_state` — KEEP.
- `TestAmpWaveformConversion::test_rms_ma_from_dc_current_waveform` — KEEP — a concrete DC
  conversion figure (1 V → 10 mA), distinct code path from the AC/sine tests above.

**Total: 26 (26 keep, 0 rewrite, 0 delete)**

---

## tests/test_labjack_stream.py

Production module read: `src/rbl/config/labjack_stream_config.py` and
`src/rbl/hardware/labjack_stream_worker.py`.

- `TestStreamConfig::test_single_channel_profiles_exist` — KEEP — the feature's basic presence.
- `TestStreamConfig::test_single_flags` — KEEP — both positive and negative cases of
  `is_single_channel` in one function, over four profiles.
- `TestStreamConfig::test_choices_are_amp_monitors_only` — KEEP — the documented feature-scope
  restriction (single-channel targets are the 8 HV amp monitors, never a log amp).
- `TestStreamConfig::test_multichannel_profiles_have_no_choices` — KEEP — the complementary case.
- `TestStreamConfig::test_default_single_channel_is_a_valid_choice` — KEEP — a real
  self-consistency guard (the module's own default must be one of its own valid choices).
- `TestStreamConfig::test_fast_profile_is_max_rate` — KEEP — pins three real hardware-rate
  numbers for the SINGLE_FAST profile.
- `TestStreamConfig::test_hires_profile_trades_rate_for_resolution` — KEEP — the documented
  rate/resolution trade-off, a real T7 hardware constraint.
- `TestStreamConfig::test_every_profile_rate_fits_its_resolution_index` — KEEP — a contract test
  in spirit: one rule (aggregate rate ceiling) checked against every profile in `STREAM_PROFILES`
  rather than one profile at a time.
- `TestStreamConfig::test_resolution_index_defaults_to_module_constant` — KEEP — the fallback
  branch for a profile with no explicit override.
- `TestSingleChannelPayload::test_target_channel_is_live_others_paused` — KEEP — see "Flagged
  for the reviewer" above (module-sanctioned `_build_payload` seam).
- `TestSingleChannelPayload::test_current_target_supported` — KEEP — same rationale; a current-
  monitor target is a distinct code path from a voltage-monitor target.
- `TestSingleChannelPayload::test_multichannel_payload_unchanged` — KEEP — same rationale;
  regression guard that the single-channel feature didn't disturb the FULL-profile payload shape.
- `TestWorkerConstruction::test_channel_override_stored` — **DELETE** — writes a constructor
  argument into the private `_channel_override`/`_profile_name` attributes and reads the exact
  same attributes straight back; the real consumer of `_channel_override`
  (`_resolve_scan_names()`, which decides which physical channel actually gets streamed) is
  never called by this test, so a bug where `_resolve_scan_names` ignored the override entirely
  would still pass this test. Matches "writes the private state it then reads back" and "would
  not have caught a real defect."
- `TestWorkerConstruction::test_channel_override_defaults_none` — **DELETE** — same reasoning as
  the previous test for the default-`None` case; `_resolve_scan_names` is grep-confirmed to have
  no other test anywhere in the suite (`tests/test_labjack_stream.py` and no other file
  references `_resolve_scan_names`), so the channel-override routing behaviour these two tests
  gesture at is entirely untested, not merely tested indirectly.

**Total: 14 (12 keep, 0 rewrite, 2 delete)**

---

## tests/test_snapshot_json.py

Production module read: `src/rbl/services/snapshot_json.py`.

- `test_nan_becomes_null_not_a_bare_nan` — KEEP — the specific historical corruption bug named in
  the module docstring (a bare `NaN` token breaks every non-Python JSON reader from that byte
  on), verified with a strict parser that rejects non-standard tokens.
- `test_infinities_become_null` — KEEP — the `Infinity`/`-Infinity` analogue of the same bug.
- `test_dump_json_is_atomic` — KEEP — a real crash-safety guarantee (no `.part` file left behind,
  no truncated file as the only record).
- `test_raw_windows_never_reach_disk` — KEEP — the specific historical bug named in the test
  (`default=str` turning a numpy array into an unparseable, enormous repr string); checks both
  that the keys are absent and that the file stays small.
- `test_no_numpy_repr_strings_in_output` — KEEP — see "Flagged for the reviewer" above (one of
  its three assertions is a harmless tautology; the other two are real).
- `test_asdict_lean_does_not_copy_the_big_arrays` — KEEP — a real performance guarantee (8
  channels × 10 000 samples at 10 Hz must not be deep-copied to be immediately discarded), and
  that the live dataclass's arrays are left untouched by the process.
- `test_amp_summary_matches_the_hv_tab_conventions` — KEEP — pins `amp_summary`'s unit and sign
  conventions (Vpp/2×gain for commanded, pkpk/2 for measured) against what the HV tab actually
  shows an operator, per the test's own docstring.
- `test_amp_summary_current_uses_the_drive_frequency` — KEEP — the specific numeric bias this
  test guards against (peak-based current biases +181% on this rig's noise floor; the
  fundamental-frequency method biases +0.2%) is a real, previously-costly measurement choice.
- `test_amp_summary_suppresses_delta_when_output_is_off` — KEEP — a real and easy-to-get-wrong
  branch: comparing a live setpoint against a channel driving nothing would flag a false -100%
  finding on hardware behaving exactly as commanded.
- `test_amp_summary_marks_unsampled_monitors` — KEEP — "a paused readout is not a zero reading,"
  tested at the summary layer specifically (distinct from the same rule already tested at the
  conversion layer in `test_labjack_link.py`, since this is a different function with its own
  branch for it).
- `test_dc_setpoint_keeps_its_sign` — KEEP — a real polarity-preservation guarantee for DC mode,
  distinct code path (`mode == "DC"`) from the AC tests above.
- `test_slit_summary_reports_the_opening` — KEEP — the aperture/jaw-opening arithmetic an
  operator reads directly off the log.
- `test_funcgen_fields_survive_to_the_log` — KEEP — the specific historical hazard named in the
  test docstring (a generator left on 50 Ω delivers half the commanded voltage into the
  EEL5000's high-Z input, and `load`/`load_ohms` are the only fields that would reveal it), plus
  the instrument's own `9.9e37` high-Z sentinel surviving as a number rather than becoming
  `null` (which would read identically to "unreadable").

**Total: 13 (13 keep, 0 rewrite, 0 delete)**

---

## tests/test_tab_persistence.py

Production modules read: `src/rbl/hardware/galil_workers.py` (`GalilPollWorker` — real),
`src/rbl/hardware/current_monitor.py` (`RollingBuffer` — real), and a full-repo grep for
`LabJackPollWorker` (**no such class exists anywhere in `src/`**).

This file's stated purpose (module docstring: "verify that background polling threads remain
active regardless of which tab is visible") never actually exercises the two production polling
threads it claims to cover. Instead it defines its own from-scratch `threading.Thread` subclasses
— `StubGalilPollThread` ("Pure Python version of GalilPollWorker for testing the loop logic") and
`StubLabJackPollThread` ("Pure Python version of LabJackPollWorker for testing") — and tests
those. `GalilPollWorker` is a real `QThread` subclass in `galil_workers.py`, used by
`motor_tab.py`; `StubGalilPollThread` reimplements its own version of the poll loop rather than
importing and driving the real class, so a real defect in `GalilPollWorker` (e.g. its actual
disconnect handling, its actual polling cadence, a real exception path) would never be caught
here — only a defect in the test's own from-scratch reimplementation could fail these tests.
`LabJackPollWorker` is worse: it is not a stub of anything real. No such class exists in `src/`
today (confirmed by grep across the whole `src/` tree), so `StubLabJackPollThread` and everything
built on it test a class of the test author's own invention with no production counterpart to
regress. This is the exact failure mode ADR 0001 names for a churn test — "would not have caught
a real defect" — taken to its limit: these tests cannot catch a defect in production code because
they never call any.

- `TestGalilPollPersistence::test_thread_runs_multiple_polls` — **DELETE** — exercises
  `StubGalilPollThread`, not `GalilPollWorker`; would not catch a defect in the real class.
- `TestGalilPollPersistence::test_thread_continues_when_not_observed` — **DELETE** — same reason;
  also relies on wall-clock `time.sleep` windows for its pass/fail margin, compounding the churn
  risk with timing flakiness on a loaded CI runner.
- `TestGalilPollPersistence::test_thread_stops_on_request` — **DELETE** — same reason.
- `TestGalilPollPersistence::test_thread_stops_on_disconnect` — **DELETE** — same reason.
- `TestGalilPollPersistence::test_no_errors_on_normal_operation` — **DELETE** — same reason.
- `TestLabJackPollPersistence::test_buffer_fills_while_running` — **DELETE** — exercises
  `StubLabJackPollThread`, which has no production counterpart at all.
- `TestLabJackPollPersistence::test_reads_accumulate_in_hidden_state` — **DELETE** — same reason;
  this is the specific test the docstring claims proves data "still accumulates... when 'tab is
  not visible'" — it proves that about the stub only.
- `TestLabJackPollPersistence::test_buffer_values_match_mock_readings` — **DELETE** — same
  reason.
- `TestLabJackPollPersistence::test_multiple_channels_all_fill` — **DELETE** — same reason.
- `TestLabJackPollPersistence::test_no_errors_on_normal_operation` — **DELETE** — same reason.
- `TestBufferAccumulatesIndependentlyOfUI::test_append_during_snapshot` — KEEP — this one drives
  the real `rbl.hardware.current_monitor.RollingBuffer` directly (concurrent writer/reader
  threads against the real `append`/`snapshot` public API), a real thread-safety property a
  background poll thread and the GUI thread both depend on simultaneously.
- `TestBufferAccumulatesIndependentlyOfUI::test_snapshot_does_not_clear_buffer` — KEEP — same
  real class, a distinct real behaviour (`snapshot()` must be non-destructive, since the docstring
  says "UI reading (snapshot) should not block or clear the buffer").

**Total: 12 (2 keep, 0 rewrite, 10 delete)**

---

## tests/test_beamline.py

Production module read: `src/rbl/state/beamline.py`, `src/rbl/state/hv_interlock_link.py`.

### TestMotorIngestion

- `test_emits_motor_state_keyed_by_slit_label` — KEEP — the real `ingest_motor_poll` → typed
  `MotorState` snapshot path, asserting on the signal payload a tab renders.
- `test_pos_mm_matches_counts_to_mm` — KEEP — the counts→mm conversion, the "one conversion, one
  place" invariant for the motor axis.
- `test_disconnected_state_is_empty` — KEEP — the disconnected/empty-state branch.

### TestLabjackIngestion

- `test_logamp_current_conversion` — KEEP — real ingestion path, real conversion.
- `test_logamp_paused_channel_is_nan` — KEEP — "paused ≠ zero," at the `Beamline` boundary
  specifically (`test_labjack_link.py` covers the same rule via `LabJackFeed`; this is the
  narrower unit-level check with the raw payload dict, not a duplicate — different construction
  path, same production method).
- `test_amp_voltage_and_current_conversion` — KEEP — real conversion factors, asserted at the
  `Beamline` boundary.
- `test_amp_missing_channel_is_nan` — KEEP — the missing-data branch.
- `test_disconnected_marks_both_subsystems` — KEEP — a real cross-subsystem guarantee
  (`disconnect_labjack()` must mark both the log-amp and amp snapshots disconnected together, not
  just one).

### TestBeamReconstruction

- `test_no_beam_without_all_four_edges` — KEEP — the degenerate-input guard on
  `reconstruct_beam`, a real safety-relevant "don't report a beam estimate from partial data"
  rule.
- `test_centred_beam_from_symmetric_currents` — KEEP — a planted-symmetric-currents-recover-a-
  centred-beam inversion test, through the real motor+labjack ingestion path and the real
  `reconstruct_beam` call — the presumed-keeper category (plants a beam and recovers it) applied
  at the `Beamline` integration boundary rather than the pure-math layer ticket 07 already
  confirmed `beam_reconstruction.py` at.

### TestFuncgenIngestion

- `test_readback_converts_to_channel_snapshots` — KEEP — real readback→snapshot conversion.
- `test_error_channel_is_skipped` — KEEP — a real defensive branch (a channel that errored on
  readback must not appear as if it had valid data).
- `test_disconnected_clears_channels` — KEEP — the disconnected branch.

### TestDeviceOwnership

- `test_constructs_galil_and_labjack` — KEEP — the "Beamline is the single owner of every
  instrument" invariant CLAUDE.md states as one of three the whole design rests on, checked
  directly against the real driver classes.
- `test_funcgens_start_unconnected` — KEEP — real initial-state guarantee.
- `test_shutdown_is_safe_with_nothing_connected` — KEEP — a real and easy-to-break guarantee
  (must not raise with nothing connected — the common case for a dev/CI run).
- `test_shutdown_disconnects_galil` — KEEP — swaps a `MagicMock` into the **public** `galil`
  attribute (not private — confirmed public via `test_constructs_galil_and_labjack` asserting on
  `beamline.galil` directly) and asserts on call order/occurrence, the documented teardown
  contract from CLAUDE.md §4 ("abort Galil motion before disconnecting").
- `test_shutdown_never_disables_funcgen_outputs` — KEEP — same public-attribute pattern, and
  tests the specific documented exception in CLAUDE.md §4 ("never disable function-generator
  outputs on exit — they are meant to hold state"), including that `close()` is still called.
- `test_shutdown_survives_a_broken_generator` — KEEP — a real fault-isolation guarantee: one
  generator raising on close must not stop the Galil/LabJack teardown that follows.
- `test_disconnect_labjack_stops_stream_before_closing_handle` — KEEP — see "Flagged for the
  reviewer" above; call-order assertions on injected mocks are real behaviour checks (the
  documented "stop the stream before closing the T7 handle" ordering from CLAUDE.md §4), one
  private-attribute read (`_lj_worker is None`) kept for lack of a cheaper public observation.
- `test_labjack_connected_signal_carries_serial` — KEEP — see "Flagged for the reviewer" above;
  the assertion is on the public, downstream-visible `labjack_connected` signal payload.

### TestCommandSurface

- `test_set_channel_rejects_over_limit_amplitude` — KEEP — the ±5 V peak interlock, a
  load-bearing safety feature per CLAUDE.md §7, checked at its actual chokepoint
  (`Beamline.set_channel`).
- `test_set_channel_accepts_safe_amplitude` — KEEP — the complementary accept-path, and that the
  driver call actually happens (not just that rejection works).
- `test_interlock_rejects_identically_via_direct_call_or_widget_path` — **DELETE** — both
  "call sites" the test defines (`apply_via_direct_beamline_call` and
  `apply_via_simulated_widget_call`) invoke the exact same expression,
  `beamline.set_channel("A1", self.OVER_LIMIT)`, with nothing between them that differs — there
  is no second, distinct call path being exercised despite the docstring's claim ("an
  interlock-violating amplitude is rejected the same way regardless of which caller reaches
  Beamline.set_channel"). The test can only fail if `set_channel` is non-deterministic, which it
  isn't, so `result_direct == result_widget` cannot ever be false; a real second bypass path
  (e.g. a widget that used to call the driver directly) would not be caught here since no widget
  code is invoked at all. This restates, verbatim, what
  `test_set_channel_rejects_over_limit_amplitude` already tests and would pass or fail in
  lockstep with it — the near-duplicate criterion from ticket 08's own text.
- `test_set_channel_no_generator_connected` — KEEP — the not-connected branch, a distinct guard
  from the interlock (rejects before ever reaching the interlock check or the driver).
- `test_apply_all_blocks_when_any_channel_over_limit` — KEEP — the multi-channel "any one over
  limit blocks the whole apply" atomicity guarantee — neither generator's driver is touched, a
  real all-or-nothing property distinct from the single-channel test.
- `test_apply_all_configures_then_enables_then_aligns_in_order` — KEEP — the documented
  configure→enable→align command ordering, asserted via real call-order tracking on the injected
  driver mock (this is the sanctioned way to assert ordering when the only observable effect is
  calls made to a physical instrument).
- `test_apply_all_off_channels_disabled_before_on_channels_enabled` — KEEP — a distinct and
  safety-relevant ordering guarantee from the previous test (turning channels off before turning
  others on, to avoid a moment where both drive simultaneously in an unintended configuration).
- `test_all_outputs_off_turns_off_every_channel` — KEEP — the emergency/shutdown-adjacent
  all-off command actually reaches every channel on both generators.

### TestRampedSetChannel

- `test_offset_only_change_ramps_instead_of_apply` — KEEP — the documented ramp-eligibility rule
  (Section 5.4): an offset-only change on an already-running channel ramps rather than jumping.
- `test_amplitude_only_change_ramps_instead_of_apply` — KEEP — the complementary
  amplitude-only case.
- `test_both_amp_and_offset_changing_falls_back_to_apply` — KEEP — the documented boundary of
  ramp-eligibility (both changing at once falls back), a real and easy-to-get-wrong condition.
- `test_frequency_change_falls_back_to_apply` — KEEP — a distinct disqualifying change
  (frequency) from the amp/offset boundary case.
- `test_output_currently_off_falls_back_to_apply` — KEEP — a distinct disqualifying condition
  (channel not currently running) from a parameter-shape disqualifier.
- `test_get_state_error_falls_back_to_apply` — KEEP — the defensive fallback when readback itself
  fails, a real fault-tolerance branch.
- `test_unramped_call_is_unaffected` — KEEP — confirms the ramp feature is opt-in and doesn't
  change default `set_channel` behaviour.

### TestHvInterlock

- `test_no_vacuum_reading_blocks_a_nonzero_command` — KEEP — constructs a raw `Beamline()` (not
  the pre-primed fixture) and exercises the real default "no vacuum reading yet" block through
  the public `set_channel` API — no private access.
- `test_healthy_reading_permits_the_command` — KEEP — uses the pre-primed `beamline` fixture (see
  the fixture note above) but the test body itself only calls the public `set_channel` and
  asserts on its public result/driver-call — no private access in the test itself.
- `test_high_pressure_blocks_even_with_a_fresh_reading` — **REWRITE** — sets
  `beamline._hv_pressure_torr` and `beamline._hv_pressure_at` directly. Sanctioned path: build a
  real `VacuumState` with a gauge reading at/above the absolute lockout pressure and call
  `beamline.on_vacuum_changed_for_interlock(state)` (the same method production wires to
  `vacuum_changed`), after `beamline.set_interlock_gauges({...})` selects that gauge.
- `test_stale_reading_blocks_even_though_pressure_value_was_once_good` — **REWRITE** — sets
  `beamline._hv_pressure_torr` and a manually-backdated `beamline._hv_pressure_at` directly.
  Sanctioned path: call `on_vacuum_changed_for_interlock(state)` with a healthy `VacuumState` to
  set a real, fresh timestamp through production code, then monkeypatch `hv_interlock_link.time.
  monotonic` to advance past `GAUGE_STALE_TIMEOUT_S` before calling `set_channel` — exercising the
  real staleness check (`hv_pressure_is_stale()`) instead of hand-writing its cached inputs.
- `test_apply_all_channels_blocked_by_interlock_too` — KEEP — a raw, unprimed `Beamline()`
  again; no private access, tests the multi-channel apply path is blocked by the same interlock.
- `test_transition_into_block_ramps_live_channel_to_zero` — **REWRITE** — sets
  `beamline._hv_pressure_torr` directly and calls the private `beamline._recompute_hv_interlock()`
  twice by hand. Sanctioned path: call `on_vacuum_changed_for_interlock(healthy_state)` then
  `on_vacuum_changed_for_interlock(high_pressure_state)` — production's own transition trigger,
  which calls `_recompute_hv_interlock()` internally exactly once per call, the same as the real
  vacuum-poll → interlock pipeline.
- `test_hv_interlock_changed_is_emitted_on_recompute` — **REWRITE** — calls the private
  `beamline._recompute_hv_interlock()` directly. Sanctioned path: call
  `on_vacuum_changed_for_interlock(state)`, which triggers the same recompute and the same
  `hv_interlock_changed` emission through the production entry point.

### TestMoveSlit

- `test_move_slit_converts_label_to_axis_and_mm_to_counts` — KEEP — the label→axis and mm→counts
  conversions, a real and easy-to-invert-by-mistake mapping.
- `test_move_slit_fails_when_galil_not_connected` — KEEP — the not-connected guard branch.
- `test_emergency_stop_calls_abort` — KEEP — a safety-critical command path.
- `test_emergency_stop_noop_when_not_connected` — KEEP — the complementary not-connected branch
  (must not raise trying to abort a controller that was never connected).

### TestSnapshotsCarryWhatTheTabsNeed

- `test_logamp_state_carries_window_time_and_raw_volts` — KEEP — the fields the class docstring
  explains exist precisely so tabs stop reconverting data themselves.
- `test_unsampled_log_amp_is_absent_from_volts_not_zero` — KEEP — the same
  paused-vs-faulty distinction as elsewhere, at the field-presence level specifically ("absent
  from `volts`," not just NaN in `currents`) — a different assertion than the currents-only tests
  above.
- `test_amp_state_carries_window_time_and_sample_period` — KEEP — the timing fields a history
  buffer/scope view depends on.
- `test_live_flags_track_which_monitors_were_scanned` — KEEP — cross-checks `v_live`/`i_live`
  together with the actual NaN'd `rms_ma` in one assertion, a real combined guarantee.
- `test_full_resolution_window_is_scaled_once_here` — KEEP — this is literally the test for the
  "One conversion" invariant CLAUDE.md states as load-bearing: the scope view's raw samples are
  converted exactly once, in `Beamline`, not re-derived in the tab.
- `test_no_waveform_leaves_the_full_window_empty` — KEEP — "must not fabricate an array," a real
  and specifically-motivated guarantee (the scope view needs to distinguish "nothing to draw"
  from a real, flat signal).

**Total: 53 (48 keep, 4 rewrite, 1 delete)**
