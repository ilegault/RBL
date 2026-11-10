# 111: Raster Planner "Send to drive" and generator line

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 109

**Spec:** `.scratch/axis-offset-and-vpp/spec.md` (decisions D13–D15)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

## What to build

The Raster Planner (`src/rbl/gui/raster_planner_tab.py`) can put its plan into the shared
setpoints with one press, so nothing is retyped. **Send to drive** writes each axis's
amplitude, **offset** (as a **linked pair**) and frequency through
`FuncGenSetpoints.send_plan` (ticket 109). It never commands a generator; Apply on the
Overview or FG tab still does that. The planner's generator line uses the same wording as
the inputs elsewhere: Vpp, offset, input peak.

## Acceptance criteria

- [ ] A pure function (no Qt), e.g. `drive_summary(sol_axis: dict, minus_label: str) -> str`
      in a non-GUI module, returns the generator line:
      `4.000 Vpp, offset +0.400 V (X− −0.400 V) · input peak 2.40 V`. With a zero offset
      it returns `4.000 Vpp, offset 0 V · input peak 2.00 V`, and with input peak above
      5 V it appends ` — over the 5 V limit`. The values come from `gen_amp_vpp`,
      `gen_offset_v` and `gen_peak_v`. Unit tests use plain dicts. `_show_drive` puts this
      text in `lbl_gen_x` / `lbl_gen_y` and keeps the `theme.FAULT` style above 5 V. The
      `lbl_plate_x/y` kV lines are unchanged. Test: tests/test_raster_planner_tab.py::`test_drive_summary_line`.
- [ ] A pure function returns `(enabled: bool, reason: str)` for Send to drive. It is False
      with a reason when there is no Beamline, no solution, any of `gen_amp_vpp` or
      `gen_offset_v` or frequency is not finite, or either axis's `gen_peak_v` is above
      `PEAK_MAX_VOLTS`. Unit tests cover each case. Test: tests/test_raster_planner_tab.py::`test_send_to_drive_enable_rule`.
- [ ] `btn_send_drive` ("Send to drive") sits directly under `lbl_gen_y`. Its enabled state
      and tooltip come from that function. With a real `Beamline` (as `tests/test_raster_planner_tab.py`
      builds one), one click leaves A1/A2 and B1/B2 in `beamline.funcgen_setpoints` at the
      plan's Vpp, ±offset, `sb_freq_x_hz`/`sb_freq_y_hz` values and `shape == "Triangle"`,
      with both axes linked. Test: tests/test_raster_planner_tab.py::`test_send_to_drive_writes_the_setpoints`.
- [ ] Clicking it issues no hardware command. A test asserts no `Beamline` method in the
      `set_channel` / `apply_all_channels` / `move_slit` family was called (spy those
      methods). `output_on` is unchanged on all four channels. Test: tests/test_raster_planner_tab.py::`test_send_to_drive_commands_no_hardware`.
- [ ] After a send, a label under the button reads
      `Sent X: 4.000 Vpp, +0.400 V @ 517 Hz · Y: <same format> — press Apply on the Overview`. Test: tests/test_raster_planner_tab.py::`test_send_to_drive_status_line`.

Tests may spy on Beamline command methods. The solver output may come from the real
planner with a test geometry or a hand-built `sol` dict. `FuncGenSetpoints` must be real.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --durations=25
```

## Comments
