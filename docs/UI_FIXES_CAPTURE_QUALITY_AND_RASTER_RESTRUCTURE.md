# RBL — Camera crop bug, capture-quality restructure, vacuum font, Raster Planner layout

**Implementation plan for Claude Sonnet**
Target repo: `C:\Users\IGLeg\PycharmProjects\RBL`
Written 2026-08-25. Author of record: Isaac (ilegault004@gmail.com), design discussion by Claude Opus.

---

## 0. Read this first

Four independent items. They do not share code, so they can land in any order,
but **Phase 2 is the one with the physics-of-the-pipeline reasoning in it** and
its Section 2.1 is load-bearing for every change in that phase — read it before
touching `video_recorder.py`.

| Phase | What | Files touched |
|---|---|---|
| 1 | Camera preview crop/ratchet bug | `rbl/gui/widgets/video_view.py` (new), `recording_panel.py`, `camera_tab.py`, `drag_panel.py` (comment only) |
| 2 | Capture-quality restructure (no ffmpeg.exe in the lab) | `recording_config.py`, `video_recorder.py`, `session_recorder.py`, `camera_source.py`, `recording_panel.py`, `camera_tab.py` |
| 3 | Vacuum pressure font-size control | `vacuum_tab.py` |
| 4 | Raster Planner layout + editable species table | `raster_planner_tab.py`, `beam_species.py`, `raster_defaults.py` (new) |

### Style constraints for this repo (unchanged, restated)

- Every module docstring explains **why it exists**, and records the failure the
  design prevents. Continue that. Phase 1 and Phase 2 both have a "the bug this
  prevents" story worth writing down verbatim.
- Pure-math modules take arrays and return floats — no Qt, no config lookups.
- GUI tabs own widgets only; services own procedures; drivers stay thin.
- Every new module gets an `if __name__ == "__main__":` self-test block.
- Every new feature gets or extends a `tests/test_*.py`.
- Do not break existing tests. The ones most at risk here are
  `tests/test_recording_panel.py`, `tests/test_camera_tab.py`,
  `tests/test_video_recorder.py`, `tests/test_session_recorder.py`,
  `tests/test_raster_planner_tab.py`. Run the full suite before and after.

---

## Phase 1 — The camera preview crop / ratchet bug

### 1.1 Symptom (operator's words)

> Drag the camera widget around in the Overview and the actual camera video gets
> cropped based on where you put the frame. Drag it back to default and the
> camera stays where you cropped it.

### 1.2 Root cause

`RecordingPanel._on_preview_frame()` (`rbl/gui/widgets/recording_panel.py`)
does this on every preview frame:

```python
pix = QPixmap.fromImage(img).scaled(
    self._lbl_feed.width(), self._lbl_feed.height(),
    Qt.AspectRatioMode.KeepAspectRatio,
    Qt.TransformationMode.SmoothTransformation)
self._lbl_feed.setPixmap(pix)
```

Two Qt facts collide:

1. **A `QLabel` holding a pixmap reports that pixmap's size as its
   `minimumSizeHint()`.** Not `sizeHint()` — `minimumSizeHint()`. A layout is
   not allowed to shrink it below that.
2. **`DragPanel` wraps every Overview panel in a `QScrollArea` with
   `setWidgetResizable(True)`** (`rbl/gui/widgets/drag_panel.py`, `DragPanel.__init__`).
   That scroll area sizes its content widget to the viewport **or the content's
   minimum size hint, whichever is larger**, and shows scrollbars for the rest.

So the code has a positive feedback loop with a one-way ratchet:

```
label is 900x600  →  pixmap scaled to 900x600  →  setPixmap
                  →  label.minimumSizeHint() becomes 900x600
                  →  the Session Recorder group box's minimum grows to match
                  →  the DragPanel scroll area's content is now >= 900 px wide
```

Now drag the panel narrower. The **viewport** shrinks; the **content** cannot,
because its minimum is latched at the largest size the label ever reached. The
scroll area starts clipping — you are looking at a window onto a widget that is
bigger than the hole it is shown through. That clipping is the "cropping." It
follows the drag because the scroll offset does.

Drag back out and nothing recovers, because the latch only ever moves up: a
bigger viewport makes a bigger label makes a bigger pixmap makes a bigger
minimum. The layout has no path back down. That is the "stays where you cropped
it" half of the report.

Note what is **not** happening: no frame data is cropped, the recorded video is
untouched, and the camera is fine. This is purely a layout ratchet in the
preview widget. Say so in the commit message — it matters that the archived
footage was never affected.

`camera_tab.py`'s `_FeedLabel` has the same `setPixmap`-drives-minimum property.
It gets away with it today only because `CameraTab` calls
`self._feed.setFixedSize(self._scroll.viewport().size())` in fit mode, which
pins the size from the viewport side. That is a workaround holding a bug down,
not an absence of the bug — replace it too.

### 1.3 The fix — a `VideoView` widget whose size never depends on its content

