# 69: `ZwoCamera`: the real camera behind the Capture Process, over `zwoasi`

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 65

**Spec:** `.scratch/zwo-capture/spec.md`, sections D3 (no automatic anything, bit depth, ROI),
D5 ("Camera implementations"), D1 (`--sdk-dll`), D12 (`requirements.txt`)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md`,
`docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:**
- `src/rbl/capture/camera.py` (`CaptureCamera`, `CameraInfo`, `CameraSettings`, `CameraLost`,
  `FakeCamera`) and `src/rbl/capture/process.py` `main` (65).
- `camera2_test.py` at the repo root, if present: the bench script that proved `zwoasi` 0.2.0
  returns a 3840 × 2160 `uint16` frame with exposure and gain set by code.
- **The installed `zwoasi` 0.2.0 source itself** (`pip install zwoasi==0.2.0`, then open
  `zwoasi/__init__.py`). Confirm from it, before writing the stand-in: the exact names of
  `init`, `list_cameras`, `Camera`, `get_camera_property`, `get_controls`,
  `set_control_value`, `get_control_value`, `set_roi`, `set_image_type`,
  `start_video_capture`, `capture_video_frame`, `get_dropped_frames`, `stop_video_capture`,
  `close`, the `ASI_*` constants, and **what `capture_video_frame` does on a timeout** (which
  exception class and which attribute carries the ASI error code; ASI's timeout code is 11).
  Write the confirmed shape into `ZwoCamera`'s docstring.

## What to build

### `src/rbl/capture/camera.py`

1. `class ZwoCamera` implementing `CaptureCamera`. Constructor `ZwoCamera(sdk_dll: str)`.
   `import zwoasi` happens **inside** `open`, so nothing else pays for it and tests can put a
   stand-in module in `sys.modules` first. `open(index)` always calls
   `zwoasi.init(sdk_dll)` with the explicit path (never relying on a DLL found on `PATH`), then
   `zwoasi.Camera(index)`.
2. `info()`: model from the camera's `Name`, serial from the library (if 0.2.0 exposes none,
   the camera ID string the library gives, and the docstring says so), `sdk_version` from the
   library, sensor size from `MaxWidth`/`MaxHeight`, `bayer_pattern` from the integer
   `BayerPattern`: `0 → "RG"`, `1 → "BG"`, `2 → "GR"`, `3 → "GB"` (ASICamera2.h
   `ASI_BAYER_PATTERN` order).
3. `controls()`: `{name: {"min", "max", "default"}}` for `Exposure`, `Gain`, `Offset`,
   `BandWidth` from `get_controls()` (`MinValue`, `MaxValue`, `DefaultValue`).
4. `apply(settings)`: `set_control_value(ASI_EXPOSURE, exposure_us, auto=False)`,
   `set_control_value(ASI_GAIN, gain, auto=False)`,
   `set_control_value(ASI_HIGH_SPEED_MODE, 0)`; `Offset` and `BandWidth` left at the library
   defaults (read back with `get_control_value` and returned, not set); image type
   `ASI_IMG_RAW16` for bit depth 16 and `ASI_IMG_RAW8` for 8 and **no other image type ever**;
   `set_roi(start_x, start_y, width, height, bins=1, image_type=...)`. Returns the applied
   `CameraSettings` with `offset`, `usb_bandwidth` and `high_speed_mode` read back.
5. `get_frame(timeout_ms)`: `capture_video_frame(timeout=timeout_ms)`; the library's timeout
   (as confirmed above) returns `None`; any other library exception raises
   `CameraLost(str(exc))`. The returned array is the library's, unconverted.
6. `temperature_c()`: `get_control_value(ASI_TEMPERATURE)[0] / 10.0`. `dropped_frames()`:
   `get_dropped_frames()`.

### `src/rbl/capture/process.py`

7. Replace ticket 65's `"ZWO camera support is not built yet"` branch: `--camera zwo` requires
   `--sdk-dll` (argparse error otherwise). If the file does not exist, send `fatal` with message
   `f"ZWO SDK library not found: {path}"` and return 1. Otherwise construct
   `ZwoCamera(args.sdk_dll)`; `hello.cameras` is `zwoasi.list_cameras()` after `init`. An
   exception from `init` sends `fatal` with its message and returns 1.

### `requirements.txt`

8. Add `zwoasi==0.2.0`.

## Acceptance criteria

`tests/test_zwo_camera.py` (new). A stand-in module built in the test file with the confirmed
`zwoasi` shape is installed with `monkeypatch.setitem(sys.modules, "zwoasi", stand_in)`; it
records every call. No real DLL and no real camera.

- [ ] `test_open_passes_the_explicit_dll_path`: `ZwoCamera("C:/x/ASICamera2.dll").open(0)` calls
  `init` exactly once with that string before `Camera(0)`.
- [ ] `test_info_and_bayer_mapping` (parametrized over the four `BayerPattern` integers):
  `info().bayer_pattern` is `RG`, `BG`, `GR`, `GB` for 0..3, sensor size from
  `MaxWidth`/`MaxHeight`; `controls()` has the four keys with `min`/`max`/`default` taken from
  the stand-in's `MinValue`/`MaxValue`/`DefaultValue`.
- [ ] `test_apply_never_switches_on_auto_and_never_asks_for_rgb`: after `apply` at bit depth 16
  and again at 8, the recorded calls set exposure and gain with `auto=False`, set
  `HighSpeedMode` to 0, never set `Offset` or `BandWidth`, and the only image types ever passed
  are `ASI_IMG_RAW16` then `ASI_IMG_RAW8`; `set_roi` received the ROI's four values and
  `bins=1`.
- [ ] `test_timeout_is_none_and_other_errors_are_camera_lost`: the stand-in raising its timeout
  error makes `get_frame` return `None`; raising any other of its errors makes `get_frame`
  raise `CameraLost` carrying the message; a normal return is passed through as the same array
  object.
- [ ] `test_missing_dll_is_fatal` (Seam A, `tests/capture_child.py` with
  `--camera zwo --sdk-dll <tmp_path>/missing.dll` replacing `--camera fake`): the child's first
  stdout message is `fatal` with message `"ZWO SDK library not found: <that path>"`, and it
  exits with code 1.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile

mypy has `ignore_missing_imports = true`, so no stub is needed for `zwoasi`; do not add a
`# type: ignore`. Soft count must not rise above `tools/mypy_ratchet.txt`.

## Out of scope

- Bundling the DLL, `rbl.spec`, `tools/zwo_bench.py` (ticket 76).
- Any real-hardware run (bench tickets 78-80).
- Exposing `Offset`, `BandWidth`, binning, cooling or any other control.

## Comments
