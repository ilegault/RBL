# 108: FG tab wording — input peak, no kV

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 106

**Spec:** `.scratch/axis-offset-and-vpp/spec.md` (decisions D4, D5 FG side)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

## What to build

The Function Generators tab (`src/rbl/gui/funcgen_tab.py`) already takes amplitude in Vpp.
What changes is the feedback beside the inputs. Each channel shows its **input peak**
(`CONTEXT.md` "Raster drive") and nothing in kV. The help text states the limit in
generator volts only. Blocked by 106 only because both tickets edit `funcgen_tab.py`.

## Acceptance criteria

- [x] `ChannelPanel.lbl_hv` (set in `ChannelPanel._update_hv_label`) reads exactly
      `input peak 3.20 V` for Sine, 4.4 Vpp, +1.0 V offset, and `input peak 1.50 V` for
      shape DC at −1.5 V. It uses `channel_peak_volts` / `peak_status` from
      `src/rbl/hardware/funcgen_safety.py`, with the same WARN (> 4 V) and FAULT (> 5 V)
      roles the label uses today. A test drives the panel through
      `beamline.funcgen_setpoints.update(...)` and asserts both strings. Test: tests/test_funcgen_panel_status.py::`test_channel_input_peak_line`.
- [x] The FG tab help text (the paragraph around the "Amplitude is peak-to-peak (Vpp)"
      wording in `FuncGenTab.__init__`) still states: input peak = |offset| + ½·amplitude,
      Apply blocked above 5 V, confirmation above 4 V, 10 Vpp at 0 V reaches the limit,
      4 V + 4 Vpp is over it. It contains no `kV`. Test: tests/test_funcgen_panel_status.py::`test_help_text_states_the_limit_in_generator_volts`.
- [x] No `text()` or `toolTip()` of any `ChannelPanel` widget, and no FG tab help label,
      contains `kV/plate`, `kV p-p` or `5 kV`. A test walks `tab.panels.values()` and the
      help label and asserts this. Test: tests/test_funcgen_panel_status.py::`test_fg_tab_text_has_no_kv`.
- [x] Amplitude and offset boxes, their ranges, the Mirror button's behaviour
      (`FuncGenTab._mirror_channel`, amplitude and frequency only) and the readback line
      (`ChannelPanel.update_readback`) are unchanged. Existing tests in
      `tests/test_funcgen_panel_status.py` and `tests/test_setpoint_sync.py` pass with
      no deletions. Test: the existing `test_update_signals_only_on_a_real_change` and the rest of tests/test_funcgen_panel_status.py and tests/test_setpoint_sync.py, unedited.

Tests may fake the generator connection as `tests/test_funcgen_panel_status.py` does.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --durations=25
```

## Comments

2026-11-13: Updated `ChannelPanel._update_hv_label` to display `input peak {peak:.2f} V` using `peak_status` from `funcgen_safety.py` with WARN/FAULT styling, removed all kV conversions and references from ChannelPanel widgets and the FG tab help text banner, and bound `lbl_help`/`note_lbl` on `FuncGenTab`. Added tests `test_channel_input_peak_line`, `test_help_text_states_the_limit_in_generator_volts`, and `test_fg_tab_text_has_no_kv` in `tests/test_funcgen_panel_status.py`. All existing mirror/setpoint sync tests pass unedited.
