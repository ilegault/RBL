# 110: Overview Offset box and Link ±

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 107, 109

**Spec:** `.scratch/axis-offset-and-vpp/spec.md` (decisions D3 offset part, D4 per-channel peak, D12)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

## What to build

Each Overview axis panel (`AxisDriveControl`, `src/rbl/gui/widgets/axis_drive.py`, wired
in `OverviewTab`, `src/rbl/gui/overview_tab.py`) gets an **offset**. A **Link ±** checkbox,
ticked by default, shows one `Offset:` box when linked: the plus plate gets the value and
the minus plate its negative. Unticked, it shows `X+ offset:` and `X− offset:` boxes (the
Y panel uses its own labels), each writing only its own channel. Link state comes from
`FuncGenSetpoints.axis_linked` (ticket 109). When an FG tab per-channel offset edit
unlinks the pair, the panel flips to two boxes by itself.

## Acceptance criteria

- [ ] New `AxisDriveControl` signals `offset_edited(str, float)`,
      `channel_offset_edited(str, float)` and `link_toggled(str, bool)`. `OverviewTab`
      wires them to `set_axis_offset`, `update(key, offset_v=...)` and `set_linked`.
      Model to widget goes through `sync_value` under the existing `_syncing` guard,
      copying how `set_setpoint` writes amplitude. A test types `0.4` into the linked X box
      and asserts A1 == 0.4 and A2 == −0.4 in `beamline.funcgen_setpoints`. Test: tests/test_overview_tab.py::`test_linked_offset_box_writes_both_plates`.
- [ ] Unticking Link ± on X shows two boxes and hides the single box (`isVisible()` checks
      after `tab.show()`). Typing −0.2 into the X− box changes only A2. Calling
      `beamline.funcgen_setpoints.update("A1", offset_v=0.7)` on a linked X (the FG tab's
      path) unticks the checkbox and shows 0.7 in the X+ box. Test: tests/test_overview_tab.py::`test_unlinking_shows_two_offset_boxes`.
- [ ] All offset boxes are `QuietDoubleSpinBox` (`src/rbl/gui/widgets/inputs.py`) with
      range `±MAX_GEN_VOLTS`, 4 decimals and unit label `V`. Setting the offset to 4.0 with
      amplitude at 4 Vpp is accepted by the box (no clamp), and `lbl_hv` turns `theme.FAULT`. Test: tests/test_overview_tab.py::`test_offset_box_does_not_clamp_against_amplitude`.
- [ ] `lbl_hv` shows the larger of the axis's two channels' input peaks, using each
      channel's real shape and offset from the setpoints. A test asserts
      `input peak 2.50 V` for 4 Vpp with A1 at +0.5 and A2 at −0.3 (unlinked). Test: tests/test_overview_tab.py::`test_axis_input_peak_uses_the_larger_channel`.
- [ ] The readback line shows signed offsets:
      `X+ 4.00 Vpp +0.400 V · X− 4.00 Vpp −0.400 V @ 517 Hz out=ON`, built in
      `AxisDriveControl.set_readback` from `ChannelSnapshot.offset_v`. A test feeds the
      snapshot through the Beamline readback path, as existing readback tests in
      `tests/test_overview_tab.py` do. Test: tests/test_overview_tab.py::`test_axis_readback_shows_signed_offsets`.

Tests may fake the generator connection. `FuncGenSetpoints` must be the real one.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --durations=25
```

## Comments