Create `rbl/gui/widgets/video_view.py`. The rule it must enforce: **the frame is
painted into whatever rectangle the layout gives the widget, and the widget
never asks the layout for a rectangle based on the frame.**

```python
"""
video_view.py
A camera-frame display whose size hint does NOT depend on the frame.

WHY THIS EXISTS
---------------
The Overview's camera preview used a QLabel and called setPixmap() with a
pixmap scaled to the label's current size.  A QLabel holding a pixmap reports
that pixmap's size as its minimumSizeHint(), and every Overview panel lives
inside a widgetResizable QScrollArea (drag_panel.py).  So each frame ratcheted
the panel's minimum size UP to the largest size the preview had ever reached,
and the scroll area then clipped the panel whenever it was dragged smaller —
which looked, correctly, like the camera video being cropped by the drag, and
never recovered because the ratchet only turns one way.

This widget breaks the loop: sizeHint and minimumSizeHint are constants, the
frame is scaled at PAINT time into whatever rectangle the layout hands over,
and the widget therefore exerts no pressure on the layout at all.
"""
```

API:

| Member | Behaviour |
|---|---|
| `set_frame(img: QImage)` | Store **a deep copy** and `update()`. See the `.copy()` warning below. |
| `clear(text="No camera")` | Drop the frame, show placeholder text. |
| `set_crosshair(on: bool)` | Centre cross + rule-of-thirds guides, drawn over the frame, never burned in. Lift the existing painter code out of `camera_tab._FeedLabel.paintEvent` unchanged. |
| `set_scale_mode(mode)` | `"fit"` (default) scales to fit, `"one_to_one"` paints at native pixels. Only `CameraTab` uses `"one_to_one"`. |
| `sizeHint()` | `QSize(320, 240)` in fit mode. In `one_to_one` mode, the frame size — that mode is *supposed* to drive a scroll area. |
| `minimumSizeHint()` | `QSize(64, 48)` in fit mode. Constant. This is the line that fixes the bug. |
| `paintEvent` | Fill `#111`, then if a frame exists scale it `KeepAspectRatio` + `SmoothTransformation` into `self.rect()` and centre it; else draw the placeholder text centred in `#888`. Then the crosshair overlay. |

**`.copy()` is not optional.** The current code does
`QImage(rgb.data, w, h, ch * w, Format_RGB888)`, which *borrows* the numpy
buffer without owning it. Today `QPixmap.fromImage(...)` copies immediately on
the same line, so the borrow never outlives the frame. `VideoView` stores the
image until the next paint, so it must call `.copy()` — otherwise you get
torn frames or a hard crash once the ndarray is collected. Write that as a
comment on the line.

### 1.4 Wiring it in

**`recording_panel.py`**

- Replace `self._lbl_feed = QLabel("No camera")` and its four setup lines with
  `self._feed = VideoView()`, `Expanding/Expanding`, `lay.addWidget(self._feed, stretch=1)`.
  Do **not** call `setMinimumSize(240, 180)` — 240x180 is already 4x the new
  minimum hint and there is no reason for this panel to refuse to go smaller.
- `_on_preview_frame` becomes: build the `QImage` from the BGR frame, call
  `self._feed.set_frame(img)`. No `.scaled()`, no size query. Keep the
  `if not self.isVisible(): return` guard and the `try/except`.
- `_on_camera_closed_ui`: `self._feed.clear("No camera")`.

**`camera_tab.py`**

- `_FeedLabel` is deleted; `_FullscreenWindow._lbl` and `CameraTab._feed` both
  become `VideoView`.
- Fit vs 1:1 stops being a `setFixedSize` dance:
  - fit → `self._feed.set_scale_mode("fit")` and `self._scroll.setWidgetResizable(True)`
  - 1:1 → `self._feed.set_scale_mode("one_to_one")` and `self._scroll.setWidgetResizable(False)`
- Delete `CameraTab.resizeEvent` entirely — with `widgetResizable(True)` the
  scroll area does that job, and correctly.
- `_on_preview_frame` becomes symmetric with the panel's: build the QImage once,
  `self._feed.set_frame(img)`, and forward the same image to
  `self._fullscreen_win.set_frame(img)` if it exists.
- `_FullscreenWindow.update_frame(pix)` → `set_frame(img)`; drop its manual
  `.scaled()`, `VideoView` does it.
- `mouseDoubleClickEvent` currently calls `self.parent()._toggle_fullscreen()`,
  which is a fragile reach through the parent. Give `VideoView` a
  `double_clicked = Signal()` and have `CameraTab` connect it to
  `self._toggle_fullscreen`.

**`drag_panel.py`** — no code change, but add a paragraph to `DragPanel`'s
docstring: *any content widget whose `minimumSizeHint()` grows with the data it
displays will ratchet this scroll area open and then be clipped by it. Keep
content minimums constant.* That note is the reason the next person does not
reintroduce this.

### 1.5 Tests — `tests/test_recording_panel.py`, `tests/test_camera_tab.py`

