# 106: Apply greys out over the 5 V input limit

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/axis-offset-and-vpp/spec.md` (decisions D16–D19)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

## What to build

While any channel an Apply button would send has an **input peak** (`|offset| + amplitude/2`,
see `CONTEXT.md` "Raster drive") above 5 V, that button is disabled and its tooltip names
each offending channel. The operator sees the problem before reaching for the button.
Today the operator can click Apply and is refused afterwards. That refusal, inside
`Beamline.set_channel` and `Beamline.apply_all_channels`
(`src/rbl/state/funcgen_control.py`), stays exactly as it is: it is the backstop on every
path, and this ticket must not edit it.

Three buttons:

- Overview **Apply All - X & Y** (`OverviewTab.btn_apply_all`, enabled today in
  `OverviewTab._redraw_funcgens`, `src/rbl/gui/overview_tab.py`).
- FG tab **Apply All 4 Channels** (`FuncGenTab.btn_apply_all`, enabled today in
  `FuncGenTab._refresh_connection_ui`, `src/rbl/gui/funcgen_tab.py`).
- FG tab per-channel **Apply** (`ChannelPanel.btn_apply`, enabled today in
  `ChannelPanel.set_connected`).

## Acceptance criteria

- [ ] New pure function `over_limit_channels(params_by_key: dict) -> list[tuple[str, float]]`
      in `src/rbl/hardware/funcgen_safety.py`. It takes `{key: ChannelParams}` and returns
      `(key, input_peak)` for every channel whose `channel_peak_volts(...)` is above
      `PEAK_MAX_VOLTS + 1e-9`, ordered A1, A2, B1, B2. A unit test in
      `tests/test_funcgen_safety.py` asserts: 10 Vpp at 0 V → not listed (exactly 5.0 V);
      4 Vpp at +3.5 V → listed with 5.5; DC shape at −5.2 V → listed with 5.2; the
      ordering with two offenders. Test: tests/test_funcgen_safety.py::`test_over_limit_channels_lists_offenders_in_order`.
- [ ] Overview: with generator A connected, set A2's setpoint over the limit through
      `beamline.funcgen_setpoints.update("A2", amp_vpp=4.0, offset_v=-3.4)` (no widget
      typing). The test asserts `tab.btn_apply_all.isEnabled()` is `False` and that the
      tooltip contains `X− (A2) input peak 5.40 V — over the 5 V limit`. Lowering the
      offset back re-enables the button and restores its original tooltip text. A channel
      on a generator that is **not** connected does not disable the button. Test: tests/test_overview_tab.py::`test_apply_all_greys_out_over_the_input_limit`.
- [ ] FG tab: the same setpoint disables `FuncGenTab.btn_apply_all` and A2's
      `ChannelPanel.btn_apply`, with the same tooltip line. A1's `btn_apply` stays enabled. Test: tests/test_funcgen_panel_status.py::`test_fg_apply_buttons_grey_out_over_the_input_limit`.
- [ ] Every enable decision calls `over_limit_channels`. No widget repeats the
      `> PEAK_MAX_VOLTS` comparison. The state is re-evaluated on
      `FuncGenSetpoints.changed` and on every connection change, so it does not wait for
      a redraw tick. Test: tests/test_overview_tab.py::`test_greying_follows_setpoint_changes_without_a_redraw`.
- [ ] The 4 V confirmation dialogs (`OverviewTab._on_apply_all`,
      `FuncGenTab._apply_channel`, `FuncGenTab._apply_all`) and `funcgen_control.py` are
      unchanged. `git diff --stat` shows no change to `src/rbl/state/funcgen_control.py`. Test: tests/test_overview_tab.py::`test_high_peak_confirmation_still_asked_above_4_v`.

Tests may fake the generator connection the way existing tests in
`tests/test_overview_tab.py` and `tests/test_funcgen_panel_status.py` do. The setpoint
model (`FuncGenSetpoints`) and `over_limit_channels` must be the real ones.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --durations=25
```

## Comments
