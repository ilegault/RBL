# 63: The capture contract and its pure rules (`capture_config`, `capture_protocol`)

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/zwo-capture/spec.md`, sections D2, D3 ("ROI snapping"), D4 ("Frame log",
`cup_position_label`), D5 (`estimate_bytes_per_hour`), and "Where the code lives"
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 4: the boundary
is a language-neutral contract), `docs/adr/0003-commanded-and-confirmed-cup-position.md`,
`docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/hardware/cup_status.py` (`CupPosition`); `src/rbl/config/cup_config.py`
as the shape of a numbers-only config module; `CONTEXT.md` "Known collisions" → "Position".

## What to build

The one description of what crosses the pipe between the application and the Capture Process,
and the small rules both sides use. Both sides import this; neither keeps its own copy.

### `src/rbl/config/capture_config.py` (new) — numbers only

1. Module docstring with `WHY THIS EXISTS` (AGENTS.md invariant 3: every number has one home).
   Exactly these names and values; every later ticket imports them from here:
   ```
   CAPTURE_PROTOCOL_VERSION = 1
   CAPTURE_DISK_STOP_BYTES = 20 * 1024**3
   CAPTURE_HEARTBEAT_INTERVAL_S = 1.0
   CAPTURE_HEARTBEAT_TIMEOUT_S = 10.0
   CAPTURE_STATUS_INTERVAL_S = 1.0
   CAPTURE_PREVIEW_MAX_FPS = 5.0
   CAPTURE_PREVIEW_MAX_WIDTH_PX = 960
   CAPTURE_PREVIEW_JPEG_QUALITY = 85
   CAPTURE_PHOTO_BURST_MAX = 20
   CAPTURE_DARK_FRAMES_MAX = 100
   CAPTURE_KEEP_ONE_IN_N_MAX = 1000
   CAPTURE_SER_HEADER_REFRESH_S = 2.0
   CAPTURE_SEGMENT_MAX_BYTES = int(3.5 * 1024**3)
   CAPTURE_WRITE_QUEUE_FRAMES = 64
   CAPTURE_FRAME_TIMEOUT_MIN_MS = 1000
   CAPTURE_CAMERA_LOST_TIMEOUTS = 5
   CAPTURE_SHUTDOWN_WAIT_S = 5.0
   ZWO_EXPOSURE_US_DEFAULT = 20_000
   ZWO_GAIN_DEFAULT = 100
   ZWO_BIT_DEPTH_DEFAULT = 16
   PREVIEW_AUTO_LOW_PCT = 0.5
   PREVIEW_AUTO_HIGH_PCT = 99.5
   PREVIEW_BRIGHTNESS_MIN = 0.25
   PREVIEW_BRIGHTNESS_MAX = 4.0
   PREVIEW_BRIGHTNESS_DEFAULT = 1.0
   ```

### `src/rbl/hardware/capture_protocol.py` (new) — pure

2. Module docstring: the **authoritative** description of the protocol in spec D2 (framing,
   every command with its fields and reply fields, every notification, the seven error codes,
   and the `hello` version check), and the sentence that a compiled replacement for the
   Capture Process implements exactly what this docstring describes. Copy the D2 tables into
   it.
3. `class ProtocolError(ValueError)`. `ERROR_CODES = ("not_open", "already_recording",
   "not_recording", "recording", "low_disk", "bad_request", "camera_error")`.
   `CUP_POSITION_LABELS = ("IN", "OUT", "IN_TRANSIT", "INDETERMINATE", "UNKNOWN")`.
4. `encode(msg: dict) -> bytes`: compact UTF-8 JSON (`separators=(",", ":")`,
   `allow_nan=False`) plus `b"\n"`. It validates first and raises `ProtocolError` on an
   invalid message, so neither side can ever send one.
5. `decode(line: bytes) -> dict`: strips one trailing `\n` (and `\r`), parses, validates.
   Raises `ProtocolError` on bad UTF-8, bad JSON, a non-object, a missing or unknown `type`, a
   command without an integer `id`, a missing required field, a field of the wrong type, or a
   value out of range. **Validation is driven by one module-level table** mapping each message
   type to its fields, so adding a message is one table row. Field rules:
   - every command (`open`, `close`, `set`, `preview`, `start_recording`, `stop_recording`,
     `photo`, `take_darks`, `beamline`, `heartbeat`, `shutdown`) has `id: int`;
   - `open.camera_index` int ≥ 0;
   - `set`: at least one of `exposure_us` (int ≥ 1), `gain` (int ≥ 0), `bit_depth` (8 or 16),
     `roi` (object with int `x ≥ 0`, `y ≥ 0`, `width ≥ 1`, `height ≥ 1`), `keep_one_in_n`
     (int, 1..`CAPTURE_KEEP_ONE_IN_N_MAX`); no other keys besides `type` and `id`;
   - `preview.mode` in `("auto", "linear")`, `preview.brightness` a finite number;
   - `start_recording`: `folder` non-empty str, `session_t0_utc` str accepted by
     `datetime.fromisoformat`, `first_segment_index` and `first_frame_index` int ≥ 0;
   - `photo`: `folder` non-empty str, `burst` int 1..`CAPTURE_PHOTO_BURST_MAX`;
   - `take_darks`: `folder` non-empty str, `frames` int 1..`CAPTURE_DARK_FRAMES_MAX`;
   - `beamline`: `cup_position` in `CUP_POSITION_LABELS`, `alumina_position` str;
   - `ok`: `reply_to` int (other fields free); `error`: `reply_to` int, `code` in
     `ERROR_CODES`, `message` str;
   - notifications `hello`, `status`, `preview`, `event`, `fatal` with the fields of spec D2,
     where `status.segment` is an int and is `-1` when not recording.
   `bool` is never accepted where an int is required (`True` is an `int` in Python; reject it).
6. One frozen dataclass per command: `OpenCmd`, `CloseCmd`, `SetCmd`, `PreviewCmd`,
   `StartRecordingCmd`, `StopRecordingCmd`, `PhotoCmd`, `TakeDarksCmd`, `BeamlineCmd`,
   `HeartbeatCmd`, `ShutdownCmd`, each with `to_message(self, cmd_id: int) -> dict`; and
   `command_from_message(msg: dict) -> <the matching dataclass>` (with `id` as a field).
   `SetCmd` fields are all optional and default to `None`; `to_message` omits `None` fields.
7. `check_hello(msg: dict) -> None`: raises `ProtocolError` when `msg["type"] != "hello"` or
   `msg["protocol"] != CAPTURE_PROTOCOL_VERSION`.
8. `snap_roi(x, y, width, height, sensor_width, sensor_height) -> tuple[int, int, int, int]`
   exactly as spec D3: width down to a multiple of 8, height down to a multiple of 2, x and y
   down to even numbers, clamped inside the sensor, width ≥ 8 and height ≥ 2. Clamp order:
   snap x, y even and into `[0, sensor - min size]`; then width to at most `sensor_width - x`
   and height to at most `sensor_height - y`; then snap width/height down; then raise to the
   minimums.
9. `estimate_bytes_per_hour(width, height, bit_depth, acquired_fps, keep_one_in_n) -> float`:
   `width * height * (bit_depth / 8) * acquired_fps / keep_one_in_n * 3600`.
10. `cup_position_label(connected: bool, stale: bool, confirmed: CupPosition) -> str`:
    `"UNKNOWN"` when not connected or stale, else `confirmed.value`.

**Import rule (a requirement, enforced later by ticket 65's contract test):** both modules
import nothing from `PySide6`, `rbl.gui`, `rbl.services` or `rbl.state`. `capture_protocol`
may import `rbl.config.capture_config` and `rbl.hardware.cup_status`. This is what lets the
Qt-free Capture Process use the same validation as the application.

## Acceptance criteria

All in `tests/test_capture_protocol.py` (new) unless noted. Pure tests: no Qt, no process.

- [x] `test_every_message_round_trips`: for every command (built with its dataclass and
  `to_message(7)`) and one example of every notification, `ok` and `error`,
  `decode(encode(m)) == m`, `encode` output ends with exactly one `b"\n"` and contains no other
  newline, and `command_from_message(decode(encode(cmd.to_message(7))))` equals the dataclass
  with `id == 7`.
- [x] `test_decode_rejects` (parametrized, each case asserts `ProtocolError`): bad JSON; a JSON
  list; missing `type`; `type: "nope"`; a command with no `id`; `id: "1"`; `id: true`; `set`
  with no settings fields; `set` with an extra key; `bit_depth: 12`; `keep_one_in_n: 0` and
  `CAPTURE_KEEP_ONE_IN_N_MAX + 1`; `burst: 0` and `CAPTURE_PHOTO_BURST_MAX + 1`; `frames: 0`
  and `CAPTURE_DARK_FRAMES_MAX + 1`; `roi.width: 0`; `brightness: NaN` (raw text `NaN`);
  `cup_position: "MAYBE"`; `error.code: "oops"`; `session_t0_utc: "yesterday"`. And
  `encode` raises `ProtocolError` for the same invalid `set` it would refuse to decode.
- [x] `test_check_hello`: passes for `{"type": "hello", "protocol": CAPTURE_PROTOCOL_VERSION,
  "pid": 1, "cameras": []}`; raises for `type: "status"` and for `protocol: 2`.
- [x] `test_snap_roi` (parametrized): `(1, 1, 101, 51, 1920, 1080) → (0, 0, 96, 50)`;
  `(1900, 1070, 500, 500, 1920, 1080) → (1900, 1070, 16, 10)`; expected values are literal
  tuples in the test, never recomputed with the same formula; a
  tiny ROI `(0, 0, 3, 1, ...)` → width 8, height 2; full sensor unchanged; every result is
  inside the sensor, with even x, y, width % 8 == 0, height % 2 == 0.
- [x] `test_bytes_per_hour_and_cup_label`: `estimate_bytes_per_hour(640, 400, 16, 100.0, 1)
  == 640 * 400 * 2 * 100 * 3600`, and keep 1 in 4 is a quarter of it; `cup_position_label`
  returns each `CupPosition` member's `.value` when connected and fresh, and `"UNKNOWN"` for
  every member when disconnected and when stale.
- [x] `test_numbers_only` (in `tests/test_capture_config.py`): `capture_config.py` contains no
  `def`, `class` or `import` statement (checked with `ast`), and every public name listed in step 1 exists
  with exactly that value.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile

Both modules are under hard type-gate prefixes (`rbl.config`, `rbl.hardware`): any mypy error
fails the build. No `# type: ignore`.

## Out of scope

- Anything that sends or receives over a real pipe (tickets 65 and 70).
- `preview_stretch.py`, `paths.py` and the settings store (ticket 64).
- Any message type, field or error code not in spec D2.

## Comments