1. `test_preview_does_not_grow_minimum_size`: build the panel, record
   `panel._feed.minimumSizeHint()`, push ten 1920x1080 synthetic frames through
   `_on_preview_frame`, assert the hint is unchanged.
2. `test_panel_can_shrink_after_frames`: put the panel in a `QScrollArea` with
   `widgetResizable(True)` (i.e. reproduce `DragPanel`), resize to 900x700,
   push frames, resize to 300x250, assert
   `panel.width() <= 300` — under the old code the content stayed at 900.
3. `test_aspect_ratio_preserved`: 16:9 frame into a 100x400 view, grab the
   widget, assert the painted image region is 16:9 (or simply assert
   `VideoView._painted_rect()` if you expose a small helper for the test).
4. `test_frame_survives_source_buffer_release`: `set_frame` from an ndarray, del
   the ndarray, force `gc.collect()`, repaint. Fails without `.copy()`.

---

## Phase 2 — Capture quality, restructured for a machine with no ffmpeg

### 2.1 What the compression system actually is today

You asked for the explanation, so here it is before the change list. There are
**three** places a frame can lose information between the sensor and the file,
and the one the UI lets you control is the one that never runs in the lab.

```
 [1] USB camera                [2] cv2.VideoWriter            [3] ffmpeg.exe
 encodes MJPEG in-camera  ───►  decodes to BGR, re-encodes ───► decodes AVI,
 (camera_source.py:78)          JPEG into an AVI              re-encodes H.264
                                (video_recorder.py:_open_segment)  (video_transcoder.py)
     LOSSY                          LOSSY                          NEVER RUNS
```

**[1] The camera stream is already JPEG.** `camera_source._CameraThread.run()`
does `cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))`. That is
there for a good reason — most USB cameras cannot stream HD over USB 2.0 in any
uncompressed format — but it means the very first array the app ever sees has
already been through a JPEG encoder at whatever quality the camera's firmware
chose. Nothing downstream can recover that.

**[2] The AVI writer re-encodes to JPEG a second time.**

```python
fourcc = cv2.VideoWriter_fourcc(*"MJPG")
writer = cv2.VideoWriter(path, fourcc, self._record_fps, (self._width, self._height))
```

No backend argument and no quality property. OpenCV picks its default backend
(the bundled FFmpeg library) and its default JPEG quality, and the code never
says what that is. Measured on a 640x480 synthetic test frame with fine detail:

| writer | file size | max per-pixel error | RMS error |
|---|---|---|---|
| current code (default backend, MJPG, no quality set) | 1635 KiB | 175 | 23.5 |
| `CAP_OPENCV_MJPEG` backend, default quality | 2671 KiB | 143 | 20.0 |
| `CAP_OPENCV_MJPEG` backend, quality 100 | 3003 KiB | 140 | 19.9 |
| `FFV1` (default backend) | 6698 KiB | **0** | **0.00** |

The current path is the *worst* of the four. That is the generation-loss you are
seeing, and it is happening on every single recorded frame.

(That table is a deliberately hostile test image — one channel is pure noise, so
the residual errors at high quality are dominated by MJPEG's chroma
subsampling of noise. A beam image is smooth and will show much smaller numbers.
The *ordering* is what matters and the ordering is stable.)

**[3] The CRF setting does nothing on the lab machines.** `rbl.spec` lines 63-87
deliberately do not bundle `ffmpeg.exe` — an unsigned 100 MB static ffmpeg
inside an unsigned PyInstaller build got the app quarantined by the lab's
antivirus. So `find_ffmpeg()` returns `None`, `session_recorder._start_video()`
takes the `else` branch, writes a `transcode_disabled` event, and sets
`self._transcode_queue = None`. **The Quality combo box — "Archive (CRF 12)" /
"Standard (CRF 18)" / "Small (CRF 23)" — is wired only to `TranscodeQueue`.**
With no ffmpeg there is no TranscodeQueue, so choosing CRF 12 changes nothing at
all. Your instinct that "the CRF 18 might be at play" was reasonable and is
simply not what is happening: on a lab machine that control is inert UI.

**And the "acquisition frame rate" control you were looking for already exists
under the wrong name.** Both panels do:

```python
fps = self._spin_preview.value()      # labelled "Preview:"
self._camera.open(idx, w, h, fps)     # → cap.set(cv2.CAP_PROP_FPS, fps)
```

The box labelled **Preview** is what the camera is actually asked to acquire at.
The box labelled **Record** (`record_fps`) only *decimates* that stream — it
picks which already-acquired frames get written. So "lower the acquisition frame
rate to buy quality" is the right instinct and it is done with the wrong box
today. And it buys something concrete, just not what you guessed: a lower
requested rate is what lets you ask the camera for an **uncompressed** pixel
format at high resolution, which removes lossy stage [1] entirely.

### 2.2 The restructure

