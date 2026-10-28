# 65: The Capture Process runs on a fake camera: launch, contract, Preview, status, heartbeat

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 63, 64

**Spec:** `.scratch/zwo-capture/spec.md`, sections D1, D2, D3, D5 (threads 1-3, heartbeat),
D8, "Where the code lives" (import rules), "Testing Decisions" → Seam A
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (all decisions),
`docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/main.py`; `src/rbl/hardware/capture_protocol.py` (ticket 63) module
docstring; `src/rbl/hardware/preview_stretch.py` (ticket 64); `tests/test_screenshot_overview.py`
lines 30-50 (how a test runs a child Python with `PYTHONPATH` set to `src`).

## What to build

A Qt-free child program that the application can start, talk to and stop, proved entirely from
the outside. Recording, photos and the real ZWO camera come in later tickets.

### `src/rbl/main.py`

1. `main(argv: list[str] | None = None) -> None`. `argv` defaults to `sys.argv[1:]`. When
   `"--capture-process"` is in `argv`, import `rbl.capture.process` **inside the branch**, call
   its `main()` with `argv` minus that flag, and `raise SystemExit(<its return code>)`.
   Otherwise behave exactly as now. **Nothing from `rbl.gui` (or `PySide6`) may be imported
   before that check**: the Capture Process must never load Qt (ADR 0008 decision 3). Update the
   module docstring's Usage block with the capture flag and why it exists.

### `src/rbl/capture/` (new package)

2. `__init__.py`: docstring only, with `WHY THIS EXISTS` (ADR 0008: the one owner of the ZWO
   camera, plain Python with threads, no Qt, replaceable by a compiled program).
3. `camera.py`:
   - `class CameraLost(Exception)`.
   - `@dataclass(frozen=True) class CameraInfo`: `model: str`, `serial: str`,
     `sdk_version: str`, `sensor_width: int`, `sensor_height: int`, `bayer_pattern: str`.
   - `@dataclass(frozen=True) class CameraSettings`: `exposure_us: int`, `gain: int`,
     `bit_depth: int`, `roi: tuple[int, int, int, int]`, `offset: int`, `usb_bandwidth: int`,
     `high_speed_mode: int`.
   - `class CaptureCamera(Protocol)`: `open(index: int) -> None`, `info() -> CameraInfo`,
     `controls() -> dict[str, dict[str, int]]` (keys `Exposure`, `Gain`, `Offset`,
     `BandWidth`, each `{"min", "max", "default"}`), `apply(settings: CameraSettings) ->
     CameraSettings` (returns what was applied), `start_stream() -> None`,
     `get_frame(timeout_ms: int) -> np.ndarray | None` (None on timeout; raises `CameraLost`
     on a library error), `dropped_frames() -> int`, `temperature_c() -> float`,
     `stop_stream() -> None`, `close() -> None`.
   - `class FakeCamera` implementing it exactly as spec D5: 1920 × 1080, Bayer `"RG"`, model
     `"Fake ASI"`, serial `"FAKE0001"`, `sdk_version` `"fake"`, 25.0 °C, `dropped_frames()`
     always 0. Frames are the ROI's size; for the acquired index *k*, pixel (*x*, *y*) of the
     frame is `((k * 7 + x + 3 * y) % 4096) << 4` (`uint16`) at bit depth 16 and
     `(k + x + y) % 256` (`uint8`) at bit depth 8. *k* counts every frame delivered since
     `open`, starting at 0, and is **not** reset by `stop_stream`/`start_stream`, so a test can
     identify any frame from its pixel (0, 0). `get_frame` sleeps `max(exposure_us, 1000)` µs
     before returning a frame; when not streaming it waits `timeout_ms` and returns None.
     Constructor `FakeCamera(fail_after: int | None = None)`: after `fail_after` delivered
     frames, `get_frame` raises `CameraLost("fake camera lost")`. Fake identity values are
     module constants named `FAKE_*`.
