# 89: Session Recorder: repacked rows and a Preview that stops eating the panel

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/camera-layout-and-draft-roi/spec.md` (Solution 1-2, D1, D2, D4, D5)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 7: USB behaviour unchanged; this ticket moves widgets, never changes what they do)

**Read first:** `src/rbl/gui/widgets/recording_panel.py` (`RecordingPanel._build_ui`, `_render`); `src/rbl/gui/widgets/zwo_controls.py` (`ZwoControls._build_ui`); `src/rbl/gui/widgets/camera_kind_mixin.py` (`_make_kind_row`, `_render_kind`); `src/rbl/gui/widgets/video_view.py` (module docstring: why size hints must never depend on the frame); `src/rbl/hardware/preview_stretch.py` (style of a pure helper); `tests/test_recording_panel.py` (`zwo_rig`, `_make_view`), `tests/test_zwo_controls.py` (`recorder_and_widget`).

## What to build

The Overview's Session Recorder packs into fewer, fuller rows under both cameras, and its
Preview is only as tall as the frame needs. Layout only: no handler, signal or enable rule
changes. Keep every existing attribute name (`_btn_open`, `_btn_video`, `_spin_csv`,
`usb_only_widgets`, `zwo_controls`, `btn_full_sensor`, ...).

1. **Pure sizing rule.** `src/rbl/hardware/preview_stretch.py` gains
   `preview_height_for_width(width: int, frame_size: tuple[int, int] | None, max_height_per_width: float) -> int`:
   `round(width * min(frame_h / frame_w, max_height_per_width))`, using
   `max_height_per_width` when `frame_size` is `None` or has a zero side. No Qt. The cap lives
   in `src/rbl/config/recording_config.py` as `PREVIEW_MAX_HEIGHT_PER_WIDTH = 9 / 16` (one
   home per number, AGENTS.md invariant 3).
2. **`VideoView` opt-in.** Add `set_height_follows_width(on: bool)`. When on:
   `hasHeightForWidth()` is True, `heightForWidth(w)` returns
   `preview_height_for_width(w, (frame w, frame h) or None, PREVIEW_MAX_HEIGHT_PER_WIDTH)`,
   the vertical size policy is `Preferred`, and `updateGeometry()` is called when a frame of a
   **different size** than the last one arrives (not every frame). Off by default, so the
   Camera tab, which never calls it, is unchanged. `sizeHint`/`minimumSizeHint` stay constant
   as they are today.
3. **`RecordingPanel` Preview.** Call `self._feed.set_height_follows_width(True)`; add the
   feed with `stretch=0`; end the panel's `QVBoxLayout` with `addStretch(1)` so spare height
   goes below the last row.
4. **`ZwoControls` rows** (three rows plus the status line, each `QHBoxLayout` ending in
   `addStretch(1)` except row 3, whose slider takes the stretch):
   - row 1: `Exposure: [spin_exposure_ms] ms  Gain: [spin_gain]  Bit depth: [combo_bit_depth]  Keep 1 in N: [spin_keep_one_in_n]`
   - row 2: `ROI: [x][y][w][h] [btn_full_sensor] [btn_take_darks]`
   - row 3: `Preview: [combo_preview]  Brightness: [slider_brightness, stretch=1]`
5. **`RecordingPanel` rows**, top to bottom:
   - row 1 = the layout returned by `_make_kind_row()`, with `_combo_cam`, `_btn_open`,
     `_lbl_res` appended to it, then `addStretch(1)`. In `camera_kind_mixin._make_kind_row`,
     drop `stretch=1` from `combo_camera_kind` and keep a reference to the "Camera:" label as
     `self.lbl_camera_kind`. `CameraTab` appends `addStretch(1)` to its kind row.
   - USB rows: `Res / Acquire / Record` (as today, plus trailing stretch);
     `Format: [combo_fmt]  Master: [combo_master]`; `MP4 copy (CRF): [combo_quality]  Segment: [combo_seg]`.
   - `zwo_controls`, the Preview, the caption.
   - `CSV every: [_spin_csv]  [_btn_session (stretch=1, min height 36)]`.
   - `_lbl_status`, `_lbl_stats`.
   - `[_btn_video] [_btn_photo] [_btn_note] [_btn_folder]`.

## Acceptance criteria

Tests may use the fake Capture Process (`CaptureClient().start("fake")`, 1920 × 1080) and the
`_StubRecorder`/`zwo_rig` fixtures already in `tests/test_recording_panel.py`. Layout
assertions use a small test helper `_same_row(*widgets)` that finds the `QHBoxLayout`
holding the first widget (walk `parentWidget().layout()` and its child layouts, use
`indexOf`) and checks every other widget is in that same layout object.

- [ ] `tests/test_preview_stretch.py::test_preview_height_for_width` (parametrized):
  `(800, (1920, 1080), 9/16) -> 450`; `(800, (1000, 1000), 9/16) -> 450` (tall frame is
  capped); `(800, (2000, 500), 9/16) -> 200`; `(800, None, 9/16) -> 450`; `(800, (0, 0), 9/16) -> 450`.
- [ ] `tests/test_video_view.py::test_height_follows_width_is_opt_in`: a fresh `VideoView`
  has `hasHeightForWidth()` False; after `set_height_follows_width(True)` and
  `set_frame(QImage(2000, 500, Format_RGB888))`, `heightForWidth(800) == 200` and
  `minimumSizeHint()` still equals the constant `QSize(64, 48)`.
- [ ] `tests/test_recording_panel.py::test_preview_takes_no_spare_height` (`zwo_rig`,
  `"panel"`): panel resized to 800 × 1600 and shown, after `qapp.processEvents()` the feed's
  height equals `preview_height_for_width(feed.width(), None, 9/16)` within 2 px, i.e. the
  Preview did not grow to fill 1600 px.
- [ ] `tests/test_recording_panel.py::test_rows_are_packed` (`zwo_rig`, `"panel"`):
  `_same_row(combo_camera_kind, _btn_open)`; `_same_row(_spin_csv, _btn_session)`;
  `_same_row(_btn_video, _btn_photo, _btn_note, _btn_folder)`;
  `_same_row(_combo_fmt, _combo_master)`; `_same_row(_combo_quality, _combo_seg)`.
- [ ] `tests/test_zwo_controls.py::test_rows_are_packed`: `_same_row(spin_exposure_ms, spin_gain, combo_bit_depth, spin_keep_one_in_n)`;
  `_same_row(spin_roi_x, btn_full_sensor, btn_take_darks)`; `_same_row(combo_preview, slider_brightness)`.
- [ ] Every existing test in `tests/test_recording_panel.py`, `tests/test_zwo_controls.py`
  and `tests/test_camera_tab.py` passes unmodified.

## Gate

Run all four, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