Goal: a chain that is lossless where it can be and explicit everywhere else,
with **no dependency on `ffmpeg.exe`**. Everything below uses only what
`opencv-python` already ships. FFV1 encoding runs through the
`opencv_videoio_ffmpeg` DLL that lives *inside* the opencv wheel — it is a
library already loaded in-process, not an external executable, so it is invisible
to whatever policy blocks `ffmpeg.exe` and adds nothing to the build.

#### 2.2a Master codec becomes an explicit choice — `recording_config.py`

```python
# ---- Master (on-disk capture) codec ---------------------------------------
#
# THIS is the compression that actually happens in the lab.  The MP4 transcode
# below it requires ffmpeg.exe, which rbl.spec deliberately does not bundle
# (antivirus quarantine, see its lines 63-87), so on a lab machine the CRF
# preset is inert and the master codec is the ONLY thing standing between the
# sensor and the archive.
#
# label -> (fourcc, container_ext, jpeg_quality|None, lossless: bool)
MASTER_CODECS = {
    "MJPEG q98 (default)":  ("MJPG", ".avi", 98,   False),
    "MJPEG q100":           ("MJPG", ".avi", 100,  False),
    "FFV1 lossless":        ("FFV1", ".mkv", None, True),
    "PNG sequence (lossless)": ("PNGS", "",  None, True),
}
MASTER_CODEC_DEFAULT = "MJPEG q98 (default)"
```

- **`.mkv` for FFV1, not `.avi`.** AVI's RIFF length field is 32-bit; FFV1 fills
  1.5 GB fast enough that the existing `SEGMENT_MAX_BYTES` guard would be
  rolling segments constantly. Matroska has no such ceiling. Verified: both
  `FFV1/.avi` and `FFV1/.mkv` open successfully through cv2's bundled ffmpeg.
- **`"PNGS"` is a sentinel, not a fourcc** — see 2.2c.

#### 2.2b MJPEG quality must be set through the right backend — `video_recorder.py`

`VideoWriter.set(cv2.VIDEOWRITER_PROP_QUALITY, q)` **returns `False` and does
nothing on the default (FFmpeg) backend.** It only works on OpenCV's own MJPEG
writer, which you must request explicitly. Verified both ways.

```python
def _open_writer_mjpeg(self, path: str, quality: int):
    writer = cv2.VideoWriter(
        path, cv2.CAP_OPENCV_MJPEG, cv2.VideoWriter_fourcc(*"MJPG"),
        self._record_fps, (self._width, self._height))
    if writer.isOpened():
        # MUST be CAP_OPENCV_MJPEG: on the default FFmpeg backend this set()
        # returns False and the writer silently keeps its internal default,
        # which measures worse than every explicit setting we tested.
        writer.set(cv2.VIDEOWRITER_PROP_QUALITY, float(quality))
    return writer
```

Rework `_open_segment()` to dispatch on the configured master codec, and
**verify the writer actually opened**, falling back down the list rather than
failing the session:

```
requested codec → isOpened()?  → use it
                → not opened   → self.error/event "codec_unavailable", fall back
                                  to MJPEG q98 → if that fails, existing error path
```

A codec that is unavailable on a particular machine must degrade to a working
recording with a logged `events.csv` entry, never to a dead session. Follow the
existing "CSV-only is a first-class mode" philosophy in `session_recorder.py`.

#### 2.2c PNG sequence mode — the guaranteed-lossless option

At the record rates this rig actually uses (1-5 fps), a per-frame PNG is the
simplest bit-exact archive there is, needs no codec at all, and every frame is
independently openable — which matters for "the shot we can't afford to lose."

- `VideoRecorder` gains a branch where a "segment" is a **folder**
  `video_000/` holding `frame_000000.png …`, written with
  `cv2.imwrite(path, frame, [cv2.IMWRITE_PNG_COMPRESSION, 1])`
  (level 1, not the default 3 — PNG is lossless at every level; the level only
  trades CPU for size, and this runs on the camera thread where you want the
  CPU back).
- `frames.csv` is unchanged — it already carries `segment` and `segment_frame`,
  which map one-to-one onto the folder and filename.
- `segment_closed` still fires with the folder path. `session.json` records
  `"master_codec": "PNGS"`.
- **Guard it**: if `record_fps > 10`, disable this option in the UI with a
  tooltip saying why. A 30 fps PNG sequence will fill any disk and stall the
  camera thread on `imwrite`.

#### 2.2d Uncompressed acquisition — `camera_source.py`

