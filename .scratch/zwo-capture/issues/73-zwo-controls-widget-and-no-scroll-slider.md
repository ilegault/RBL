# 73: One ZWO settings widget (`ZwoControls`) and `NoScrollSlider`

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 71

**Spec:** `.scratch/zwo-capture/spec.md`, section D11 (the ZWO rows, the status line, Take
Darks, "Values shown come from the `set` reply"), D8 (the Preview caption)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 6: stretch and
brightness exist only in the Preview), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/widgets/inputs.py` (`NoScrollComboBox.wheelEvent` and its
comment, `NoScrollSpinBox`, `QuietDoubleSpinBox`); `tests/test_no_scroll_inputs.py`;
`src/rbl/gui/widgets/recording_panel.py` module docstring (the panels render the recorder's
state and keep no copy of a setting); `src/rbl/services/session_recorder.py` (ticket 71:
`state()` keys `zwo_open`, `zwo_recording`, `zwo_status`, `zwo_info`, `zwo_settings`,
`set_zwo_settings`, `set_zwo_preview`, `take_darks`); `src/rbl/gui/theme.py` roles.

## What to build

The ZWO rows are built once, here, and both panels embed this widget (ticket 74). Decided:
the Overview panel and the Camera tab show identical ZWO rows, so one widget, tested once.

### `src/rbl/gui/widgets/inputs.py`

1. `class NoScrollSlider(QSlider)` whose `wheelEvent` calls `event.ignore()`, with a docstring
   in the style of `NoScrollComboBox`'s (why: the brightness slider sits in a scrolling tab).

### `src/rbl/config/capture_config.py`

2. Add `CAPTURE_DARK_FRAMES_DEFAULT = 20` (frames averaged per Take Darks; no field for it in
   the UI).

### `src/rbl/gui/widgets/zwo_controls.py` (new)

3. Module docstring with `WHY THIS EXISTS`. Module-level pure function
   `preview_caption(mode: str) -> str`: `"Display stretch: not intensity"` for `"auto"`,
   `"Linear display"` for `"linear"`.
4. `class ZwoControls(QWidget)`, constructed with the `SessionRecorder`. **Public attributes**
   (tests use them; no test may reach a `_`-prefixed name): `spin_exposure_ms`
   (`QuietDoubleSpinBox`, ms, 3 decimals), `spin_gain` (`NoScrollSpinBox`), `combo_bit_depth`
   (`NoScrollComboBox`, items `"RAW16 (default)"` with data 16 and `"RAW8"` with data 8),
   `spin_roi_x`, `spin_roi_y`, `spin_roi_width`, `spin_roi_height` (`NoScrollSpinBox`),
   `btn_full_sensor`, `spin_keep_one_in_n` (`NoScrollSpinBox`, 1..`CAPTURE_KEEP_ONE_IN_N_MAX`),
   `combo_preview` (`NoScrollComboBox`, `"Auto stretch"` → `"auto"`, `"Linear"` → `"linear"`),
   `slider_brightness` (`NoScrollSlider`, horizontal, integer value = brightness × 100, range
   `PREVIEW_BRIGHTNESS_MIN * 100`..`PREVIEW_BRIGHTNESS_MAX * 100`), `btn_take_darks`,
   `lbl_status`.
5. Writes: exposure on `editingFinished` → `set_zwo_settings(exposure_us=round(ms * 1000))`;
   gain, bit depth, keep 1 in N on value change → `set_zwo_settings(<field>=...)`; any ROI box
   on `editingFinished` → `set_zwo_settings(roi={x, y, width, height})` from all four boxes;
   **Full sensor** → `roi` = `(0, 0, sensor_width, sensor_height)` from `state()["zwo_info"]`;
   preview combo and slider → `set_zwo_preview(mode, value / 100)`.
6. Reads: re-render on the recorder's `state_changed` and `settings_changed`, with signals
   blocked while setting values. **Every displayed value comes from
   `state()["zwo_settings"]`** (the snapped values the Capture Process applied), never from
   what was typed. Ranges: exposure and gain from `state()["zwo_info"]["controls"]`
   (`Exposure` min/max in µs shown in ms); ROI boxes 0..sensor size. Every input is disabled
   while `zwo_open` is False.
7. `lbl_status` from `state()["zwo_status"]`:
   `f"{acquired_fps:.1f} fps · {frames_written} written · {dropped} dropped ·
   {sensor_temperature_c:.1f} °C · ≈{bytes_per_hour / 1e9:.1f} GB/h ·
   {disk_free_bytes / 1024**3:.0f} GB free"` with `dropped = frames_dropped_camera +
   frames_dropped_queue`; empty when there is no status.
8. Take Darks: enabled only when `zwo_open` and not `zwo_recording`. Clicking calls the public
   method `confirm_darks() -> bool` (a `QMessageBox.question` with text
   `"Cap the lens before taking Dark Frames."`); only on True does it call
   `recorder.take_darks(CAPTURE_DARK_FRAMES_DEFAULT)`. Add a `tests/conftest.py` autouse
   fixture that patches `ZwoControls.confirm_darks` to return False, as conftest already does
   for the Faraday Cup tab's modals, so no test can hang on a real dialog.

## Acceptance criteria

`tests/test_zwo_controls.py` (new) unless noted. Real `SessionRecorder` + real `CaptureClient`
on `start("fake")` (as in ticket 71's tests), `connect_zwo()`, waits spinning `processEvents`.

- [ ] `test_no_scroll_slider_ignores_the_wheel` (in `tests/test_no_scroll_inputs.py`, extend): a `NoScrollSlider` at value 100 receiving a
  `QWheelEvent` keeps value 100, emits no `valueChanged`, and leaves the event not accepted
  (same three checks as the existing `NoScrollSpinBox` class there).
- [ ] `test_inputs_disabled_until_open_then_ranged_from_controls`: before `opened` every input
  in step 4 is disabled; after it they are enabled, `spin_gain.maximum()` equals the fake
  camera's `Gain` max from `zwo_info["controls"]`, and `spin_roi_width.maximum() == 1920`.
- [ ] `test_values_shown_come_from_the_reply`: set `spin_roi_x` to 1 and `spin_roi_width` to
  101 and emit `editingFinished`; once the reply arrives the boxes show 0 and 96; set
  `spin_exposure_ms` to 5.0 and emit `editingFinished`; `state()["zwo_settings"]["exposure_us"]
  == 5000` and the box shows 5.000. `btn_full_sensor.click()` → boxes show 0, 0, 1920, 1080.
- [ ] `test_preview_changes_never_touch_the_recording`: during a session and a ZWO video run
  at ROI 64 × 32, change `combo_preview` to Linear and `slider_brightness` to 300, record 1 s
  more, stop: exactly one `segment_*.ser` exists and its settings record equals the one
  written before the changes, field for field, except `frames`, `closed_utc`,
  `closed_reason`, `sensor_temperature_c_close` and the drop counts.
- [ ] `test_take_darks_asks_first`: with `confirm_darks` monkeypatched on the instance to
  return False, clicking `btn_take_darks` writes nothing under `paths.ZWO_DARKS_DIR` (patched
  into `tmp_path`); returning True, a `dark_*.fits` appears under `<darks>/FAKE0001/` within
  30 s. During a ZWO run the button is disabled.
- [ ] `test_status_line_and_caption`: after a `status` arrives, `lbl_status.text()` contains
  `"fps"`, `"written"`, `"°C"`, `"GB/h"` and `"GB free"`; `preview_caption("auto") ==
  "Display stretch: not intensity"` and `preview_caption("linear") == "Linear display"`.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile

Soft type-gate count must not rise above `tools/mypy_ratchet.txt`;
`tools/private_access_ratchet.txt` must not rise; `scripts/check_layers.py` and
`tests/test_layering.py` pass (the widget never converts a value itself; it renders `state()`).

## Out of scope

- Putting the widget into the panels, the camera picker, the Preview display (ticket 74).
- ROI drawing (75). A frame-count field for Dark Frames. The app-wide scroll-wheel fix for
  existing widgets.

## Comments
