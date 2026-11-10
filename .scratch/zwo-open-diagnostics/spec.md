# Spec: ZWO Open fails loudly — camera message line, Open-sequence logging, hello and open timeouts

Status: ready-for-agent
Date: 2026-11-09
Related: `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decisions 1, 3, 4, 5),
`docs/adr/0001-tests-first-and-no-muted-failures.md`, `CONTEXT.md` ("Camera": ZWO camera,
Capture Process, Preview). Must land around set 8 (`.scratch/camera-layout-and-draft-roi/`),
see D14.

> ADR 0001 is binding on all test work here: a failing test is fixed or escalated, never muted.

## Problem Statement

On the lab PC, clicking **Open** with the ZWO camera selected did nothing visible, and nothing
explained why. Reading the code shows every ZWO failure is thrown away:

- `CaptureClient.error` (`src/rbl/services/capture_client.py`) has **no subscriber** anywhere in
  `src/rbl`. A missing DLL, a child that fails to start, an `error` reply to `open`, a `fatal`
  from the child and an unexpected exit are all emitted into nothing, and none is logged.
- `SessionRecorder.status` (`src/rbl/services/session_recorder.py`, `Signal(str, str)`) also has
  **no subscriber**, so its warnings ("Cannot start ZWO video: …", "Stop video before switching
  cameras", "Camera lost …") are lost on both the USB and the ZWO path.
- `CaptureClient.start` checks for `ASICamera2.dll` and, if it is missing, emits `error` and
  returns **before any child is started**. With the DLL absent (the development checkout's
  `vendor/zwo/` holds only `README.md`; `dist/RBL/` has no `ASICamera2.dll`), Open does
  exactly what was seen: nothing, and no log line.
- When the child's first line is `fatal` (sent by `rbl.capture.process.main` before `hello`
  when the DLL is missing or `zwoasi.init` / `zwoasi.list_cameras` raises),
  `CaptureClient._handle_line` runs `check_hello` on it and replaces the child's reason with
  "expected hello, got 'fatal'".
- The child's stderr is copied into `rbl.log` at INFO regardless of its level, unprefixed, and
  split per read chunk rather than per line.
- A hello list of `cameras: []` (no camera on USB) still leads to `open(0)` and whatever the SDK
  raises.
- Nothing bounds the wait for `hello` or for the reply to `open`.

## Solution

1. **Every failure is logged and shown.** `CaptureClient` writes each error it emits to
   `rbl.log`; `SessionRecorder` turns capture errors and its own `status` messages into one
   **camera message** held in its `state()`; both camera panels draw it on a line by Open.
2. **The Open sequence narrates itself** in `rbl.log` at INFO, and on screen as stages:
   Starting Capture Process… → Opening camera… → Opened <model> <serial>, or the failure.
3. **The child's own reason survives**: a `fatal` before `hello` is passed through verbatim.
4. **No camera on USB is named**: an empty camera list in `hello` ends the attempt with
   "No ZWO camera detected on USB".
5. **Hangs end**: no `hello` within 10 s kills the child with an error; no reply to `open`
   within 15 s warns and frees the Open button, leaving the child running.
6. **Child stderr keeps its level** in `rbl.log`, prefixed `[capture pid N]`.
7. **A bench ticket** proves the three cases on the lab PC with the real camera.

## Decisions

- D1 No debug toggle and no full protocol trace. The Open-sequence lines below are always on.
  Heartbeat, `status` and `preview` traffic is never logged per message.
- D2 `CaptureClient` logs to the existing `rbl.capture` logger (`_log`). Every `error.emit(text)`
  is preceded by `_log.error("%s", text)`, done in one private helper
  `CaptureClient._fail(text: str) -> None` that both logs and emits; no `error.emit` call
  remains outside it.
- D3 Open-sequence INFO lines, logged by `CaptureClient`, in this order:
  1. `start`: the resolved DLL path, whether `RBL_ZWO_SDK_DLL` set it, and whether the file
     exists.
  2. `start`: the full child argv and, once `QProcess.started` fires, the child PID.
  3. `hello` received: protocol version and the `cameras` list.
  4. `open` sent: command id and camera index.
  5. `open` reply: `ok` with model and serial, or the `error` code and message.
  6. Child finished: exit code and exit status, and whether the exit was expected.
- D4 The missing-DLL message is exactly:
  `ZWO SDK library not found: <path>. Copy ASICamera2.dll there or set RBL_ZWO_SDK_DLL.`
- D5 A `fatal` as the first line from the child: `CaptureClient` reports `msg["message"]`
  verbatim through `_fail`, does not send `shutdown`, and marks the coming exit expected, so no
  second "Capture Process exited unexpectedly" message follows. Any other non-`hello` first
  line keeps today's behaviour (protocol error, `shutdown`).
- D6 A `hello` whose `cameras` is empty: `CaptureClient` does **not** emit `hello`, reports
  exactly `No ZWO camera detected on USB` through `_fail`, sends `shutdown`, and marks the exit
  expected. `SessionRecorder._on_zwo_hello` therefore never sees it and never sends `open`.
- D7 Hello timeout: `CAPTURE_HELLO_TIMEOUT_S = 10.0` in `src/rbl/config/capture_config.py`. A
  single-shot `QTimer` armed in `start` and stopped on the first valid `hello`. On expiry:
  `_fail("Capture Process sent no hello within 10 s")` (the number formatted from the
  constant), kill the child, exit marked expected.
- D8 Open-reply timeout: `CAPTURE_OPEN_REPLY_TIMEOUT_S = 15.0` in the same file. Armed when
  `open` is written, stopped when its reply arrives. On expiry `CaptureClient` emits a new
  signal `open_timed_out = Signal()` and logs a WARNING; it does **not** kill the child or call
  `_fail` (ADR 0008: after `hello`, only the child's events or exit end things). A reply that
  arrives later is handled as normal.
- D9 Both timeouts are constructor arguments of `CaptureClient`
  (`hello_timeout_s: float = CAPTURE_HELLO_TIMEOUT_S`,
  `open_reply_timeout_s: float = CAPTURE_OPEN_REPLY_TIMEOUT_S`). Required, not optional: tests
  must exercise both expiries in well under a second.
- D10 Child stderr is buffered and split on `\n` like stdout (a line split across two reads is
  logged once). Each complete line is parsed by a **pure module-level function**
  `child_log_level(line: str) -> int` in `capture_client.py`, so it is testable without Qt or a
  child. It returns the `logging` level named in the child's format
  (`%(asctime)s %(levelname)s [%(name)s] %(message)s`, set in `rbl.capture.process.main`) and
  `logging.WARNING` for any line that does not match (tracebacks, loader messages). The line is
  logged at that level as `[capture pid <pid>] <line>`.
- D11 `SessionRecorder` connects `CaptureClient.error` and `CaptureClient.open_timed_out` and
  keeps the **camera message** in `state()` as two keys: `camera_message: str` and
  `camera_message_level: str`, the level being one of the theme roles already used by
  `SessionRecorder.status`: `"neutral"`, `"ok"`, `"warn"`, `"fault"`. Starts as `""` /
  `"neutral"`. One private method `_set_camera_message(text, level)` sets both and emits
  `state_changed`. Every `self.status.emit(text, level)` in `SessionRecorder` also calls it
  (USB and ZWO alike); the `status` signal itself stays.
- D12 Stages, set by `SessionRecorder`:
  - `connect_zwo` with no child running: `Starting Capture Process…` / `neutral`.
  - `connect_zwo` with a child running, and `_on_zwo_hello`: `Opening camera…` / `neutral`.
  - `_on_zwo_opened`: `Opened <model> <serial>` / `ok`.
  - Capture `error`: the error text / `fault`.
  - `open_timed_out`: `No reply to open after 15 s — the camera may be stalled on USB` / `warn`
    (number formatted from the constant).
  - `disconnect_zwo`: `""` / `neutral`.
- D13 `SessionRecorder.state()` gains `zwo_opening: bool`: true from `connect_zwo` until the
  open succeeds, a capture `error`, `open_timed_out`, or the child exits. Both panels disable
  Open while it is true, so a second click cannot send a second `open`.
- D14 Ordering against set 8: the `CaptureClient` and `SessionRecorder` work touches no GUI
  file and may run in parallel with set 8. The panel work (the camera message line) is
  **blocked by set 8 tickets 90, 94 and 95**, so it lands after the reworked rows and adds its
  label into them; set 8's tickets are not edited.
- D15 The camera message line is one `QLabel` per panel, created once in the shared
  `CameraKindMixin` (`src/rbl/gui/widgets/camera_kind_mixin.py`) as `lbl_camera_message`,
  placed by each panel on the row with Open, drawn from `state()` in `_render_kind` with the
  colour of its `theme` role, word-wrapped, hidden when the text is empty. It shows under both
  the USB and the ZWO camera.
- D16 The camera message line is visible in compact mode (amends set 8's D3 list by one item;
  the panel ticket applies it after the compact visibility pass in `RecordingPanel._render`).
- D17 Test seams on the child, accepted only with `--camera fake` (argparse error otherwise),
  in `rbl.capture.process`:
  `--fake-cameras-empty` (hello carries `cameras: []`),
  `--fake-fatal MESSAGE` (sends `fatal` with MESSAGE before `hello` and exits 1),
  `--fake-no-hello` (never sends `hello`; waits for stdin to close),
  `--fake-open-delay-s SECONDS` (sleeps before replying to `open`).
  `CaptureClient.start` gains `extra_args: list[str] | None = None`, appended to the child argv,
  so `tests/test_capture_client.py` drives each case against a real child. These flags are part
  of the CLI, not of the line protocol; ADR 0008 decision 4 is unchanged.
- D18 No new ADR and no glossary change: this works inside ADR 0008, and **camera message** is
  a UI string, not a domain term.
- D19 Bench ticket, `ready-for-developer`, blocks nothing. On the lab PC with the real camera,
  click Open three ways and record what the line and `rbl.log` show:
  (1) `ASICamera2.dll` absent and `RBL_ZWO_SDK_DLL` unset → the D4 message;
  (2) DLL present, camera unplugged → `No ZWO camera detected on USB`;
  (3) DLL present, camera plugged in → `Opened ASI585MC <serial>` and a Preview.
  Case (1) also answers whether a missing DLL caused the first silent Open on the lab PC.

## Out of scope

A debug toggle or per-message protocol trace; a diagnostics button; any change to the line
protocol, its messages or `CAPTURE_PROTOCOL_VERSION`; reattaching to a Capture Process; the USB
camera's own error handling beyond routing `SessionRecorder.status` to the line; deploying the
DLL to the lab PC (bench ticket 80, unchanged); edits to set 8's tickets.