Add an optional `fourcc: str = "MJPG"` parameter to `CameraSource.open()` and
`_CameraThread`, and set it instead of hard-coding MJPG. Two values matter:
`"MJPG"` (today's behaviour, needed for HD at 30 fps) and `"YUY2"`
(uncompressed 4:2:2 — no in-camera JPEG at all).

The camera decides whether it can honour the request, so the thread must
**report back what it got**, not what it asked for:

```python
actual = int(cap.get(cv2.CAP_PROP_FOURCC))
self._actual_fourcc = "".join(chr((actual >> (8 * i)) & 0xFF) for i in range(4))
```

Extend the `opened` signal (or add `format_ready(str)`) so both panels can show
`1920x1080 @ 5 fps · YUY2` in `_lbl_res`. A silent fallback from YUY2 to MJPG
that the operator cannot see is exactly the kind of thing this codebase's
docstrings keep warning about.

Most USB cameras expose YUY2 only at low frame rates at full resolution — 1080p
YUY2 at 5 fps is typical. That is fine here and is the correct version of the
trade you were reaching for: **drop the acquisition rate, get uncompressed
frames.** Pair YUY2 + FFV1 (or PNG) and the chain is bit-exact from sensor to
disk.

#### 2.2e UI changes — `recording_panel.py` and `camera_tab.py`

Both panels render one model, so both change identically:

1. Rename the **"Preview:"** spin box to **"Acquire:"**, tooltip:
   *"Frame rate requested from the camera. This is the acquisition rate — the
   Record box below only selects which of these frames are written. Lower it to
   make an uncompressed pixel format available at full resolution."*
2. Tooltip on **"Record:"**: *"How many of the acquired frames are written.
   Cannot exceed the acquire rate."* Clamp it in `set_record_fps()`.
3. New **"Format:"** combo — `MJPG (compressed, fast)` / `YUY2 (uncompressed)`.
   Next to the resolution combo, since together they decide what the camera can
   deliver.
4. New **"Master:"** combo bound to `MASTER_CODECS`. This is the control that
   matters; put it where "Quality" is now.
5. Relabel the existing quality combo **"MP4 copy (CRF):"** and, when
   `state()["ffmpeg_found"]` is False, **disable it** with the tooltip
   *"ffmpeg not found — no MP4 copy is made. The Master setting above is what is
   recorded."* Right now that control looks live and is not, which is what sent
   you looking at CRF 18 in the first place.
6. New status line: **estimated disk rate**. After the first ~50 written frames,
   `bytes_on_disk / frames_written * record_fps * 3600` → `≈ 42 GB/hour`.
   Cheap, and it is what makes the lossless options safe to offer.

Rough sizing to put in the panel's tooltip, 1920x1080:

| master | per frame | at 2 fps | at 30 fps |
|---|---|---|---|
| MJPEG q98 | ~0.5-1 MB | ~5 GB/h | ~75 GB/h |
| FFV1 | ~1.5-3 MB | ~15 GB/h | ~220 GB/h |
| PNG (level 1) | ~2-4 MB | ~20 GB/h | not offered |

Real numbers depend entirely on image content — a mostly-dark beam image
compresses far better than these. The in-app estimator is the honest answer;
these are only for choosing where to start.

#### 2.2f Stills are already the best path — say so in the UI

`SessionRecorder.take_photo()` writes `cv2.imwrite(path, frame)` to PNG with a
JSON sidecar. That is already lossless relative to what the camera handed over,
which means with `YUY2` it is lossless full stop. Add to the Take Photo tooltip:
*"Saves a lossless PNG plus a beamline-state sidecar, independent of the video
settings. For a shot that matters, this is the highest-quality path in the app."*

Optionally add a **burst**: `take_photo(n=5)` writing five consecutive frames.
Cheap, and it covers "the moment we can't afford to miss" better than any video
setting.

### 2.3 `session.json` and tests

- `_write_manifest()` — replace the hard-coded `"master_codec": "MJPG"` with the
  real one, and add `"master_quality"`, `"camera_fourcc"`, `"camera_fourcc_requested"`.
  A session whose codec fell back must say so in its own manifest.
- `tests/test_video_recorder.py`: a test per master codec asserting the file or
  folder appears and `frames.csv` row count matches; a test that an
  unavailable codec falls back to MJPEG and logs the event rather than raising.
- `tests/test_session_recorder.py`: assert the manifest records the codec that
  was actually used, and that `record_fps` is clamped to the acquire rate.
- New `tests/test_capture_quality.py`: round-trip a known frame through the
  MJPEG-q100 writer and the FFV1 writer and assert `max_error == 0` for FFV1.
  That is the test that stops a future refactor from quietly dropping the
  backend argument and taking the quality setting with it.

---

## Phase 3 — Vacuum pressure font size control

### 3.1 Where it is now

`rbl/gui/vacuum_tab.py`, in `_build_ui()`:

```python
self._table.verticalHeader().setDefaultSectionSize(32)

# Bold, large font for the Pressure column so it stands out.
self._pressure_font = QFont()
self._pressure_font.setPointSize(theme.FS_BIG)   # 19
self._pressure_font.setBold(True)
```

applied per-cell inside `_redraw()` (the 100 ms table timer) at
`if col == _COL_PRESS: item.setFont(self._pressure_font)`.

**Note for whoever edits this:** `theme.FS_BIG` is documented in `theme.py` as a
**px** value for Qt stylesheets, and here it is being fed to `setPointSize()`,
which is **points**. 19 pt is roughly 25 px, so the pressure column is already
bigger than the theme intends. That is not a bug to fix — it is the size that is
on screen today and the operator wants it larger — but the new control must be
in points and must stop referencing `FS_BIG`, so nobody later "fixes" the units
and shrinks the readout by a third.

### 3.2 The change

Add to the module-level constants near `_HIDDEN_CFG_KEY`:

```python
# Pressure-column font size, in POINTS.  Deliberately not theme.FS_BIG: that
# constant is documented as px for stylesheets, and this is a QFont point size.
_PRESSURE_FONT_CFG_KEY = "vacuum_pressure_font_pt"
_PRESSURE_FONT_PT_DEFAULT = 19
_PRESSURE_FONT_PT_MIN     = 10
_PRESSURE_FONT_PT_MAX     = 72
```

with `_load_pressure_font_pt()` / `_save_pressure_font_pt(pt)` following the
exact shape of the existing `_load_hidden_gauges()` / `_save_hidden_gauges()`
pair — `from rbl.config.persistence import load_config, save_config` inside the
function, `try/except` that logs at debug and never raises.

In `_build_ui()`, a compact row above the gauge table inside `tbl_box`:

```python
font_row = QHBoxLayout()
font_row.addStretch()
font_row.addWidget(QLabel("Pressure font:"))
self._spin_press_font = QSpinBox()
self._spin_press_font.setRange(_PRESSURE_FONT_PT_MIN, _PRESSURE_FONT_PT_MAX)
self._spin_press_font.setValue(_load_pressure_font_pt())
self._spin_press_font.setSuffix(" pt")
self._spin_press_font.setToolTip(
    "Size of the pressure readings in the table below. Saved between sessions.")
self._spin_press_font.valueChanged.connect(self._on_pressure_font_changed)
font_row.addWidget(self._spin_press_font)
tbl_lay.addLayout(font_row)
```

Use `NoScrollSpinBox` from `rbl/gui/widgets/inputs.py`, not a bare `QSpinBox` —
`tests/test_no_scroll_inputs.py` enforces the house rule that a stray scroll
wheel over a control must not change its value.

Handler:

```python
def _on_pressure_font_changed(self, pt: int):
    self._pressure_font.setPointSize(pt)
    self._apply_row_height()
    _save_pressure_font_pt(pt)
    # _redraw() runs on a 100 ms timer and rebuilds every cell, so the new
    # font appears within one tick.  No explicit re-render call needed.

def _apply_row_height(self):
    """Rows must grow with the font or the digits get clipped mid-glyph."""
    pt = self._pressure_font.pointSize()
    metrics_h = QFontMetrics(self._pressure_font).height()
    self._table.verticalHeader().setDefaultSectionSize(max(32, metrics_h + 10))
```

**The row height is the part that is easy to miss.** `setDefaultSectionSize(32)`
is a fixed 32 px; at 40 pt the digits are taller than the row and get clipped.
Call `_apply_row_height()` once at construction with the loaded value, and again
on every change.

Two details:

- `_redraw()` (100 ms timer) rebuilds every `QTableWidgetItem` and re-applies
  `self._pressure_font` on the pressure column, so mutating the shared `QFont`
  object in place is all that is needed — the change shows up within one tick
  and no cell-walking is required.
- Leave the other five columns alone. The operator asked for the pressure
  number, and a table where every column is 40 pt fits three gauges on screen.

### 3.3 Test — `tests/test_vacuum_tab.py` (new, or extend the nearest existing)

- `test_pressure_font_spin_changes_cell_font`: set the spin to 40, render a
  state with one gauge, assert `item(0, _COL_PRESS).font().pointSize() == 40`.
- `test_pressure_font_persists`: set 40, monkeypatch `persistence.CONFIG_PATH`
  to a tmp path, rebuild the tab, assert the spin box comes up at 40.
- `test_row_height_grows_with_font`: assert
  `verticalHeader().defaultSectionSize()` is strictly larger at 40 pt than at
  19 pt.

---

## Phase 4 — Raster Planner: layout, and a species table you can add to

### 4.1 Where the defaults live today (the direct answer)

This is scattered across four files, which is why it was hard to find. **Part of
this phase is consolidating it** — see 4.4.

| What | Current value | Lives in |
|---|---|---|
| **Species table rows** | Protons 1.0 amu / 3.00 MeV / q1; Al 27 / 2.25 / 1; Ni 57 / 3.00 / 3; Ti 48 / 3.40 / 2 | `rbl/config/beam_species.py` → `DEFAULT_SPECIES` |
| Which row is selected on open | row 0 (Protons) | `beam_species.py` → `DEFAULT_SPECIES_ROW` |
| Drift, steerer exit → sample | `DRIFT_TO_SAMPLE_CM` | `rbl/config/steerer_geometry.py` |
| Plate length / gap / rating | 12.5 cm / 3.8 cm / 5 kV | `rbl/config/steerer_geometry.py` |
| **Beam FWHM** | 1.0 mm | **hard-coded literal** in `raster_planner_tab.py` `__init__`: `_spin(0.001, 100.0, 1.0, ...)` |
| **Sample width X / height Y** | 10.0 mm / 10.0 mm | **hard-coded literals**, same place |
| **Scan centre offset X / Y** | 0.0 / 0.0 | **hard-coded literals**, same place |
| **Turnaround margin k** | 1.5 × FWHM | **hard-coded literal**, same place |
| **X (fast) frequency** | 517.0 Hz | **hard-coded literal**, same place |
| **Y (slow) frequency** | 64.0 Hz | **hard-coded literal**, same place |
| Amplifier bandwidth wall | 10 000 Hz | `raster_planner_tab.py` module level, `AMP_MAX_BANDWIDTH_HZ` |
| Fallback load capacitance | `CAL_LOAD_CAP_PF` | `rbl/config/calibration_config.py` |
| AC trip current / max kV | `CAL_AC_TRIP_MA`, `CAL_MAX_KV` | `rbl/config/calibration_config.py` |
| Measured per-channel capacitance | measured, per channel | `~/.config/rbl/load_calibration.json`, read via `load_calibration_store.capacitance_pf_for()` |

So: **species → `beam_species.py`; geometry → `steerer_geometry.py`; everything
in the "Raster Parameters" and "Amplifier Drive" boxes → literals inside
`raster_planner_tab.py.__init__`.** That last row is the one to fix.

### 4.2 Layout restructure

**Remove the Dwell Uniformity plot.** In `__init__`, delete the `dwell_box`
group box, `self._fig_dwell`, `self._canvas_dwell`, `self._ax_dwell`, and delete
the `_draw_dwell()` method and its call site in `_do_recompute()`.

**Keep the dwell uniformity *number*.** `self.lbl_uniformity` in the "Required
Drive" box stays, the `dwell_uniformity()` calls stay, and the
`f"X {_pct(du_x)} peak-to-peak     Y {_pct(du_y)} peak-to-peak"` line stays.
Only the plot goes. Two reasons: it is one line of text that costs nothing, and
`tests/test_raster_planner_tab.py:222-232` asserts on it
(`test_changing_fwhm_changes_uniformity`, `test_uniformity_reports_both_axes`).
Deleting `lbl_uniformity` breaks the suite for no gain.

`du_x["profile_mm"]` / `profile_dose` become unused. Leave `raster_model.py`
alone — it is pure math with its own tests, and the profile arrays are part of
its contract.

**Move the species table beside the envelope.** Today:

```
row 1:  [ Raster Parameters | Amplifier Drive | Required Drive ]
row 2:  [ Deflection for Various Species ...................... ]   full width
row 3:  [ Operating Envelope       | Dwell Uniformity          ]
```

Target:

```
row 1:  [ Raster Parameters | Amplifier Drive | Required Drive ]
row 2:  [ Operating Envelope | Deflection for Various Species  ]
```

Concretely: delete `layout.addWidget(sp_box, stretch=0)`, and in the plot row
add `sp_box` where `dwell_box` was:

```python
plot_row.addWidget(env_box, stretch=1)
plot_row.addWidget(sp_box,  stretch=1)
layout.addLayout(plot_row, stretch=1)
```

The species box now stretches vertically, so drop `setMinimumHeight(150)` on
`tbl_species` (or lower it to ~120 as a floor) and let it fill. Consider
`stretch=1` / `stretch=1` to start; if the envelope plot reads cramped, `2`/`3`
in favour of the table, since the table is the thing being read off.

### 4.3 Editable species table — add, remove, persist

**Buttons.** A row under `tbl_species`, above the existing hint label:

```python
btn_add    = QPushButton("Add species")
btn_remove = QPushButton("Remove selected")
btn_reset  = QPushButton("Reset to lab defaults")
```

- **Add** appends a row seeded from the currently selected one (a new species is
  almost always a variant of the one being planned), name suffixed
  `" (copy)"`, and selects it so it can be typed over immediately. Columns
  4 and 5 get the same non-editable `"—"` placeholder `_populate_species()`
  makes — `_fill_species_deflections()` assumes every row has an item in every
  column and will `AttributeError` on a row that does not.
- **Remove** deletes the selected row. Refuse when only one row is left — a
  zero-row table makes `_selected_species()` return `DEFAULT_SPECIES_ROW` for a
  row that does not exist. Disable the button at `rowCount() == 1` rather than
  popping a dialog.
- **Reset** restores `DEFAULT_SPECIES` and clears the saved list.
- Every one of these ends with `self._recompute()`, and every one must be
  wrapped in the existing `self._updating` guard while it mutates the table, or
  `itemChanged` fires mid-mutation and recomputes against a half-built row.

**Persistence** (`~/.config/rbl/funcgen.json`, via `rbl/config/persistence.py` —
the same store `vacuum_tab.py` and `profiler_tab.py` already use):

```python
_SPECIES_CFG_KEY = "raster_species"      # [[name, mass_amu, energy_mev, q], ...]
_SPECIES_ROW_KEY = "raster_species_row"
```

- `_load_species()` → list of 4-tuples, falling back to `DEFAULT_SPECIES` on a
  missing key, a parse failure, or any row that fails the same validation
  `beam_species.py`'s self-test uses (`name and mass > 0 and energy > 0 and q >= 1`).
  A corrupt config must never stop the tab from opening.
- `_save_species()` writes the whole table on any edit, add, or remove —
  called from `_on_species_edited` (guarded), and from each button.
- Selected row saves too, so the tab reopens designing for the same species.
- `_populate_species()` takes the row list as an argument instead of reading
  `DEFAULT_SPECIES` directly, and sets `setRowCount(len(rows))` first. Right now
  the row count is fixed at construction from `len(DEFAULT_SPECIES)`.

`beam_species.py` keeps its current contents and its docstring is updated: it is
now explicitly *the factory default the Reset button restores*, not the runtime
table. That is what the file already claims in its "DEFAULTS, NOT CONSTRAINTS"
section — it just becomes true across restarts.

### 4.4 New `rbl/config/raster_defaults.py`

Move the nine literals out of `raster_planner_tab.__init__` so there is one place
to look. Data only, no computation, matching `steerer_geometry.py`'s shape:

```python
"""
raster_defaults.py
Opening values for the Raster Planner's input boxes.

WHY THIS EXISTS
---------------
These nine numbers used to be literals inside raster_planner_tab.__init__ —
`_spin(0.1, 10_000.0, 517.0, ...)` and eight more like it — which meant the
answer to "where do I change the default X frequency?" was "read a GUI
constructor."  Every other default this tab uses already lives in a config
module (steerer_geometry, calibration_config, beam_species); these are the
stragglers.

These are OPENING VALUES, not constraints.  Every one is editable in the tab
and nothing downstream reads them again.
"""

FWHM_MM_DEFAULT        = 1.0
SAMPLE_WIDTH_X_MM      = 10.0
SAMPLE_HEIGHT_Y_MM     = 10.0
OFFSET_X_MM_DEFAULT    = 0.0
OFFSET_Y_MM_DEFAULT    = 0.0
TURNAROUND_K_DEFAULT   = 1.5      # multiples of FWHM beyond the sample edge
FREQ_X_HZ_DEFAULT      = 517.0    # fast axis
FREQ_Y_HZ_DEFAULT      = 64.0     # slow axis

AMP_MAX_BANDWIDTH_HZ   = 10_000.0 # EEL5000 large-signal BW, no load (manual p.1-3)
```

`AMP_MAX_BANDWIDTH_HZ` moves here from the top of `raster_planner_tab.py`; keep
the manual citation on the line. Import it back into the tab so
`_check_envelope()` is unchanged. Add the `if __name__ == "__main__":` self-test
asserting every value is finite and positive (offsets excepted).

Then update the table in 4.1 into the module docstring of `raster_planner_tab.py`
under a **WHERE THE DEFAULTS LIVE** heading, so the next person asking this
question finds it in the file they are already reading.

### 4.5 Tests — extend `tests/test_raster_planner_tab.py`

- `test_dwell_plot_removed`: `not hasattr(tab, "_canvas_dwell")`.
- `test_uniformity_text_survives`: the two existing tests must still pass
  untouched. Do not edit them.
- `test_species_layout`: `tab.tbl_species` and `tab._canvas_env` share a parent
  layout row (or simply assert the species group box is not a direct child of
  the tab's top-level `QVBoxLayout` any more).
- `test_add_species_row`: rowCount +1, new row parses via `_species_row()`,
  columns 4-5 exist and are non-editable.
- `test_remove_last_row_refused`: reduce to one row, assert the Remove button is
  disabled and rowCount stays 1.
- `test_species_round_trip`: monkeypatch `persistence.CONFIG_PATH` to tmp, add a
  row, edit its mass, rebuild the tab, assert the row is there with the new mass.
- `test_corrupt_species_config_falls_back`: write `{"raster_species": "banana"}`
  and assert the tab opens on `DEFAULT_SPECIES`.

---

## Appendix A — Ordering and review

Land as four separate commits; each is independently revertable.

1. **Phase 1** first and alone. It is a pure bug fix with a clear regression
   test, and it is the one that is actively costing shots.
2. **Phase 3** next — smallest, self-contained.
3. **Phase 4** — layout plus persistence, no hardware.
4. **Phase 2** last and most carefully. It changes what lands on disk during a
   real run. Before merging, record a 60-second session at each master codec
   with a live camera, open the files, and compare against a `Take Photo` PNG
   taken in the same session. That PNG is the reference: it is the least-processed
   thing the app can produce.

After Phase 2, one thing is worth checking on the actual lab machine and
reporting back, because it decides whether any of the MP4 path is worth keeping:
`shutil.which("ffmpeg")` from a Python prompt. If IT has since installed ffmpeg
system-wide, `find_ffmpeg()` already picks it up with no rebuild
(`rbl.spec` lines 75-76), and the CRF control becomes live again.
