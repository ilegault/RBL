# Spec: Axis offset on the Overview, amplitude in Vpp everywhere, Send to drive

Status: ready-for-agent
Date: 2026-11-10
Related: `docs/adr/0001-tests-first-and-no-muted-failures.md`, `CONTEXT.md` ("Raster
drive": Amplitude, Offset, Linked pair, Input peak; "Known collisions": "Peak" means three
things). Safety background: `docs/AMP_ENVELOPE_AND_HV_SAFETY_PLAN.md`.

> ADR 0001 is binding on all test work here: a failing test is fixed or escalated, never muted.
> The ±5 V interlock is load-bearing (AGENTS.md §7). This work adds checks in front of it and
> removes none: `Beamline.set_channel` and `Beamline.apply_all_channels` are not edited.

## Problem Statement

- The Raster Planner computes each axis's generator offset (`gen_offset_v`, equal and
  opposite on the two plates) but only prints it. Getting it onto the generators means typing
  it per channel on the Function Generators (FG) tab. The Overview axis panel
  (`src/rbl/gui/widgets/axis_drive.py`, `AxisDriveControl`) has no offset at all; its
  docstring assumes the raster always runs centred.
- The Overview axis panel takes amplitude in **peak volts** ("V pk") while the FG tab and the
  generator itself use **Vpp**. The operator switches between the two in their head.
- Both screens print kV-at-the-plate conversions and "5 kV" next to the inputs
  (`→ ±2.000 kV/plate · 2.00 of 5 V in`, `→ ±2.0000 kV/plate (4.0000 kV p-p) | peak 2.000 V`,
  FG help text "±5 V input (= ±5 kV/plate)"). That puts a third unit in front of the operator
  while they type.
- An over-limit setpoint is only refused after Apply is clicked.

## Solution

1. **Amplitude is Vpp on every input.** The Overview axis panel's amplitude box, its
   commanded-vs-measured bar and its readback line switch to Vpp. The FG tab already uses
   Vpp; only its labels change.
2. **One feedback line next to the inputs: input peak.** Each axis panel and each FG channel
   panel shows `input peak 3.20 V`, computed live from the typed values with
   `rbl.hardware.funcgen_safety.peak_status`. No kV figure appears beside any input.
3. **Axis offset on the Overview**, with a **Link ±** checkbox (ticked by default). Linked:
   one Offset box; X+ gets the value and X− gets its negative. Unlinked: two boxes, one per
   plate.
4. **Send to drive** on the Raster Planner writes the plan's amplitude, offset and frequency
   for both axes into the shared setpoints. It never commands hardware; Apply still does
   that.
5. **Apply is disabled while any channel it would send is over the 5 V input limit**, and
   its tooltip names the channel(s).

## Decisions

### Amplitude and wording

- D1 `AxisDriveControl.spn_amp` is in Vpp, range `0.0` to `MAX_AMP_VPP` (10), 3 decimals,
  single step `0.1`. Its unit label is `Vpp`. `params_edited` keeps emitting Vpp, now
  without conversion. Delete `MAX_AMP_PEAK_V`, `peak_to_vpp` and `vpp_to_peak` from
  `axis_drive.py`; nothing else may halve or double an amplitude for display.
- D2 The axis bar (`AxisDriveControl.bar`) runs `0` to `MAX_AMP_VPP` with unit `Vpp`. Its
  target is the commanded Vpp and its value is the measured `amp_vpp` of the + channel.
- D3 The axis readback line is
  `X+ 4.00 Vpp +0.400 V · X− 4.00 Vpp −0.400 V @ 517 Hz out=ON`
  (amplitude 2 decimals, signed offset 3 decimals, frequency `.4g`). The split-pair warning
  colour rule is unchanged.
- D4 The input-peak line replaces `AxisDriveControl.lbl_hv`'s text and `ChannelPanel.lbl_hv`'s
  text. Format: `input peak {peak:.2f} V`. Colour roles: none at or below 4 V, `theme.WARN`
  above `PEAK_WARN_VOLTS`, `theme.FAULT` above `PEAK_MAX_VOLTS` (the same `+1e-9` tolerance
  `peak_status` uses). On an axis the shown peak is the larger of its two channels' input
  peaks. Each channel's peak uses that channel's real shape and offset from the setpoints
  (DC shape → `|offset|`), not a hard-coded `"Triangle"` and `0.0`. On the Overview,
  ` ⚠ pair differs` is still appended when `axis_matched` is false.
- D5 Remove every kV-at-the-plate figure and every "5 kV" from input-side text: the two
  `lbl_hv` lines, their tooltips, the `spn_amp` tooltips, and the FG tab help text (around
  `funcgen_tab.py` line 627). The help text keeps its rule, stated in generator volts only:
  input peak = |offset| + ½·amplitude, blocked above 5 V, confirm above 4 V. Tooltips may say
  "input peak" and "Vpp"; they never say "V pk", "kV/plate" or "5 kV".
- D6 Unchanged: the Overview amplifier bars (`hv_bars`, measured output in kV, ±5 kV
  scale), the HV Amplifiers tab, and the Raster Planner's kV physics lines (`lbl_plate_x/y`,
  "±… kV per plate · … kV plate-to-plate", the kV/plate rating warning).

### Offset and linking

- D7 The link state lives in `FuncGenSetpoints` (`src/rbl/state/setpoints.py`), one flag per
  axis, default linked, never saved; every launch starts linked with 0 V offsets. It is
  kept out of the widget so that the planner, both tabs and the tests all read one flag
  without constructing a widget.