4. `process.py`:
   - `build_arg_parser() -> argparse.ArgumentParser` with exactly the five arguments in spec
     D1's table and those defaults (from `capture_config`).
   - `main(argv: list[str]) -> int`. **First**, before anything else: keep `sys.stdout.buffer`
     as the protocol stream and set `sys.stdout = sys.stderr`, so a stray `print()` anywhere
     cannot corrupt the protocol; configure `logging` to stderr. Then parse arguments. With
     `--camera zwo`, send `fatal` with message `"ZWO camera support is not built yet"` and
     return 1 (ticket 69 replaces this). With `--camera fake`, construct `FakeCamera(
     fail_after=args.fake_fail_after)`.
   - A `CaptureProcess` class that holds all state and takes its collaborators as constructor
     arguments: `send: Callable[[dict], None]`, `camera: CaptureCamera`,
     `disk_free: Callable[[str], int]` (default in `main`:
     `lambda p: shutil.disk_usage(p).free`), `monotonic: Callable[[], float]` (default
     `time.monotonic`), the heartbeat timeout and the disk-stop bytes. **This is a requirement,
     not a style choice:** ticket 67 tests low-disk and heartbeat behaviour in-process by
     injecting `disk_free` and `monotonic`; a module that calls `shutil` or the clock directly
     cannot be tested that way.
   - Threads (spec D5): the stdin reader (decodes `sys.stdin.buffer` lines with
     `capture_protocol.decode` into a queue; end of stdin queues a `shutdown`); the main loop;
     the acquisition thread while open. `send` is one function that holds a lock, writes
     `capture_protocol.encode(msg)` to the protocol stream and flushes.
   - First message, always: `hello` with `protocol = CAPTURE_PROTOCOL_VERSION`, `pid`, and
     `cameras` (`["Fake ASI"]` for the fake).
   - Commands handled here: `open` (opens, applies the defaults `ZWO_EXPOSURE_US_DEFAULT`,
     `ZWO_GAIN_DEFAULT`, `ZWO_BIT_DEPTH_DEFAULT`, full-sensor ROI, keep 1 in 1, starts the
     stream, logs `camera opened: <model> <serial>` at INFO, replies with the D2 `open` fields;
     a second `open` while open replies the same fields again), `close`, `set` (snaps any ROI
     with `snap_roi`; while streaming stops and restarts the stream around the change and
     increments the settings generation; replies `settings` as applied; sends one `event`
     `settings_changed` per changed field, detail `"<field>: <old> -> <new>"`, with an ROI
     written as `x,y,width,height`), `preview` (stores mode and brightness; allowed before
     `open`), `heartbeat` (no reply), `shutdown` (replies `ok`, closes the camera, returns 0).
     `close` and `set` before `open` reply `error` `not_open`. A line that fails to decode gets
     `error` `bad_request` with `reply_to` = its `id` if one could be read, else `-1`.
     `start_recording`, `stop_recording`, `photo` and `take_darks` reply `error` `bad_request`
     with message `"not implemented"` until tickets 66 and 68.
   - While open: `status` every `CAPTURE_STATUS_INTERVAL_S` with every D2 field
     (`recording` false, `segment` −1, `frames_written` 0, `frames_dropped_queue` 0,
     `bytes_per_hour` from `estimate_bytes_per_hour`, `disk_free_bytes` of the process's working
     directory until a recording folder is known), and `preview` at most
     `CAPTURE_PREVIEW_MAX_FPS`: latest frame → `debayer_for_display` → `downscale_for_display`
     to `CAPTURE_PREVIEW_MAX_WIDTH_PX` → `stretch_preview` → `cv2.cvtColor(RGB2BGR)` (OpenCV's
     encoder expects BGR) → `cv2.imencode(".jpg", quality CAPTURE_PREVIEW_JPEG_QUALITY)` →
     base64 ASCII, with `width`, `height` of the JPEG, `roi`, `stretch_mode`, `brightness`.
   - Heartbeat: any received message counts. More than the timeout without one: close the
     camera and return 0. (Ticket 67 adds closing an open recording first.)

