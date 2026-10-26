# 75: Draw the ZWO region of interest on the Camera tab's video view

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 74

**Spec:** `.scratch/zwo-capture/spec.md`, section D11 ("ROI drawing")
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md`,
`docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/widgets/video_view.py` — module docstring, `set_frame`,
`painted_rect`, `paintEvent`, `mouseDoubleClickEvent`; `src/rbl/gui/camera_tab.py` (after 74);
`src/rbl/hardware/capture_protocol.py` `snap_roi`; the `preview` message fields in spec D2
(`width`, `height`, `roi`).

## What to build

1. Pure function `view_rect_to_sensor_roi(view_rect: tuple[int, int, int, int], painted:
   tuple[int, int, int, int], preview_size: tuple[int, int], roi: dict) -> tuple[int, int, int,
   int]` in `src/rbl/hardware/preview_stretch.py` (pure, no Qt; tuples are `(x, y, width,
   height)`): intersect `view_rect` with `painted` (the on-screen rectangle the image occupies),
   scale from painted pixels to preview pixels, then from preview pixels to ROI pixels
   (`roi["width"] / preview_width`, `roi["height"] / preview_height`), then add `roi["x"]`,
   `roi["y"]`. Returns sensor coordinates, unsnapped (the Capture Process snaps). A rectangle
   entirely outside `painted` returns `(roi["x"], roi["y"], 0, 0)`.
2. `VideoView`: `set_rect_drawing(on: bool)` and a signal `rect_drawn = Signal(int, int, int,
   int)` (view coordinates). While on, a left-drag shows a `QRubberBand` and release emits
   `rect_drawn` for a drag of at least 4 × 4 pixels. Off by default; double-click to fullscreen
   keeps working when off.
3. `CameraTab`: drawing is on exactly when the kind is `"zwo"`, `zwo_open` is True and
   `zwo_recording` is False (re-evaluated on render). It remembers the last `zwo_preview`
   message's `width`, `height` and `roi`. On `rect_drawn`, it computes
   `view_rect_to_sensor_roi(...)` with `self._feed.painted_rect()` and calls
   `recorder.set_zwo_settings(roi={...})` when the result has width > 0 and height > 0. The
   Overview panel gets no drawing (numeric fields only).
4. Short note in `CameraTab`'s module docstring: draw over **Full sensor** to choose an ROI;
   the boxes then show the snapped result.

## Acceptance criteria

- [ ] `test_view_rect_to_sensor_roi` (in `tests/test_preview_stretch.py`) (pure, parametrized,
  literal expected tuples): full painted area at preview 960 × 540 of a full-sensor ROI
  1920 × 1080 maps a view rect equal to `painted` to `(0, 0, 1920, 1080)`; a painted rect
  offset by `(100, 50)` (letterboxing) with view rect `(100, 50, 480, 270)` maps to
  `(0, 0, 960, 540)` sensor at scale 2 when painted is 960 × 540; with ROI origin `(200, 100)`
  the result is shifted by it; a rect half outside `painted` is clipped; one fully outside →
  width and height 0.
- [ ] `test_drawing_a_rect_sets_the_roi` (in `tests/test_camera_tab.py`) (real recorder + fake
  client, connected, not recording, Full sensor applied, tab shown): emit
  `tab.video_view.rect_drawn` — `video_view` is a new **public** read-only property returning
  the feed — with the top-left quarter of `tab.video_view.painted_rect()`; once the reply
  arrives, `state()["zwo_settings"]["roi"]` equals `snap_roi(*view_rect_to_sensor_roi(<the
  same inputs>), 1920, 1080)` (the mapping itself is proved by the pure test above) and its
  width is between 900 and 960.
- [ ] `test_drawing_is_off_while_recording` (in `tests/test_camera_tab.py`): during a ZWO run,
  `video_view.rect_drawing_enabled()` (add this public getter) is False; after `stop_video` it
  is True; under `"usb"` it is False.
- [ ] `test_rect_drawn_on_drag` (in `tests/test_video_view.py`, new or extended): with drawing on, a simulated press-move-
  release (`QTest.mousePress/mouseMove/mouseRelease`) of 40 × 30 pixels emits `rect_drawn`
  once with those values; with drawing off it emits nothing; a 2 × 2 drag emits nothing.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile

`preview_stretch.py` is under a hard type-gate prefix. Soft count must not rise above
`tools/mypy_ratchet.txt`; `tools/private_access_ratchet.txt` must not rise.

## Out of scope

- ROI drawing on the Overview panel.
- Drawing anything into recorded data or photos (the rubber band is screen-only).

## Comments
