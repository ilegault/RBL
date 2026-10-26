# Spec: The ZWO ASI585MC, recorded at full bit depth by a Capture Process

Status: ready-for-agent
Date: 2026-10-23
Related: `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (new, binding),
`docs/adr/0004-monitoring-log-and-sessions.md` (decision 7: video starts and stops inside
a session), `docs/adr/0003-commanded-and-confirmed-cup-position.md` (confirmed position),
`docs/adr/0001-tests-first-and-no-muted-failures.md`,
`CONTEXT.md` ("Camera", "Logging and sessions", and "Known collisions": recorder,
position, session), `docs/ANTIVIRUS_FALSE_POSITIVE.md` (why no new executable).

> ADR 0001 is binding on all test work here: a failing test is fixed or escalated,
> never muted.

## Problem Statement

**The application cannot get a measurement-grade picture from the ZWO camera.** The
camera tab opens cameras through OpenCV's DirectShow backend
(`rbl/hardware/camera_source.py`, `_CameraThread.run`). The ZWO ASI585MC is not visible
there without ZWO's separate DirectShow driver, and through that driver it delivers
8-bit, already colour-processed frames with no dependable control of exposure or gain.
The images are meant for measurement: beam spot position and size in mm, and glow
intensity compared across a run. That needs every bit the sensor produces, fixed
manual exposure and gain, and a record of those settings.

**ZWO's own library does deliver that.** On the bench, `zwoasi` 0.2.0 on top of
`ASICamera2.dll` returned a full 3840 × 2160 `uint16` frame from Python with gain and
exposure set by code (`camera2_test.py`, 2026-10-23). The path works; nothing in the
application uses it.

**Recording it inside the GUI would endanger everything else the GUI does.** A
full-resolution 16-bit frame is about 16.6 MB. The GUI process also drives the steerer,
the slits and the Faraday cup, so a stalled disk write must not be able to stall it.

**A second executable is not an option on the lab PC.** Windows Defender's machine
learning scoring has quarantined the unsigned `RBL.exe` before; its bundled DLLs have
never been flagged (`docs/ANTIVIRUS_FALSE_POSITIVE.md`). RBL runs from a USB drive as a
one-folder build.

## Solution

The ZWO camera gets its own program, the **Capture Process**: `RBL.exe` relaunched with
`--capture-process` (in development, `python -m rbl.main --capture-process`). It is the
only thing that ever opens the camera. It records the **Master Recording** (SER files,
one per **segment**, exactly as the library delivers each frame), a **frame log**, and a
**settings record** per segment, all inside the session's folder. It sends the
application a small JPEG **Preview** at most five times a second and a status line once a
second. The application talks to it through `CaptureClient`, a new service that owns the
child process, and never touches a full frame.

The operator chooses one camera at a time, **USB camera** or **ZWO camera**, on both the
Overview recording panel and the Camera tab. The USB camera path is unchanged. Start
Video, Stop Video, Take Photo and Start/Stop Session work the same for both; the
settings rows change to suit the camera.

Every decision below came out of a grilling session on 2026-10-23; ADR 0008 records the
architectural ones.

## User Stories

1. As an operator, I pick "ZWO camera" on the Overview panel or the Camera tab, press
   Connect, and see a live Preview within a few seconds, with the camera's model, serial
   number and sensor temperature shown.
2. As an operator, I set exposure in milliseconds, gain, bit depth (RAW16 by default,
   RAW8 by choice) and a region of interest, and the camera uses exactly those values; no
   automatic exposure or gain is ever switched on.
3. As an operator, I start a session and press Start Video. Every frame inside the region
   of interest goes to disk at full bit depth, in the session's folder, alongside the
   CSV, the events and the cup log.
4. As an operator, I change exposure during a recording without stopping it. The change
   is logged as an event with the old and new value, and the recording continues in a new
   segment, so no file ever mixes settings.
5. As an operator, I record only every Nth frame on a long run, and the panel shows how
   many GB per hour that costs and how much disk is left. If free disk falls below
   20 GiB, recording stops cleanly with an event, and the session keeps logging CSV.
6. As an operator, I take a photo during a recording. The next frame (or a burst of up to
   20) is saved at full bit depth as FITS, with a beamline sidecar, and the recording
   neither stops nor starts a new segment.
7. As an operator, I cap the lens and press Take Darks. A Dark Frame for the current
   exposure, gain, bit depth and region of interest is added to a library I can reuse in
   later sessions.
8. As an operator, I make a dim glow visible on screen with the Preview's auto stretch
   and a brightness slider, knowing the label says it is a display stretch and that
   nothing I do to the Preview changes what is recorded.
9. As an analyst, I open a session folder and find, for every recorded frame, when it
   arrived, which segment and frame it is, and whether the end-station Faraday cup was
   confirmed IN, OUT, in transit or unknown. The file tells me plainly that upstream cups
   are not recorded.
10. As an analyst, I open any segment in SER Player or SER Viewer and see the right
    colours, and I can rely on each segment's settings record for every setting that was
    in force.
11. As an operator, if RBL crashes mid-recording, every frame written so far is intact and
    readable, the camera is released within about ten seconds, and a restarted RBL can
    connect to it at once.
12. As an operator, I switch back to the USB camera and everything works exactly as it did
    before this change.
13. As the developer, I can deploy the ZWO path to the lab PC with no new executable: the
    ZWO DLL ships inside the existing one-folder build.

## Implementation Decisions

### Where the code lives

ADR 0008 decision 3: the Capture Process imports no Qt. The layers stay as AGENTS.md §2
describes, with one new package for the child program:

| Path | What it is |
|---|---|
| `src/rbl/config/capture_config.py` (new) | every number in this spec; numbers only |
| `src/rbl/config/paths.py` | `ZWO_SETTINGS_STORE`, `ZWO_DARKS_DIR`, `zwo_sdk_dll_path()` |
| `src/rbl/config/zwo_settings_store.py` (new) | the persisted ZWO settings, modelled on `cup_settings_store.py` |
| `src/rbl/hardware/ser_format.py` (new) | SER header and trailer bytes; pure |
| `src/rbl/hardware/fits_format.py` (new) | FITS header and data bytes; pure |
| `src/rbl/hardware/capture_protocol.py` (new) | message encode/decode/validate, and the small pure rules below |
| `src/rbl/hardware/preview_stretch.py` (new) | 16- or 8-bit frame to 8-bit display image; pure |
| `src/rbl/capture/` (new package) | the Capture Process: `process.py`, `camera.py`, `recording.py`, `stills.py` |
| `src/rbl/services/capture_client.py` (new) | `CaptureClient(QObject)`, owns the child process |
| `src/rbl/services/session_recorder.py` | camera kind, ZWO video, photos and events |
| `src/rbl/gui/widgets/recording_panel.py`, `src/rbl/gui/camera_tab.py` | camera picker and ZWO rows |
| `src/rbl/gui/widgets/inputs.py` | `NoScrollSlider` |
| `src/rbl/gui/app.py` | construction, wiring, shutdown |
| `src/rbl/main.py` | `--capture-process` dispatch |

**Import rules, enforced by a contract test:** nothing under `rbl/capture/`, and none of
`ser_format`, `fits_format`, `capture_protocol` or `preview_stretch`, imports `PySide6`
or anything from `rbl.gui`, `rbl.services` or `rbl.state`. Nothing in `src/rbl` imports
`rbl.capture` except `rbl/main.py`. The first rule is what keeps the Capture Process
testable without a GUI and replaceable by a compiled program; the second keeps the GUI
from ever reaching for the camera directly.

### D1. Launching the Capture Process

`rbl/main.py:main()` checks `sys.argv` for `--capture-process` **before importing
anything from `rbl.gui`**, and if present calls `rbl.capture.process.main(argv)` and
exits with its return code. Without the flag, behaviour is unchanged.

`CaptureClient` builds the command line with a pure module-level function
`capture_command_line(frozen: bool, executable: str, args: list[str]) -> list[str]`:
frozen → `[executable, "--capture-process", *args]`; not frozen →
`[executable, "-m", "rbl.main", "--capture-process", *args]`. `frozen` is
`getattr(sys, "frozen", False)` and `executable` is `sys.executable`, passed in by the
caller so the function stays pure.

Arguments the Capture Process accepts (argparse, in `rbl/capture/process.py`):

| Argument | Meaning | Default |
|---|---|---|
| `--camera {zwo,fake}` | which camera implementation | `zwo` |
| `--sdk-dll PATH` | absolute path to `ASICamera2.dll` | required when `--camera zwo` |
| `--disk-stop-bytes N` | free-disk threshold that stops recording | `CAPTURE_DISK_STOP_BYTES` |
| `--heartbeat-timeout-s S` | seconds without a heartbeat before exiting | `CAPTURE_HEARTBEAT_TIMEOUT_S` |
| `--fake-fail-after N` | fake camera only: raise camera-lost after N frames | none |

`--camera fake` exists for tests and for bench demonstrations without hardware. It is
not shown anywhere in the GUI.

**Standard output belongs to the protocol.** The first thing `process.main` does is keep
a handle on the original binary stdout for protocol messages and point `sys.stdout` at
`sys.stderr`, so a stray `print()` anywhere cannot corrupt the protocol. Logging goes to
stderr. `CaptureClient` reads stderr and passes each line to
`logging.getLogger("rbl.capture")`.

### D2. The contract (ADR 0008 decision 4)

UTF-8 JSON, one object per line, `\n`-terminated, on the child's stdin (commands) and
stdout (replies and notifications). Every message has a string `"type"`. Every command
has an integer `"id"`; its reply is `{"type": "ok", "reply_to": id, ...}` or
`{"type": "error", "reply_to": id, "code": str, "message": str}`. The module docstring of
`capture_protocol.py` is the authoritative description, and must say that a compiled
replacement implements exactly what it describes.

`capture_protocol.py` provides `encode(msg: dict) -> bytes`, `decode(line: bytes) -> dict`
(raises `ProtocolError` on bad JSON, a missing or unknown `type`, a command without an
integer `id`, or a field of the wrong type or out of range), and one frozen dataclass per
command. Validation lives only there and is used by both sides.

`CAPTURE_PROTOCOL_VERSION = 1`. The child's first line is `hello`.
`capture_protocol.check_hello(msg: dict) -> None` raises `ProtocolError` when `type` is
not `hello` or `protocol` differs from `CAPTURE_PROTOCOL_VERSION`; `CaptureClient` then
sends `shutdown`, emits `error` and does not open the camera.

**Commands (application → Capture Process):**

| type | fields | reply fields |
|---|---|---|
| `open` | `camera_index: int` | `model, serial, sdk_version, sensor_width, sensor_height, bayer_pattern, controls, settings` |
| `close` | — | — |
| `set` | any of `exposure_us: int, gain: int, bit_depth: 8\|16, roi: {x, y, width, height}, keep_one_in_n: int` | `settings` (as applied, after snapping) |
| `preview` | `mode: "auto"\|"linear", brightness: float` | — |
| `start_recording` | `folder: str` (absolute session folder), `session_t0_utc: str` (ISO 8601), `first_segment_index: int`, `first_frame_index: int` | — |
| `stop_recording` | — | `next_segment_index, next_frame_index, segments: [str]` |
| `photo` | `folder: str`, `burst: int` (1-`CAPTURE_PHOTO_BURST_MAX`) | `files: [str]` (absolute) |
| `take_darks` | `folder: str`, `frames: int` (1-`CAPTURE_DARK_FRAMES_MAX`) | `file: str` |
| `beamline` | `cup_position: str`, `alumina_position: str` | — |
| `heartbeat` | — | none (no reply) |
| `shutdown` | — | `ok`, then the process exits |

`controls` is `{name: {"min": int, "max": int, "default": int}}` for `Exposure`, `Gain`,
`Offset` and `BandWidth`, from the library. `settings` is
`{"exposure_us", "gain", "bit_depth", "roi": {...}, "keep_one_in_n", "offset",
"usb_bandwidth", "high_speed_mode"}`.

**Notifications (Capture Process → application):**

| type | fields | when |
|---|---|---|
| `hello` | `protocol, pid, cameras: [str]` | first line, always |
| `status` | `acquired_fps, frames_written, frames_dropped_camera, frames_dropped_queue, recording, segment, bytes_per_hour, disk_free_bytes, sensor_temperature_c` | every `CAPTURE_STATUS_INTERVAL_S` while open |
| `preview` | `width, height, roi, stretch_mode, brightness, jpeg_b64` | at most `CAPTURE_PREVIEW_MAX_FPS` while open |
| `event` | `event: str, detail: str` | see D6 |
| `fatal` | `message` | just before exiting on an unrecoverable error |

Refusals use `error` with these codes, and no other: `not_open`, `already_recording`,
`not_recording`, `recording` (command not allowed while recording: `take_darks`, `close`),
`low_disk`, `bad_request`, `camera_error`.

### D3. Camera settings

- **No automatic anything.** `open` and every `set` apply exposure and gain with auto
  off, and force `HighSpeedMode` to 0. `Offset` and `BandWidth` stay at the library's
  defaults and are recorded, not exposed in the UI.
- **Bit depth** is 16 (`ASI_IMG_RAW16`) by default and 8 (`ASI_IMG_RAW8`) only when the
  operator chooses it. No other image type is ever requested; no RGB24, no debayering
  before saving.
- **ROI snapping** is one pure function in `capture_protocol.py`,
  `snap_roi(x, y, width, height, sensor_width, sensor_height) -> (x, y, width, height)`:
  width down to a multiple of 8, height down to a multiple of 2, x and y down to even
  numbers, all clamped inside the sensor, width and height at least 8 and 2. Even x and y
  keep the Bayer pattern of the ROI identical to the sensor's, which is what lets one
  `bayer_pattern` describe every segment.
- **Any settings change while streaming stops and restarts video capture** around the
  change and increments a settings generation number carried by every frame. A short
  gap in the recording is the price of a segment never mixing settings: frames already in
  the library's buffer would otherwise carry the old exposure under the new settings
  record.
- **Defaults** on first ever connect: exposure `ZWO_EXPOSURE_US_DEFAULT` (20 000 µs), gain
  `ZWO_GAIN_DEFAULT` (100), bit depth 16, ROI the full sensor, keep 1 in 1, Preview auto
  at brightness 1.0. After that, the last applied values come from
  `~/.config/rbl/zwo_camera.json` (`zwo_settings_store.py`: `ZwoSettings` frozen
  dataclass, `validate_settings`, `load_settings`, `save_settings`, same shape and
  warning behaviour as `cup_settings_store.py`). The store also holds the selected camera
  kind. It is saved whenever an applied value changes.

### D4. The Master Recording on disk

Everything the Capture Process writes for a session goes under `<session folder>/zwo/`,
and every location comes from the command that asked for it, never from a path the child
works out for itself:

```
<session>/zwo/frames.csv
<session>/zwo/segment_000.ser
<session>/zwo/segment_000.json      (settings record)
<session>/zwo/segment_001.ser ...
<session>/photos/zwo_photo_000_0.fits (+ .json beamline sidecar written by the application)
```

**SER** (`ser_format.py`): the published SER v3 layout. A 178-byte little-endian header:
`FileID` `b"LUCAM-RECORDER"`, `LuID` 0, `ColorID`, `LittleEndian`, `ImageWidth`,
`ImageHeight`, `PixelDepthPerPlane` (16 or 8), `FrameCount`, `Observer` `"RBL"`,
`Instrument` `"<model> <serial>"`, `Telescope` empty (each 40 bytes, space-padded), then
`DateTime` (local) and `DateTime_UTC` as int64 counts of 100 ns since 0001-01-01. Frames
follow as raw little-endian pixels, then a trailer of `FrameCount` int64 UTC timestamps,
one per frame, in the same unit. `ColorID` from the library's Bayer pattern:
`RG → 8 (RGGB)`, `GR → 9 (GRBG)`, `GB → 10 (GBRG)`, `BG → 11 (BGGR)`. The
`LittleEndian` field is one named constant, `SER_LITTLE_ENDIAN_FLAG = 1`; bench ticket
B2 confirms SER Player reads our files unswapped, and if it does not, that constant is
the only thing that changes.

`ser_format.py` exposes `pack_header(...) -> bytes`, `pack_trailer(timestamps) -> bytes`,
`color_id_for_bayer(pattern: str) -> int` and `ticks_from_datetime(dt) -> int`. The
writer that holds a file handle lives in `rbl/capture/recording.py`.

**Crash safety.** The writer rewrites the header's `FrameCount` every
`CAPTURE_SER_HEADER_REFRESH_S` (2 s) and on close, and writes the trailer on close. A
killed process therefore leaves every frame up to the last refresh correctly counted.

**A segment ends** when settings change, when it reaches `CAPTURE_SEGMENT_MAX_BYTES`
(3.5 GiB), on Stop Video, on low disk, on camera loss, on heartbeat loss and on shutdown.
Segment numbering and frame numbering continue across every video run in a session
(ADR 0004 decision 7): the application passes `first_segment_index` and
`first_frame_index`, and `stop_recording` returns the next ones.

**Settings record** (`segment_NNN.json`), written when the segment opens and rewritten,
atomically (temp name then rename, as `snapshot_json.dump_json` does), when it closes:

```json
{
  "schema": 1,
  "segment": 0,
  "file": "segment_000.ser",
  "opened_utc": "...", "closed_utc": "...",
  "closed_reason": "stop|settings_change|size_limit|low_disk|camera_lost|heartbeat_lost|shutdown",
  "frames": 0,
  "frames_dropped_camera": 0, "frames_dropped_queue": 0,
  "camera": {"model": "...", "serial": "...", "sdk_version": "...", "bayer_pattern": "RG"},
  "settings": {"exposure_us": 0, "gain": 0, "bit_depth": 16,
               "roi": {"x": 0, "y": 0, "width": 0, "height": 0},
               "keep_one_in_n": 1, "offset": 0, "usb_bandwidth": 0, "high_speed_mode": 0},
  "sensor_temperature_c_open": 0.0, "sensor_temperature_c_close": 0.0,
  "focus_preset": null
}
```

`focus_preset` is always `null` in this spec. It is the slot motorized focus will fill
later, so that change adds no new format.

**Frame log** (`zwo/frames.csv`), appended across video runs, header written only when
the file is new, flushed after every row:

```
# RBL ZWO frame log, schema 1
# wall_utc is when the frame arrived from the camera library, not when its exposure began.
# cup_position reflects the end-station Faraday cup only. Upstream cups are manual and not recorded; beam-on-target cannot be inferred from this file alone.
frame_index,wall_utc,t_rel_s,segment,segment_frame,cup_position,alumina_position,photo
```

- `wall_utc` comes from the Capture Process's own monotonic anchor (a
  `time.perf_counter()` and `datetime.now(timezone.utc)` pair taken at start-up, the same
  method `SessionRecorder` uses), so a clock correction mid-run cannot reorder rows.
- `t_rel_s` is `wall_utc` minus `session_t0_utc`.
- `cup_position` is the last value the application sent in a `beamline` command:
  `IN`, `OUT`, `IN_TRANSIT`, `INDETERMINATE` or `UNKNOWN` (the `CupPosition` values plus
  `UNKNOWN`; CONTEXT.md "Position" collision). `UNKNOWN` until the first `beamline`
  message.
- `alumina_position` is always empty in this spec. It is reserved for the servo-driven
  alumina stage.
- `photo` is the photo's file name when that frame was also saved as a photo, else empty.

The application computes `cup_position` with one pure function in
`capture_protocol.py`: `cup_position_label(connected: bool, stale: bool,
confirmed: CupPosition) -> str`, `UNKNOWN` when not connected or stale, else
`confirmed.value`. No column says whether beam was on the target. The log records facts,
never conclusions.

### D5. Inside the Capture Process

Four threads, no Qt:

1. **stdin reader**: decodes lines into a command queue. End of stdin counts as
   `shutdown`.
2. **main loop**: runs commands, sends `status` and `preview` on their intervals, checks
   free disk once a second while recording, and enforces the heartbeat timeout (any
   received message counts as a heartbeat).
3. **acquisition**: `camera.get_frame(timeout_ms)` in a loop. Each frame gets its arrival
   time, a running acquired index and the settings generation. It replaces the "latest
   frame" (under a lock, for Preview and photos) and, while recording and when
   `acquired_index % keep_one_in_n == 0`, goes onto a bounded write queue of
   `CAPTURE_WRITE_QUEUE_FRAMES` (64). **A full queue drops the frame and counts it; the
   acquisition thread never blocks on the disk.**
4. **writer**: takes frames off the queue, opens a new segment when the generation
   changes or the size limit is reached, appends to the SER, writes the frame-log row.

Timeout per `get_frame` is `max(2 × exposure_ms + 500, CAPTURE_FRAME_TIMEOUT_MIN_MS)`.
`CAPTURE_CAMERA_LOST_TIMEOUTS` (5) consecutive timeouts, or a library error, is camera
loss: recording stops with `closed_reason: camera_lost`, an `event` is sent, and the
process stays alive and reports `not_open` until opened again.

**Heartbeat loss** (ADR 0008 decision 5): close the segment with
`closed_reason: heartbeat_lost`, flush and close the frame log, close the camera, exit
with code 0. **Shutdown**: the same with `closed_reason: shutdown`, after replying `ok`.

`bytes_per_hour` in `status` is `estimate_bytes_per_hour(width, height, bit_depth,
acquired_fps, keep_one_in_n)` (pure, in `capture_protocol.py`):
`width × height × (bit_depth / 8) × acquired_fps / keep_one_in_n × 3600`, plus nothing for
the frame log.

**Low disk:** `start_recording` is refused with `low_disk` when free space under the
session folder is below `--disk-stop-bytes`, and a running recording stops with
`closed_reason: low_disk` and an `event` when it falls below. Preview continues. The
session's own 1 GiB auto-stop (`DISK_AUTOSTOP_BYTES`) is unchanged and still stops the
whole session.

**Camera implementations** (`rbl/capture/camera.py`): a `CaptureCamera` `Protocol`
(`open`, `info`, `controls`, `apply(settings)`, `start_stream`, `get_frame`,
`dropped_frames`, `temperature_c`, `stop_stream`, `close`), `ZwoCamera` (wraps `zwoasi`,
imported inside `ZwoCamera.open` so nothing else pays for it; `zwoasi.init(sdk_dll)` is
always called with the explicit path), and `FakeCamera`. `FakeCamera` produces
deterministic frames: for acquired index *k*, pixel (*x*, *y*) is
`((k * 7 + x + 3 * y) % 4096) << 4` at bit depth 16 and `(k + x + y) % 256` at bit depth
8, reports a 1920 × 1080 sensor with Bayer pattern `RG`, model `"Fake ASI"` and serial
`"FAKE0001"`, a temperature of 25.0 °C, and with `--fake-fail-after N` raises
camera-lost after N frames. Its frames are what the tests compare files against, bit
for bit.

### D6. Events

The Capture Process reports; the application writes. `events.csv` keeps one writer,
`SessionRecorder._write_event`, which records each `event` notification as
`zwo_<event>` while a session is running and logs it otherwise. Events the Capture
Process sends: `recording_started`, `recording_stopped` (detail: reason),
`segment_opened` (file), `segment_closed` (file, reason, frames), `settings_changed`
(one per changed field, detail `"<field>: <old> -> <new>"`), `photo` (file names),
`darks_saved` (file), `camera_lost` (message), `low_disk` (free bytes). When the
application is gone, the reason survives in the settings record's `closed_reason`.

### D7. Photos and Dark Frames

**Photo** (`photo` command): the next `burst` acquired frames, whatever keep-1-in-N
says, each saved as FITS in the given folder as `zwo_photo_<NNN>_<k>.fits`, numbered on
from the highest `zwo_photo_` number already in that folder. A photo never starts a
segment and never pauses the stream. When a photo frame is also written to the SER, its
frame-log row carries the file name. The application then writes the beamline sidecar
next to each file with `dump_json`, in the shape `SessionRecorder.take_photo()` already
uses for USB photos, adding `"camera": "zwo"`, and records one `photo` event. Without a
session the folder is `logs/photos/`, as for the USB camera.

**Dark Frames** (`take_darks` command, refused while recording): the application first
asks the operator to confirm the lens is capped. The Capture Process averages the next
`frames` frames in float64 and writes the mean as float32 FITS to
`<folder>/<serial>/dark_exp<us>us_gain<g>_raw<bits>_x<x>y<y>w<w>h<h>_<YYYYmmddTHHMMSS>.fits`,
never overwriting (the timestamp makes each set its own file). `folder` is
`ZWO_DARKS_DIR` (`DATA_DIR / "zwo_darks"`), outside any session, because a Dark Frame
does not depend on the beamline and is reused.

**FITS** (`fits_format.py`): one primary HDU, 2880-byte blocks, 80-character cards, `END`,
space-padded header, zero-padded data, big-endian data. Photos at 16 bits: `BITPIX = 16`,
`BZERO = 32768`, `BSCALE = 1`; at 8 bits: `BITPIX = 8`. Dark Frames: `BITPIX = -32`. Cards
on every file: `EXPTIME` (seconds), `GAIN`, `BAYERPAT`, `XOFFSET`, `YOFFSET` (ROI origin),
`CCD-TEMP`, `DATE-OBS` (UTC ISO), `INSTRUME`; on Dark Frames also `NCOMBINE`. Exposes
`pack_fits(data: np.ndarray, cards: list[tuple[str, object, str]]) -> bytes`.

### D8. Preview

The Capture Process makes it, so the application does almost nothing per frame. At most
`CAPTURE_PREVIEW_MAX_FPS` (5): take the latest frame, debayer it with OpenCV for display
(`cv2.cvtColor`, code chosen from the Bayer pattern), downscale with `INTER_AREA` to at
most `CAPTURE_PREVIEW_MAX_WIDTH_PX` (960) wide, convert with
`stretch_preview(image, bit_depth, mode, brightness) -> np.ndarray` (uint8), encode as
JPEG at quality `CAPTURE_PREVIEW_JPEG_QUALITY` (85), base64.

`stretch_preview` (pure, `preview_stretch.py`) **never modifies its input array**:
- `linear`: full-scale mapping, 0 to 2^bit_depth − 1 onto 0 to 255, times brightness.
- `auto`: black point at the `PREVIEW_AUTO_LOW_PCT` (0.5th) percentile and white point at
  the `PREVIEW_AUTO_HIGH_PCT` (99.5th) percentile of the image, mapped onto 0 to 255,
  times brightness. A flat image (white point equal to black point) maps to 0.
- Brightness is clamped to `PREVIEW_BRIGHTNESS_MIN`..`PREVIEW_BRIGHTNESS_MAX`
  (0.25..4.0); output is clipped to 0..255.

On screen the Preview always carries the label **"Display stretch: not intensity"**
(auto) or **"Linear display"** (linear). The application shows the JPEG as it arrives and
does no image processing of its own.

### D9. `CaptureClient`

`rbl/services/capture_client.py`. One per process, constructed in `MainWindow.__init__`
next to `CameraSource`, the only owner of the child. It runs the child with `QProcess`,
reads stdout lines with `readyReadStandardOutput`, decodes with `capture_protocol.decode`
and turns them into signals:

```python
hello      = Signal(dict)
opened     = Signal(dict)      # the open reply
closed     = Signal()
status     = Signal(dict)
preview    = Signal(QImage, dict)   # decoded JPEG, plus the message's other fields
event      = Signal(str, str)
error      = Signal(str)        # fatal, refusals, decode failures, unexpected exit
exited     = Signal(int)        # exit code
```

Public methods: `start(camera: str = "zwo")`, `open(camera_index: int = 0)`, `close()`,
`apply(**settings)`, `set_preview(mode, brightness)`, `start_recording(...)`,
`stop_recording()`, `photo(folder, burst)`, `take_darks(folder, frames)`,
`on_cup_actuation_state(state)`, `shutdown()`, `is_running()`, `is_open()`. Each command
method returns the command `id`; replies arrive through a `reply = Signal(int, dict)`
signal (`ok` or `error` payload). A heartbeat `QTimer` at `CAPTURE_HEARTBEAT_INTERVAL_S`
runs while the process does.

`on_cup_actuation_state` takes a `CupActuationState` (`src/rbl/snapshots.py`), computes
`cup_position_label(state.connected, state.stale, state.confirmed)` and sends `beamline`
only when the label changes, plus once right after `open`. `MainWindow.__init__` connects
`self.beamline.cup_actuation_changed` to it, with a comment saying why.

`shutdown()` sends `shutdown`, waits up to `CAPTURE_SHUTDOWN_WAIT_S` (5 s) for exit, then
kills. `MainWindow.closeEvent` calls it right after `session_recorder.stop`.

`start("zwo")` passes `--sdk-dll` from `paths.zwo_sdk_dll_path(...)`, a pure function
of `(frozen, bundle_dir, repo_root, env)`: the `RBL_ZWO_SDK_DLL` environment variable if
set; else, frozen, `bundle_dir / "ASICamera2.dll"` (`bundle_dir` is `sys._MEIPASS`);
else `repo_root / "vendor" / "zwo" / "ASICamera2.dll"`. If the file does not exist,
`start` emits `error("ZWO SDK library not found: <path>")` and does not launch.

### D10. `SessionRecorder`

`SessionRecorder` stays the one model both panels render (its docstring's invariant).
It gains:

- a constructor argument `capture: CaptureClient`;
- `set_camera_kind(kind: str)` with `kind` in `{"usb", "zwo"}`, refused with a `warn`
  status while video is running; switching closes whichever camera was open; and
  `camera_kind()`;
- ZWO setters that forward to the client and re-render: `set_zwo_settings(**fields)`,
  `set_zwo_preview(mode, brightness)`;
- `start_video` / `stop_video` dispatch on the kind. For `zwo`, `start_video` requires
  `capture.is_open()`, then sends `start_recording` with the session folder,
  `self._derive_wall(0.0)` as `session_t0_utc`, and its own `_zwo_next_segment` and
  `_zwo_next_frame`, which the `stop_recording` reply updates. The `video_started` /
  `video_stopped` events and `_video_runs` entries work as for the USB camera, with a
  `"camera": "zwo"` field.
- `take_photo` dispatches on the kind (D7). The USB path is unchanged.
- `take_darks(frames: int)` for the ZWO camera only.
- `capture.event` → `_write_event(f"zwo_{event}", detail)` while recording.
- `capture.exited` or `capture.error` while ZWO video runs → stop the ZWO video run,
  `_write_event("zwo_capture_lost", detail)`, status `"ZWO camera lost — continuing CSV
  only"` in `warn`: the same rule as `_on_camera_error`, CSV first-class.
