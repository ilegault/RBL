# 40: No fallback capacitance outside the UI: measured, or a named sizing assumption

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 36

**Spec:** `.scratch/amplifier-load-and-spikes/spec.md` (Part A)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/config/calibration_config.py` (`CAL_LOAD_CAP_PF`, `_resolve_load_pf`, `ac_peak_current_ma`, `ac_max_peak_kv`), `src/rbl/hardware/ramp_engine.py` (`RampEngine.__init__`, `_log_predicted_current`), `src/rbl/services/calibration_runner.py` (the two uses of `CAL_LOAD_CAP_PF`), `src/rbl/services/load_characterizer.py` (`start_mode_a`), `tests/test_calibration_config_load.py` (all six tests), `docs/AMP_ENVELOPE_AND_HV_SAFETY_PLAN.md` section 1.4.

## What to build

- `calibration_config` replaces `CAL_LOAD_CAP_PF` with `SIZING_ASSUMPTION_PF = 3000`, commented as
  an assumption, not a measurement: used only to size the first amplitude of a
  characterization run or a ramp on a plate position with no on-plates result, and
  deliberately larger than anything measured so an unmeasured load is treated as large.
- `_resolve_load_pf(load_pf, amp_label)` returns `load_pf` if given, else
  `characterization_history.newest_on_plates_c_pf(amp_label, now)`, else
  `SIZING_ASSUMPTION_PF`. It never reads `load_calibration_store`.
- `RampEngine`'s default `load_pf_for_label` is `lambda label: _resolve_load_pf(None, label)`.
- `LoadCharacterizer` starts its Mode A estimate from `_resolve_load_pf(None, amp_label)`.
- `CalibrationRunner`'s prediction note names the C it used and whether it was `measured` or
  the `sizing assumption`.
- Every remaining reference to `CAL_LOAD_CAP_PF` in `src/` (docstrings included) is updated.
- The physics plan's section 1.4 gets, as the first line under its heading: "Superseded
  2026-10: capacitance is measured per amplifier and plate position; see the Load
  Characterization tab. The figures below are history."
- Rewrite the six tests in `tests/test_calibration_config_load.py` in place, same names,
  against the history module and `SIZING_ASSUMPTION_PF`.

Tests may fake: nothing on disk; history files in the temp folder.

## Acceptance criteria

- [ ] `_resolve_load_pf(None, 'X+')` with an on-plates X+ result of 1600 pF returns 1600.0; with only a cable-only result it returns 3000.
- [ ] Rewritten `test_ac_max_peak_kv_uses_measured_capacitance_for_labelled_channel` passes using a measured Y+ result written through `write_result`.
- [ ] A `RampEngine` built without `load_pf_for_label` returns 1600.0 from its load lookup for a measured label and 3000 for an unmeasured one.
- [ ] `grep -rn CAL_LOAD_CAP_PF src` finds nothing, and a test asserts `not hasattr(calibration_config, 'CAL_LOAD_CAP_PF')`.
- [ ] The first line under the section 1.4 heading of `docs/AMP_ENVELOPE_AND_HV_SAFETY_PLAN.md` begins `Superseded 2026-10`.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
