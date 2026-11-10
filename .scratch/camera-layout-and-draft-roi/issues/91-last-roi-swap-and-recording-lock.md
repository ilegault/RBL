# 91: Last ROI: Full sensor ⇄ Back to ROI, and no ROI change during a ZWO recording

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 89

**Spec:** `.scratch/camera-layout-and-draft-roi/spec.md` (Solution 4-5)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decisions 1 and 6: the application only *asks* the Capture Process; the confirmed reply is the truth), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/config/zwo_settings_store.py` (`ZwoSettings`, `_check_roi`, `_RULES`, `validate_settings`, `save_settings`); `src/rbl/services/session_recorder.py` (`set_zwo_settings`, `take_darks` for the refuse-and-warn pattern, `state`, `_on_zwo_opened`, and the `mtype == "ok"` branch that handles a reply in `_pending_set_ids`); `src/rbl/gui/widgets/zwo_controls.py` (`_on_full_sensor_clicked`, `_render`); `tests/test_zwo_settings_store.py`, `tests/test_session_recorder_zwo.py`, `tests/test_zwo_controls.py` (`recorder_and_widget`). `tests/conftest.py::_never_touch_the_real_zwo_settings_store` already points the store at a temp file.

## What to build

**Last ROI** = the most recent ROI the Capture Process *confirmed* that is not the full
sensor. Full sensor means `x == 0`, `y == 0`, `width == zwo_info["sensor_width"]`,
`height == zwo_info["sensor_height"]`.

1. **Store.** `ZwoSettings` gains `last_roi: tuple[int, int, int, int] | None = None`,
   validated by the existing `_check_roi` (add `("last_roi", _check_roi)` to `_RULES`),
   converted to a tuple on load exactly as `roi` is, and written by `save_settings` as an
   `{"x", "y", "width", "height"}` dict exactly as `roi` is.
2. **Recorder learns it.** In the `ok` reply branch that updates `self._zwo_settings`, when
   the confirmed ROI is not the full sensor, also set `last_roi` to it (before the existing
   `save_settings` call). A full-sensor reply leaves `last_roi` unchanged. `state()` gains
   `"zwo_last_roi"`: the dict form, or `None`.
3. **`SessionRecorder.swap_zwo_roi() -> int`.** At the full sensor with a Last ROI: apply
   the Last ROI. At any other ROI: apply the full sensor. At the full sensor with no Last
   ROI: return -1 and send nothing. Goes through `set_zwo_settings`, so the lock below applies.
4. **Recording lock.** `set_zwo_settings` refuses any call whose fields include `roi` while
   `self._zwo_video_active or self._zwo_starting_video`: emit
   `status("Cannot change the ROI while video is recording", "warn")`, return -1, send
   nothing to the Capture Process. Other fields (exposure, gain, ...) are unaffected.
5. **`ZwoControls`.** `btn_full_sensor` (keep the attribute name) calls
   `recorder.swap_zwo_roi()`. `_render` sets its text to `"Back to ROI"` at the full sensor
   and `"Full sensor"` otherwise; it is enabled only when the camera is open, no ZWO video is
   recording (`state()["zwo_recording"]`), and, at the full sensor, `zwo_last_roi` is not
   `None`. The four ROI spin boxes are disabled while `zwo_recording`. Delete
   `_on_full_sensor_clicked` only if nothing else uses it.

## Acceptance criteria

Real things: the store's load/save on a temp file, the fake Capture Process
(`CaptureClient().start("fake")`, sensor 1920 × 1080) and its snapped replies. Nothing else
is faked.

- [ ] `tests/test_zwo_settings_store.py::test_last_roi_round_trips`: save
  `ZwoSettings(last_roi=(64, 32, 640, 480))`, load: same tuple, no warnings; the JSON on disk
  holds `"last_roi": {"x": 64, "y": 32, "width": 640, "height": 480}`. And
  `test_bad_last_roi_falls_back`: `{"last_roi": "nope"}` loads `last_roi=None` with exactly
  one `SettingsLoadWarning` whose key is `"last_roi"`.
- [ ] `tests/test_session_recorder_zwo.py::test_last_roi_follows_confirmed_rois`: open the
  fake camera, apply ROI `(64, 32, 640, 480)`, wait for the reply: `state()["zwo_last_roi"]`
  equals the *confirmed* (snapped) ROI; then apply the full sensor: `zwo_last_roi` is
  unchanged; and a fresh `load_settings()` returns that same `last_roi`.
- [ ] `test_swap_returns_to_the_exact_roi`: after the steps above, `swap_zwo_roi()` twice
  more: the confirmed `zwo_settings["roi"]` is first the earlier ROI exactly, then full
  sensor. With `last_roi` None at the full sensor, `swap_zwo_roi()` returns -1.
- [ ] `test_roi_is_locked_while_recording`: `rec.start()`, `rec.start_video()`, wait for
  `zwo_recording`; `set_zwo_settings(roi=...)` and `swap_zwo_roi()` both return -1, a
  `status` emission with that exact warning text is seen, and after 0.5 s the confirmed ROI
  is unchanged; `set_zwo_settings(gain=...)` still returns a command id ≥ 0.
- [ ] `tests/test_zwo_controls.py::test_roi_button_swaps_and_locks`: button text is
  `"Back to ROI"` and disabled at full sensor with no Last ROI; after an ROI is confirmed it
  reads `"Full sensor"` and is enabled; while ZWO video records, it and the four ROI spin
  boxes are disabled.

## Gate

Run all four, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
