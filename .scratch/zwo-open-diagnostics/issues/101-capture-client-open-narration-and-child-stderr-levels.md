# 101: CaptureClient: the Open sequence is narrated in rbl.log, and child stderr keeps its level

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 100

**Spec:** `.scratch/zwo-open-diagnostics/spec.md` (Solution 2, 6; D1, D3, D10)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md`, `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/services/capture_client.py` (`start`, `open`, `_on_stdout`, `_on_stderr`, `_handle_line`, `_handle_reply`, `_on_finished`, after tickets 99 and 100); `src/rbl/capture/process.py` (`main`: the `logging.basicConfig` format `%(asctime)s %(levelname)s [%(name)s] %(message)s`; `CaptureProcess.run`: the "Heartbeat timed out" warning); `src/rbl/config/paths.py` (`zwo_sdk_dll_path`: `RBL_ZWO_SDK_DLL` wins when set and non-empty).

## What to build

Reading `rbl.log` after a failed Open says how far it got. Only the Open sequence is logged:
no debug toggle, and heartbeat, `status` and `preview` traffic is never logged per message (D1).

1. **Pure helpers, module level, no Qt** (a requirement: they are tested without a child):
   - `drain_lines(buf: bytearray) -> list[bytes]`: removes and returns every complete
     `\n`-terminated line from `buf`, leaving any partial tail in it. `_on_stdout` uses it in
     place of its own loop.
   - `child_log_level(line: str) -> int`: if the line matches the child's format (a date token,
     a time token, then `DEBUG|INFO|WARNING|ERROR|CRITICAL`, then ` [`), the `logging` level of
     that name; otherwise `logging.WARNING` (tracebacks, loader messages, blank lines).
2. **Stderr by line.** `_on_stderr` appends to a new stderr `bytearray` (cleared in `start`),
   calls `drain_lines`, decodes each line as UTF-8 with `errors="replace"`, strips the line
   ending, skips blank lines, and logs
   `_log.log(child_log_level(line), "[capture pid %d] %s", self._child_pid, line)`.
   `_on_finished` first drains what is left on stderr the same way and logs a remaining partial
   tail as one line.
3. **Open-sequence INFO lines** (D3), exactly these formats, in this order:
   1. `start`, ZWO only, before the `is_file` check:
      `_log.info("ZWO SDK DLL: %s (from RBL_ZWO_SDK_DLL: %s, exists: %s)", dll, bool(os.environ.get("RBL_ZWO_SDK_DLL")), dll.is_file())`.
   2. `start`, before `proc.start`: `_log.info("Starting Capture Process: %s", argv)`. Connect
      `proc.started` to a new slot that stores `self._child_pid = proc.processId()` and logs
      `_log.info("Capture Process started, pid %d", pid)`.
   3. `_handle_line`, any `hello`: `_log.info("Capture Process hello: protocol %d, cameras %r", msg["protocol"], msg["cameras"])`.
   4. `open`, after a successful send: `_log.info("Sent open (id %d, camera index %d)", cmd_id, camera_index)`.
   5. `_handle_reply` for the pending `open`: `ok` →
      `_log.info("Open reply ok (id %d): %s %s", reply_to, msg["model"], msg["serial"])`;
      `error` → `_log.info("Open reply error (id %d): %s: %s", reply_to, msg["code"], msg["message"])`
      (the `_fail` ERROR line still follows).
   6. `_on_finished`: `_log.info("Capture Process exited: code %d, status %s, expected %s", code, status.name, self._shutting_down)`.
4. Update the module docstring: what is logged on Open and why nothing else is.

## Acceptance criteria

Tests go in `tests/test_capture_client.py`. The two pure helpers are tested with plain values;
the rest against a real fake child with `caplog` on `rbl.capture`. No new private-attribute
access in tests (the ratchet is at its limit).

- [x] `test_child_log_level`, parametrized: `"2026-11-09 20:52:01,123 INFO [rbl.capture] x"` → `logging.INFO`; the same with `DEBUG`, `WARNING`, `ERROR` → that level; `"Traceback (most recent call last):"`, `'  File "x.py", line 1'` and `""` → `logging.WARNING`.
- [x] `test_drain_lines_keeps_the_partial_tail`: `buf = bytearray(b"ab")` drains to `[]`; after `buf += b"c\nde"` it drains to `[b"abc\n"]` and `buf == bytearray(b"de")`.
- [x] `test_child_stderr_keeps_its_level_and_pid`: `start("fake", heartbeat_timeout_s=0.3)` (the child times out on heartbeats and logs a WARNING before exiting); after `spy.exits` fills, exactly one record on `rbl.capture` has `levelno == logging.WARNING`, starts with `f"[capture pid {spy.hello[0]['pid']}] "` and contains `Heartbeat timed out`.
- [x] `test_open_sequence_is_narrated`: start fake, wait for hello, `open()`, wait for `opened`, `shutdown()`. The `rbl.capture` INFO messages contain, in this order, records starting with `Starting Capture Process:`, `Capture Process started, pid `, `Capture Process hello: protocol 1, cameras ['Fake ASI']`, `Sent open (id `, `Open reply ok (id ` (ending `Fake ASI FAKE0001`), and `Capture Process exited: code 0` (ending `expected True`).
- [x] `test_dll_resolution_is_logged`: with `RBL_ZWO_SDK_DLL` set to a missing path, `start("zwo")` logs the INFO record `f"ZWO SDK DLL: {path} (from RBL_ZWO_SDK_DLL: True, exists: False)"`.
- [x] `test_streaming_is_not_logged_per_message`: start fake, open, wait for the first `preview`; record the count of `rbl.capture` records **not** starting with `[capture pid `; wait 2.0 s; the count is unchanged.

## Gate

Run all four, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments

### Completed (2026-11-10)
Implemented Open sequence narration in rbl.log and child stderr log forwarding by line:
- Added pure module-level functions `drain_lines` and `child_log_level` to `src/rbl/services/capture_client.py`.
- Updated `CaptureClient._on_stderr` and `_on_finished` to buffer and drain stderr line by line, preserving the child's log level and prefixing with `[capture pid <pid>]`.
- Added Open-sequence INFO narration lines (DLL resolution, starting process, started with PID, hello with cameras, sent open, open reply ok/error, and child exited with code/status/expected).
- Updated module docstrings explaining why Open is narrated and why streaming/heartbeat traffic is not logged per message (D1, D10).
- Added comprehensive unit tests in `tests/test_capture_client.py` covering all acceptance criteria with no new private attribute accesses.
