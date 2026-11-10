# 70: `CaptureClient`: the application's one owner of the Capture Process

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 65, 66

**Spec:** `.scratch/zwo-capture/spec.md`, sections D9, D1 (`capture_command_line`), D2,
"Testing Decisions" → Seam B (`test_capture_client.py`)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decisions 1, 2, 5),
`docs/adr/0003-commanded-and-confirmed-cup-position.md`,
`docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/hardware/capture_protocol.py` (63); `src/rbl/config/paths.py`
`zwo_sdk_dll_path` (64); `src/rbl/capture/process.py` (65) for what the child sends;
`src/rbl/snapshots.py` `CupActuationState`; `tests/test_picoammeter_worker.py` `_wait_until`
(the Qt-wait pattern); `AGENTS.md` §4 (threading contract).

## What to build

### `src/rbl/services/capture_client.py` (new)

1. Module docstring with `WHY THIS EXISTS`: one owner of the child, the GUI never touches a full
   frame, the pipe is the contract (ADR 0008).
2. Module-level pure functions:
   - `capture_command_line(frozen: bool, executable: str, args: list[str]) -> list[str]`
     exactly as spec D1.
   - `capture_child_env(frozen: bool, rbl_parent: str, env: Mapping[str, str]) ->
     dict[str, str]`: a copy of `env`; when not frozen, `PYTHONPATH` is `rbl_parent` prepended
     (with `os.pathsep`) to any existing value. **A requirement:** CI never pip-installs the
     package (it runs from `src` via `pytest.ini`'s `pythonpath`), so without this the child
     cannot import `rbl` there; in development it also makes the child independent of the
     working directory. `CaptureClient` passes `rbl_parent =
     str(Path(rbl.__file__).resolve().parent.parent)`.
3. `class CaptureClient(QObject)` with exactly the signals of spec D9 plus
   `reply = Signal(int, dict)`. It runs the child with `QProcess`, the command line from
   `capture_command_line(getattr(sys, "frozen", False), sys.executable, args)` and a
   `QProcessEnvironment` built from `capture_child_env(...)`. stdout is read on
   `readyReadStandardOutput`, split on `\n` (keeping a partial last line for the next read),
   each line decoded with `capture_protocol.decode`; stderr lines go to
   `logging.getLogger("rbl.capture")` at INFO.
4. Message → signal: the first message must pass `check_hello`, else send `shutdown`, emit
   `error(<message>)` and never open the camera; `hello` → `hello(dict)`; `ok`/`error` →
   `reply(id, msg)` and, for the `open` reply, `opened(dict)`, for the `close` reply,
   `closed()`; refusals also emit `error(f"{code}: {message}")`; `status` → `status(dict)`;
   `preview` → decode the base64 JPEG with `QImage.fromData` → `preview(image, fields)`
   (`fields` = the message without `jpeg_b64`); `event` → `event(event, detail)`; `fatal` →
   `error(message)`; a decode failure → `error(str(exc))`; process finished → `exited(code)`,
   and when it was not asked to shut down, `error(f"Capture Process exited unexpectedly
   ({code})")`.
5. Public methods of spec D9. Each command method builds the command with its
   `capture_protocol` dataclass, assigns the next integer id, writes `encode(...)` to the
   child's stdin and returns the id. Calling a command method when not running emits `error`
   and returns −1.
   - `start(camera: str = "zwo")`: for `"zwo"`, computes the DLL path with
     `paths.zwo_sdk_dll_path(frozen, getattr(sys, "_MEIPASS", None), <repo root>, os.environ)`;
     if the file does not exist, emits `error(f"ZWO SDK library not found: {path}")` and does
     **not** launch. For `"fake"`, passes `--camera fake` and no `--sdk-dll` (so CI needs no
     DLL). Starts the heartbeat `QTimer` at `CAPTURE_HEARTBEAT_INTERVAL_S`.
   - `is_running()`, `is_open()` (true between the `open` reply and `closed`/exit/
     `camera_lost` event).
   - `on_cup_actuation_state(state: CupActuationState)`: computes
     `cup_position_label(state.connected, state.stale, state.confirmed)` and sends `beamline`
     only when the label differs from the last one sent, returning the command id when it
     sent and `None` when it did not; also sends the current label once right after each
     `open` reply (before any state arrives that label is `"UNKNOWN"`).
   - `shutdown()`: sends `shutdown`, waits up to `CAPTURE_SHUTDOWN_WAIT_S` for exit
     (`waitForFinished`), then kills. Stops the heartbeat timer.

## Acceptance criteria

`tests/test_capture_client.py` (new) unless noted. Real child on `start("fake")`, no fake
client, no mocked `QProcess`. Waits use a helper that spins `qapp.processEvents()` until a
condition holds or 30 s pass (add it as `tests/qt_wait.py::wait_until` if none is shared yet).

- [ ] `test_capture_command_line_and_child_env` (in `tests/test_capture_command_line.py`, extend): `capture_command_line` frozen →
  `[exe, "--capture-process", *args]`, not frozen → `[exe, "-m", "rbl.main",
  "--capture-process", *args]`; `capture_child_env` prepends `rbl_parent` to an existing
  `PYTHONPATH`, sets it when absent, and leaves `env` unchanged when frozen; the input mapping
  is not mutated.
- [ ] `test_hello_open_status_preview`: `start("fake")` → `hello`; `open()` → `opened` with
  `serial == "FAKE0001"`; then at least one `status` and one `preview` whose `QImage` is not
  null and whose size equals the message's `width` × `height`.
- [ ] `test_heartbeat_keeps_the_child_alive`: the client started with the child's
  `--heartbeat-timeout-s` set to 2 (add a keyword `heartbeat_timeout_s: float | None = None` to
  `start` that passes the argument through) is still running after 5 s of processing events.
- [ ] `test_cup_position_is_sent_only_on_change` (asserted through the frame log, not by
  counting writes): open, `set` ROI 64 × 32, `start_recording` into `tmp_path`, feed
  `CupActuationState(connected=True, confirmed=CupPosition.IN)` three times then
  `confirmed=CupPosition.OUT` and then `stale=True`; stop; `frames.csv`'s `cup_position`
  column, with consecutive duplicates collapsed, is a subsequence of
  `["UNKNOWN", "IN", "OUT", "UNKNOWN"]` that ends `["IN", "OUT", "UNKNOWN"]`. And the five
  `on_cup_actuation_state` calls returned, in order: an id, `None`, `None`, an id, an id.
- [ ] `test_shutdown_and_unexpected_exit`: `shutdown()` returns within
  `CAPTURE_SHUTDOWN_WAIT_S + 2` s and `exited(0)` fired with no `error`. A second client whose
  child is killed from outside (`os.kill` with the pid from `hello`) emits `exited` and an
  `error` containing `"exited unexpectedly"`.
- [ ] `test_missing_dll_launches_nothing`: with `RBL_ZWO_SDK_DLL` set (monkeypatch) to a
  non-existent path, `start("zwo")` emits `error("ZWO SDK library not found: <path>")` and
  `is_running()` is False.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile

Soft type-gate count must not rise above `tools/mypy_ratchet.txt`. No `_`-prefixed attribute
access in the new test files (`tools/private_access_ratchet.txt` must not rise).

## Out of scope

- `SessionRecorder`, `MainWindow` and the panels (tickets 71-74).
- Reattaching to a child left by a previous run (ADR 0008 decision 5).

## Comments

### 2026-10-27 (implementing agent): blocked on ticket 66

Built `CaptureClient`, `capture_command_line`, `capture_child_env`, `tests/qt_wait.py`, and all
tests. 6 of 7 new tests pass against the real fake-camera child; ruff, check_tests_first,
check_layers and the type gate are clean.

`test_cup_position_is_sent_only_on_change` FAILS and cannot be fixed on this ticket:

    tests\test_capture_client.py:97: assert wait_until(lambda: len(list(tmp_path.rglob("frames.csv"))) > 0)
    AssertionError

Cause: `src/rbl/capture/process.py` (ticket 65) answers `start_recording` with
`error bad_request "not implemented"`, so no `frames.csv` is ever written. Recording and the
frame log are ticket 66. This ticket's `Blocked by:` names only 65, but its acceptance test
asserts through the frame log, so it needs 66 as well.

Decision needed from a human: add 66 to this ticket's `Blocked by:` (and rerun after 66 lands,
the test needs no change), or reword the criterion to something 65 can satisfy. I did not mute,
weaken or skip the test (ADR 0001).

### 2026-10-28 (implementing agent): unblocked, done

66 landed (PR 124). Added 66 to `Blocked by:` as proposed above. Merged master into the branch and
reran: the one failing test then failed on a different cause, a harness defect. The frame log opens
with `#` comment lines (spec, Frame log), so `csv.DictReader` took the first comment as its header
row and `cup_position` was a `KeyError`. The test now skips `#` lines before parsing. Every
assertion is unchanged: the collapsed column must still end `["IN", "OUT", "UNKNOWN"]` and be a
subsequence of `["UNKNOWN", "IN", "OUT", "UNKNOWN"]`, and the five return values must still be
`[id, None, None, id, id]`, so the test still fails if the client sends the wrong labels.