### Tests

5. `tests/capture_child.py` (new, a helper module, not a test file): `class CaptureChild` that
   starts `[sys.executable, "-m", "rbl.main", "--capture-process", "--camera", "fake", *extra]`
   with `env["PYTHONPATH"]` = the repo's `src`, `cwd` = a given temp folder and binary pipes; a
   reader thread putting decoded stdout lines on a queue and another collecting stderr lines;
   `send(msg)`, `request(msg, timeout=30) -> dict` (waits for the `ok`/`error` whose `reply_to`
   matches), `wait_for(type, timeout=30, predicate=None) -> dict`, `kill()`, and
   `wait_exit(timeout) -> int`. Every test uses it through a fixture that kills the child in
   teardown. Later tickets reuse it; keep it general.

## Acceptance criteria

`tests/test_capture_process.py` (new) unless noted. Generous timeouts (30 s); never assert on
speed.

- [ ] `test_hello_first_and_stdout_is_only_protocol`: the first stdout line decodes to `hello`
  passing `check_hello`; after `open`, `set`, `preview` and 3 s of status/preview traffic,
  every stdout line decoded without `ProtocolError`; the stderr lines contain
  `camera opened: Fake ASI FAKE0001`.
- [ ] `test_no_qt_is_loaded`: run the child with `-X importtime` added before `-m`, send
  `shutdown`; its stderr contains no line mentioning `PySide6`, and it exits 0.
- [ ] `test_open_status_and_preview`: `open` replies `model == "Fake ASI"`, `serial ==
  "FAKE0001"`, `sensor_width == 1920`, `bayer_pattern == "RG"`, `controls` with the four keys,
  and `settings` with the defaults; a `status` arrives with every D2 field; a `preview`
  arrives whose base64 JPEG decodes (`cv2.imdecode`) to exactly its advertised `width` ×
  `height`, at most 960 wide.
- [ ] `test_set_snaps_and_reports_changes`: `set` before `open` → `error` `not_open`; after
  `open`, `set` with `roi` `{x: 1, y: 1, width: 101, height: 51}` and `exposure_us: 5000`
  replies `settings.roi == {x: 0, y: 0, width: 96, height: 50}` and `exposure_us == 5000`, and
  `event` `settings_changed` arrives with detail `"exposure_us: 20000 -> 5000"` and
  `"roi: 0,0,1920,1080 -> 0,0,96,50"`; the next `preview` has `roi` width 96.
- [ ] `test_heartbeat_keeps_alive_and_its_loss_ends_the_process`: with
  `--heartbeat-timeout-s 2`, sending `heartbeat` every 0.5 s keeps the child running for 5 s;
  then, with stdin kept open and no more messages, it exits with code 0 within 10 s. A second
  child whose stdin is closed exits 0 within 10 s. `shutdown` replies `ok` and the child exits
  0 within 10 s.
- [ ] `test_capture_import_rules` (in `tests/test_capture_imports.py`) (AST scan of source files, no
  imports executed): no file under `src/rbl/capture/` and none of `ser_format.py`,
  `fits_format.py`, `capture_protocol.py`, `preview_stretch.py` imports `PySide6` or any
  `rbl.gui`, `rbl.services` or `rbl.state` module; and no file under `src/rbl` outside
  `src/rbl/capture/` imports `rbl.capture` except `src/rbl/main.py`. Plus a test that a
  violating file written into `tmp_path` is reported (so the scan cannot pass vacuously).

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile

`rbl.capture` is a soft type-gate prefix until ticket 77; the soft error count must not rise
above `tools/mypy_ratchet.txt`. Annotate everything now, because ticket 77 makes it hard.

## Out of scope

- Recording, segments, the frame log (ticket 66); recording endings (67); photos and Dark
  Frames (68); the real `zwoasi` camera (69); anything in the application (70 onward).
- Reattaching to a running Capture Process, or a second executable (ADR 0008).

## Comments