- `state()` adds `camera_kind`, `zwo_open`, `zwo_recording`, and the last `status`
  message's fields under `zwo_status`.
- `session.json` gains a `"zwo"` block: `{"runs", "segments", "camera"}` (the `open`
  reply's identity fields). The existing `"video"` block is unchanged and keeps
  describing the USB camera.

### D11. The panels

`RecordingPanel` and `CameraTab` change identically, as they always have:

- A **Camera:** `NoScrollComboBox` at the top: `USB camera`, `ZWO camera`, bound to
  `set_camera_kind`.
- With **USB camera** selected, every existing row shows and behaves exactly as now.
- With **ZWO camera** selected, the USB-only rows (Acquire, Resolution, Format, Master,
  MP4 copy, Record fps) are hidden and these show instead: **Exposure** (ms,
  `QuietDoubleSpinBox`, range from the `open` reply's `controls`), **Gain**
  (`NoScrollSpinBox`, same), **Bit depth** (`NoScrollComboBox`: `RAW16 (default)`,
  `RAW8`), **ROI** x / y / width / height (`NoScrollSpinBox`es) and a **Full sensor**
  button, **Keep 1 in N** (`NoScrollSpinBox`, 1-`CAPTURE_KEEP_ONE_IN_N_MAX`),
  **Preview** (`NoScrollComboBox`: `Auto stretch`, `Linear`) and **Brightness**
  (`NoScrollSlider`, new in `inputs.py`, ignores wheel events like `NoScrollComboBox`),
  **Take Darks** with a confirm dialog ("Cap the lens before taking Dark Frames."), and
  a status line: acquired fps, frames written, dropped, sensor °C, ≈ GB/hour, free disk.
- Connect / Disconnect, Start / Stop Video, Take Photo and Start / Stop Session keep their
  places and meanings for both cameras.
- Values shown come from the `set` reply (after snapping), never from what was typed.
- The Preview's label (D8) sits under the video view.

**ROI drawing** (Camera tab only): while not recording, **Full sensor** sets the ROI to
the whole sensor so the operator can see everything; dragging a rectangle on the video
view then applies it as the ROI. The preview message's `roi`, `width` and `height` map
view coordinates back to sensor pixels. The Overview panel offers the numeric fields
only.

### D12. Packaging

- `requirements.txt`: `zwoasi==0.2.0`.
- `rbl.spec`: add `"zwoasi"` to `all_hiddenimports`; add
  `vendor/zwo/ASICamera2.dll` to `all_binaries` with destination `"."` when the file
  exists, and print a warning (not an error) when it does not. The DLL is ZWO's x64
  `ASICamera2.dll` from the ASI Camera SDK, byte for byte. It is not committed:
  `.gitignore` gains `vendor/zwo/*.dll`, and a `vendor/zwo/README.md` says where it comes
  from and records its SHA-256.
- No new executable, no installer, nothing unpacked at run time.
- `tools/zwo_bench.py` (standalone, no `rbl` imports, like `tools/scope_fwhm.py`): the
  frame-rate measurement at full sensor and at 640 × 400 in RAW16 and RAW8 for 10 s each,
  with dropped-frame counts, and the distinct-levels check
  (`len(np.unique(img))`, `np.unique(img % 16)[:20]`). Takes `--sdk-dll`.

## Testing Decisions

A test is good when it fails if the behaviour is wrong, whatever the implementation.
Files are compared byte for byte with what the fake camera produced; nothing is asserted
on private attributes.

### Pure, no Qt, no process

- `tests/test_ser_format.py`: write a header, N fake frames and a trailer; parse them
  back **with a reader written in the test file from the published layout** (`struct`,
  not `ser_format`), and check every header field, every pixel and every timestamp;
  `color_id_for_bayer` for all four patterns; `ticks_from_datetime` against a known value.
- `tests/test_fits_format.py`: same approach with a test-side FITS reader: block sizes,
  card layout, `END`, `BZERO` round trip of 0 and 65535, float32 darks, padding.
- `tests/test_capture_protocol.py`: encode/decode round trip of every command and
  notification; `ProtocolError` for bad JSON, missing `type`, unknown `type`, missing or
  non-integer `id`, wrong field types, out-of-range `burst`, `frames`, `bit_depth`,
  `keep_one_in_n`; `check_hello` for a wrong type and a wrong version; `snap_roi` edge cases (odd origins, sizes not multiples, ROI past the
  sensor edge, tiny ROI); `estimate_bytes_per_hour`; `cup_position_label` for every
  `CupPosition`, disconnected and stale.
- `tests/test_preview_stretch.py`: the input array is unchanged after the call (compare
  with a copy); linear maps full scale to 255 at both bit depths; auto maps the
  percentiles to 0 and 255; flat image gives 0; brightness clamping.
- `tests/test_capture_command_line.py`: `capture_command_line` frozen and not frozen;
  `zwo_sdk_dll_path` for env override, frozen and source layouts.
- `tests/test_zwo_settings_store.py`: as `test_cup_settings_store`: defaults, round trip,
  rejected values produce warnings and defaults, corrupt file.

### The Capture Process from the outside (Seam A)

`tests/test_capture_process.py` starts the real child, `[sys.executable, "-m",
"rbl.main", "--capture-process", "--camera", "fake", ...]`, speaks the protocol to it and
checks only what it sends and what it leaves on disk. Every test kills the process in
teardown. Generous timeouts (30 s); no assertion on speed.

- First line is `hello` with `protocol == CAPTURE_PROTOCOL_VERSION`; every stdout line is
  valid JSON, including after commands that log.
- `open` → reply with the fake identity; `status` and `preview` arrive; the preview JPEG
  decodes to the advertised size.
- Record N frames: SER header, pixels (equal to `FakeCamera`'s formula), trailer count and
  timestamps; `frames.csv` has one row per frame with matching `segment_frame`; settings
  record matches what was applied.
- `set` exposure mid-recording → exactly two segments, each with one consistent settings
  record, and a `settings_changed` event `"exposure_us: A -> B"`.
- Keep 1 in 3 → written frames are acquired indices 0, 3, 6 … (checked through pixel
  values).
- Second video run in the same folder continues segment and frame numbering.
- `beamline` cup position appears in subsequent frame-log rows; `UNKNOWN` before any.
- `photo` burst 3 while recording → three FITS files whose pixels equal the stream frames
  at those indices, their names in `frames.csv`, and still one segment.
- `take_darks` refused while recording (`recording`); otherwise one float32 FITS equal to
  the mean of the fake frames.
- `--fake-fail-after` → `camera_lost` event, `closed_reason: camera_lost`, readable SER.
- `--disk-stop-bytes` set larger than any disk → `start_recording` refused `low_disk`.
- Stop sending heartbeats with `--heartbeat-timeout-s 2` → the process exits within 10 s,
  the last settings record says `heartbeat_lost`, and the SER header count equals the
  frame-log rows.
- `shutdown` → `ok`, exit code 0, files closed.

### The application side (Seam B)

Through the real objects, with the real child on `--camera fake` and no fake client.
`CaptureClient.start("fake")` passes no `--sdk-dll`, so no DLL is needed in CI. Qt waits
use a helper that spins `processEvents` until a condition holds or a timeout passes.

- `tests/test_capture_client.py`: hello → opened → status and preview signals;
  `on_cup_actuation_state` sends only on change (asserted through the frame log);
  `shutdown` exits within the wait; a missing DLL path emits `error` and launches nothing.
  The version check itself is the pure `check_hello` (below), tested with the pure tests.
- `tests/test_session_recorder_zwo.py`: session + ZWO video → `zwo/` files exist,
  `events.csv` carries `zwo_segment_opened` and `zwo_recording_stopped`, `session.json`
  has the `zwo` block; photo writes FITS plus sidecar and a `photo` event; killing the
  child mid-run records `zwo_capture_lost` and the CSV keeps growing; switching kind is
  refused while video runs.
- `tests/test_recording_panel.py` and `tests/test_camera_tab.py` (extended): the picker
  shows and hides the right rows; values displayed come from the snapped reply; changing
  Preview mode and brightness during a recording leaves exactly one segment and an
  unchanged settings record (the Preview never touches data); the brightness slider
  ignores wheel events.
- `tests/test_capture_imports.py` (contract): the import rules in "Where the code lives",
  by AST scan of the source files.

### Regression

Every existing camera, panel, recorder and session test passes unchanged with the USB
camera selected. Any of them that must change is rewritten in place under the same name;
none is deleted.

### Real hardware (bench, `ready-for-developer`)

- **B1, frame rate and bit depth**: run `tools/zwo_bench.py` on the bench PC; record fps
  and drops at both ROIs and bit depths, and the distinct-levels result, in the ticket.
  The 640 × 400 RAW16 figure becomes the Capture Process's target for the next check.
- **B2, end to end**: a real session with the real camera: record, change exposure, take
  a photo and Dark Frames; open the SER in SER Player and SER Viewer (colours right, not
  byte-swapped, timestamps present), open the FITS in Fiji; compare acquired fps in
  `status` with B1. Confirms `SER_LITTLE_ENDIAN_FLAG`.
- **B3, lab PC deployment**: install the ZWO native driver from its `.inf` files through
  Device Manager (no installer), confirm the bundled DLL survives the copy, connect from
  the one-folder build, and write `docs/hardware/zwo-camera-deployment.md`: driver
  version, steps, how to confirm it worked, DLL SHA-256.

## Out of Scope

- Motorized focus and Focus Presets (the FocusCube 3 or any other). The settings record's
  `focus_preset` slot is all this spec does. They get their own grill once the hardware
  is chosen.
- The alumina stage. Only the empty `alumina_position` column.
- Any export, conversion or editing tool (SER to MP4, colour correction, frame stamping).
  SER Player and SER Viewer cover viewing; a converter gets its own spec, and must not
  depend on `ffmpeg.exe` on the lab PC.
- Any colour correction or debayering of recorded data. Debayering exists only to draw
  the Preview.
- Reattaching to a Capture Process after the application restarts (ADR 0008 decision 5).
- A compiled Capture Process, or any second executable.
- Using both cameras at once.
- Changes to the USB camera path, `CameraSource`, `VideoRecorder` or their codecs.
- The app-wide scroll-wheel rule for existing widgets (its own plan). New widgets here use
  the no-scroll variants.
- Dark Frame subtraction or any analysis.
- Code signing.

## Further Notes

**Suggested slicing for `/to-tickets`** (numbering continues from 61). The pure pieces
come first so every later ticket has tested foundations; the Capture Process is built
and proved through Seam A before the application talks to it.

1. Pure formats: `ser_format`, `fits_format`, their tests.
2. Pure contract and rules: `capture_config`, `capture_protocol`, `preview_stretch`,
   `paths` additions, `zwo_settings_store`, their tests.
3. Capture Process skeleton with `FakeCamera`: `main.py` dispatch, stdout discipline,
   `hello`, `open`, `close`, `set`, `preview`, `status`, heartbeat, `shutdown`; the import
   contract test. Blocked by 2.
4. Recording: segments, frame log, settings records, generation roll, keep 1 in N, size
   limit, low disk, camera loss, heartbeat loss. Blocked by 1, 3.
5. Photos and Dark Frames in the Capture Process. Blocked by 4.
6. `ZwoCamera` over `zwoasi` (tested against a stand-in module with `zwoasi`'s shape for
   the mapping and timeout logic). Blocked by 3.
7. `CaptureClient`. Blocked by 3.
8. `SessionRecorder` integration. Blocked by 4, 5, 7.
9. Panels, `NoScrollSlider`, `MainWindow` wiring (construction, cup push, shutdown).
   Blocked by 8.
10. ROI drawing on the Camera tab. Blocked by 9.
11. Packaging and `tools/zwo_bench.py`. Blocked by 6.
12. Add `"rbl.capture"` to `[tool.rbl.type_gate].hard` in `pyproject.toml`. Gate config:
    `Auto-merge: no`, a leaf. Blocked by 5, 6.
13. B1 bench (`ready-for-developer`). Blocked by 11.
14. B2 bench (`ready-for-developer`). Blocked by 9, 11.
15. B3 bench (`ready-for-developer`). Blocked by 14.

**The heartbeat timeout is a trade.** Ten seconds means a hung GUI that recovers within
ten seconds loses nothing, and a dead one holds the camera for at most ten seconds.

**Why SER and not per-frame files:** appending bytes is the cheapest write there is,
timestamps travel in the file, and the astronomy viewers already open it. FITS is used
where one image is one file: photos and Dark Frames.