- D8 New `FuncGenSetpoints` surface (names are binding):
  - `link_changed = Signal(str, bool)`: axis, linked.
  - `axis_linked(axis) -> bool`.
  - `set_axis_offset(axis, offset_v)`: on a linked axis, sets the + channel to `offset_v` and
    the − channel to `-offset_v`.
  - `set_linked(axis, linked)`: linking keeps the + channel's offset and sets the − channel
    to its negative; unlinking changes no offset. Emits `link_changed` only on a real change.
  - `send_plan(axis, amp_vpp, offset_v, freq_hz)`: sets both channels' `amp_vpp`,
    `freq_hz` and `shape="Triangle"`, sets the offsets equal and opposite, and links the
    axis. It does not touch `output_on`, `phase_deg`, `start_phase_deg` or `load`.
- D9 A per-channel `update(key, offset_v=...)` on a linked axis that leaves the pair no
  longer equal and opposite (`|plus + minus| > 1e-9`) unlinks that axis and emits
  `link_changed`. This is how an FG tab offset edit shows up on the Overview: the panel
  flips to its two-box view showing exactly what was typed.
- D10 `update_axis` drops `offset_v` the same way it already drops `phase_deg`. The same
  offset on both plates is common-mode and moves nothing, so an axis-level write must never
  produce it.
- D11 `axis_matched` is unchanged (amplitude, frequency and output only). Offsets on an
  unlinked pair are intended and never raise "pair differs".
- D12 Overview axis panel layout: a **Link ±** `QCheckBox` and, linked, one `Offset:` box;
  unlinked, `X+ offset:` and `X− offset:` boxes (the axis's own labels for Y). All offset
  boxes are `QuietDoubleSpinBox` with range `±MAX_GEN_VOLTS`, 4 decimals, unit label `V`.
  They never clamp against amplitude. New signals on `AxisDriveControl`, wired by
  `OverviewTab`: `offset_edited(str axis, float offset_v)` for the linked box,
  `channel_offset_edited(str key, float offset_v)` for each unlinked box, and
  `link_toggled(str axis, bool linked)`. Updates coming from the model are written with
  `sync_value` under the existing `_syncing` guard, like amplitude.

### Send to drive

- D13 The Raster Planner gets a **Send to drive** `QPushButton` (`btn_send_drive`) directly
  under `lbl_gen_y`. One press calls `send_plan` for X and Y with `gen_amp_vpp`,
  `gen_offset_v` and `sb_freq_x_hz` / `sb_freq_y_hz`. It is reached through
  `self._beamline.funcgen_setpoints`. It never calls a `Beamline` command.
- D14 `btn_send_drive` is disabled when `self._beamline` is `None`, when there is no
  solution, when any of the four values is not finite, or when either axis's `gen_peak_v`
  is above `PEAK_MAX_VOLTS`. Its tooltip then states which of these applies. After a
  send, a status label under the button reads
  `Sent X: 4.000 Vpp, +0.400 V @ 517 Hz · Y: … — press Apply on the Overview`.
- D15 The planner's generator line (`lbl_gen_x` / `lbl_gen_y`, built in `_show_drive`)
  becomes `4.000 Vpp, offset +0.400 V (X− −0.400 V) · input peak 2.40 V`, or
  `4.000 Vpp, offset 0 V · input peak 2.00 V` when the offset is zero. Above 5 V the
  `theme.FAULT` style stays and ` — over the 5 V limit` is appended. Text and enable
  logic that do not depend on Qt are computed by a pure function (no widget) so they can
  be tested with plain dicts.

### Apply disabled over the limit

- D16 New pure function in `src/rbl/hardware/funcgen_safety.py`:
  `over_limit_channels(params_by_key: dict) -> list[tuple[str, float]]` returns
  `(key, input_peak)` for every channel above `PEAK_MAX_VOLTS`, ordered A1, A2, B1, B2.
  Every greying decision below calls it. No widget repeats the comparison.
- D17 Enable rules (connection rules unchanged, ANDed with these):
  - Overview **Apply All - X & Y**: disabled if any channel on a connected generator is over
    the limit.
  - FG **Apply All 4 Channels**: the same rule.
  - FG per-channel **Apply**: disabled if that channel is over the limit.
  They are re-evaluated on every setpoint change and every connection change.
- D18 While a button is disabled for the limit, its tooltip names each offending channel:
  `X− (A2) input peak 5.40 V — over the 5 V limit. Lower the amplitude or offset.` (one
  line per channel). Otherwise the button keeps its existing tooltip.
- D19 Unchanged: the 4 V confirmation dialogs, `Beamline`'s refusal above 5 V, and the
  HV pressure interlock (`hv_interlock_status_for`). Greying is a front-end courtesy;
  `Beamline` remains the backstop on every path.

### Glossary and records

- D20 `CONTEXT.md` was updated during grilling (Raster drive section: Amplitude, Offset,
  Linked pair / unlinked pair, Input peak; Known collisions: "Peak"). The developer
  commits it with this spec; no ticket edits `CONTEXT.md`. No ADR.

## Testing notes

- Model behaviour (D7–D11, `send_plan`) is tested on `FuncGenSetpoints` directly, with no
  widget.
- `over_limit_channels` and the planner's line and enable function are tested with plain
  values.
- Existing assertions that encode peak volts are rewritten in place under their current
  names, not deleted. Known one: `tests/test_overview_tab.py` around line 221 (`"0.75"`,
  "1.5 Vpp = 0.75 V pk"), which becomes a Vpp assertion.

## Out of scope

Saving setpoints or link state between runs; independent amplitudes or frequencies per
plate on the Overview; any change to `Beamline`'s interlock, the HV pressure ladder, or the
warn threshold; the HV Amplifiers tab; the Overview amplifier bars; the Raster Planner's
physics and kV lines; the FG tab's Mirror button (it still copies amplitude and frequency
only); applying to hardware from the Raster Planner.
