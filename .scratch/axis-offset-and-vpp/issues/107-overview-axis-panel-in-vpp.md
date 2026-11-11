# 107: Overview axis panel in Vpp, with input peak

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/axis-offset-and-vpp/spec.md` (decisions D1, D2, D3 amplitude part, D4 format, D5 Overview side)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

## What to build

On the Overview, each axis's raster drive panel (`AxisDriveControl`,
`src/rbl/gui/widgets/axis_drive.py`) takes and shows **amplitude** in Vpp, the same number
the generator and the FG tab use (`CONTEXT.md` "Raster drive": Amplitude). The kV-at-the-plate
line beside the inputs is replaced by the **input peak**. Offset is not added here
(ticket 110 adds it); this ticket changes units and wording only.

## Acceptance criteria

- [x] `AxisDriveControl.spn_amp` has range `0.0` to `MAX_AMP_VPP`, 3 decimals, single step
      `0.1`, and unit label `Vpp`. `AxisDriveControl.bar` runs `0` to `MAX_AMP_VPP` with
      unit `Vpp`. `MAX_AMP_PEAK_V`, `peak_to_vpp` and `vpp_to_peak` no longer exist in
      `axis_drive.py`, and `grep -rn "vpp_to_peak\|peak_to_vpp\|MAX_AMP_PEAK_V" src tests --include=*.py`
      returns nothing. Test: tests/test_overview_tab.py::`test_axis_amplitude_box_and_bar_are_in_vpp`.
- [x] Round trip, in `tests/test_overview_tab.py`: `beamline.funcgen_setpoints.update_axis("X", amp_vpp=4.0)`
      shows `4.000` in `tab.drives["X"].spn_amp`. Typing `3.0` into that box writes
      `amp_vpp == 3.0` (not 6.0) to both A1 and A2 in `beamline.funcgen_setpoints`. Test: tests/test_overview_tab.py::`test_axis_amplitude_round_trips_in_vpp`.
- [x] The existing assertion near line 221 of `tests/test_overview_tab.py`
      (`"0.75" in tab.drives["X"].lbl_readback.text()`, commented "1.5 Vpp = 0.75 V pk")
      is rewritten in place, keeping the test function's name, to assert the readback shows
      `1.50 Vpp` for both plates. The readback line reads
      `X+ 1.50 Vpp · X− 1.50 Vpp @ <freq .4g> Hz out=<ON|OFF|SPLIT>`.
      No test function is deleted. Test: tests/test_overview_tab.py::`test_funcgen_readback_feeds_the_axis_amplitude_bar` (existing, rewritten in place).
- [x] Three tests in `tests/test_setpoint_sync.py` encode the factor of two and are
      rewritten in place under the same names, so they assert that the **same** number
      appears on both screens: `test_overview_edit_reaches_the_funcgen_panels`
      (Overview 4.0 → FG 4.0), `test_funcgen_edit_reaches_the_overview_boxes`
      (FG 6.0 → Overview 6.0) and `test_the_unit_conversion_round_trips` (3.5 both ways;
      its docstring now says why no conversion exists). No test function is deleted. Test: tests/test_overview_tab.py::`test_axis_input_peak_line_text_and_colour`.
- [x] `AxisDriveControl.lbl_hv` reads exactly `input peak 2.00 V` for 4 Vpp, computed
      with `peak_status` from `src/rbl/hardware/funcgen_safety.py`. Its colour role is none
      at or below 4 V, `theme.WARN` above 4 V and `theme.FAULT` above 5 V. ` ⚠ pair differs`
      is still appended when `axis_matched` is false. A test asserts the text and the
      `FAULT` colour for 10.2 Vpp. Test: tests/test_overview_tab.py::`test_axis_panel_text_has_no_peak_volts_or_kv`.
- [x] No input-side text on the axis panel (labels and tooltips of `spn_amp`, `lbl_hv` and
      `bar`) contains `V pk`, `kV/plate` or `5 kV`. A test asserts this over those three
      widgets' `text()`/`toolTip()`. The module docstring's "AMPLITUDE IS IN PEAK VOLTS"
      section is rewritten to say why amplitude is Vpp (one unit from the box to the
      instrument) in the existing WHY style. Test: tests/test_setpoint_sync.py::`test_overview_edit_reaches_the_funcgen_panels`, `test_funcgen_edit_reaches_the_overview_boxes`, `test_the_unit_conversion_round_trips` (all existing, rewritten in place).

The Overview's measured amplifier bars (`OverviewTab.hv_bars`, ±5 kV) are not touched.
Tests may fake the generator connection as the existing Overview tests do. The shared
`FuncGenSetpoints` must be the real one.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --durations=25
```

## Comments

### 2026-11-10 implementation

AxisDriveControl now takes/shows Vpp (box, bar, readback); lbl_hv reads "input peak N V" via peak_status with WARN/FAULT roles; vpp_to_peak, peak_to_vpp, MAX_AMP_PEAK_V deleted. Four named tests added; the readback test, three setpoint-sync tests and other tests encoding the factor of two rewritten in place, none deleted. Local gate: ruff, tests-first, type_gate (ratchet 139) green; pytest 2847 passed, 1 local-only failure test_screenshot_overview (Windows access violation in matplotlib/pyparsing GC on local Python 3.14, unrelated).
