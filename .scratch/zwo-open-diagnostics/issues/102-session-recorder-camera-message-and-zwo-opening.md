# 102: SessionRecorder: one camera message and an opening flag in state()

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 100

**Spec:** `.scratch/zwo-open-diagnostics/spec.md` (Solution 1, 2; D11, D12, D13)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 7: the USB path keeps its behaviour; only where its `status` text goes changes), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/services/session_recorder.py` (`__init__`: the `self._capture.*.connect` block; `connect_zwo`, `disconnect_zwo`, `state`, `_on_zwo_hello`, `_on_zwo_opened`, `_on_zwo_exited`, and every `self.status.emit`); `src/rbl/services/capture_client.py` (`error`, `open_timed_out`, `open_reply_timeout_s` from ticket 100); `src/rbl/capture/camera.py` (`FAKE_MODEL`, `FAKE_SERIAL`); `tests/test_session_recorder_zwo.py` (`capture_client` fixture; `test_zwo_video_in_a_session` for the logs-dir and darks-dir monkeypatches).

## What to build

The recorder holds one **camera message** that says where an Open got to, or why it stopped,
for both cameras. Ticket 103 draws it.

1. `state()` gains `"camera_message": str`, `"camera_message_level": str` (one of `"neutral"`,
   `"ok"`, `"warn"`, `"fault"`) and `"zwo_opening": bool`. Initially `""`, `"neutral"`, `False`.
2. `_set_camera_message(self, text: str, level: str) -> None` sets both and emits
   `state_changed`.
3. `_announce(self, text: str, level: str) -> None`: `self.status.emit(text, level)` then
   `self._set_camera_message(text, level)`. Replace **every** `self.status.emit(...)` in the
   module with `self._announce(...)`, same text and level. The `status` signal stays.
4. In `__init__`, when a capture client is given, also connect `error` → `_on_zwo_error(text)`
   and `open_timed_out` → `_on_zwo_open_timed_out()`.
5. Stages (D12) and the opening flag (D13):
   - `connect_zwo`: if `_zwo_opening` is already True, return without doing anything (the
     recorder enforces one open at a time; a disabled button is not the lock). Otherwise set
     `_zwo_opening = True`, then, **before** calling into the client: with no child running,
     `("Starting Capture Process…", "neutral")` then today's `start("zwo")`; with a child
     running, `("Opening camera…", "neutral")` then `open(0)`. The message is set first because
     `start` can fail synchronously (missing DLL) and its fault must be the last word.
   - `_on_zwo_hello`, inside the existing `if self._zwo_open_on_hello` branch, before `open(0)`:
     `("Opening camera…", "neutral")`.
   - `_on_zwo_opened`: `_zwo_opening = False`; `(f"Opened {msg['model']} {msg['serial']}", "ok")`.
   - `_on_zwo_error(text)`: `_zwo_opening = False`, `_zwo_open_on_hello = False`; `(text, "fault")`.
   - `_on_zwo_open_timed_out()`: `_zwo_opening = False`;
     `(f"No reply to open after {self._capture.open_reply_timeout_s:g} s — the camera may be stalled on USB", "warn")`.
   - `_on_zwo_exited`: `_zwo_opening = False`, `_zwo_open_on_hello = False` (message untouched;
     the client's error carries it).
   - `disconnect_zwo`: `_zwo_opening = False`, and `("", "neutral")` as its **last** statement,
     after `close()` and `shutdown()`, so a "not running" error from `close()` is cleared too.
6. Update the module docstring: the camera message, its stages, and the opening flag.

## Acceptance criteria

Tests go in `tests/test_session_recorder_zwo.py` against a real `CaptureClient` and fake child
(ticket 98's flags through `start("fake", extra_args=[...])`, timeouts shortened only through
the constructor). Record the message history by connecting `rec.state_changed` to append
`(st["camera_message"], st["camera_message_level"])`. No new private-attribute access in tests
(the ratchet in `tests/test_layering.py` is at its limit).

- [x] `test_missing_dll_open_shows_both_stages`: client not started, `RBL_ZWO_SDK_DLL` set to a missing path, kind `"zwo"`, `connect_zwo()`; the history contains `("Starting Capture Process…", "neutral")` followed later by `(<the ticket 99 D4 text>, "fault")`; afterwards `zwo_opening` is False.
- [x] `test_open_on_a_running_child`: client started fake and hello received; right after `connect_zwo()` the state is `("Opening camera…", "neutral")` with `zwo_opening` True; once `zwo_open` is True the state is `(f"Opened {FAKE_MODEL} {FAKE_SERIAL}", "ok")` with `zwo_opening` False.
- [x] `test_open_timeout_then_late_open`: `CaptureClient(open_reply_timeout_s=0.3)` started with `["--fake-open-delay-s", "1.5"]`; after `connect_zwo()` the state reaches `("No reply to open after 0.3 s — the camera may be stalled on USB", "warn")` with `zwo_opening` False, then `zwo_open` becomes True and the message becomes the `"Opened …"` / `"ok"` pair.
- [x] `test_second_connect_while_opening_sends_no_second_open`: client started with `["--fake-open-delay-s", "1.0"]`; `connect_zwo()` twice in a row; after `zwo_open` and a further 1.5 s, the client's `opened` signal has fired exactly once.
- [x] `test_capture_errors_and_disconnect`: (a) client started with `["--fake-cameras-empty"]` and kind `"zwo"`: the state reaches `("No ZWO camera detected on USB", "fault")` and no `open` is sent (`opened` never fires); (b) on a normally opened camera, `disconnect_zwo()` leaves `("", "neutral")` and `zwo_opening` False.
- [x] `test_usb_status_becomes_the_camera_message` (in `tests/test_session_recorder.py`): a `SessionRecorder` with no capture client; `start_video()` without a session leaves `("Cannot start video: session is not recording", "warn")`. And, parsing `session_recorder.py` with `ast`, every `self.status.emit` call is inside `_announce`.

## Gate

Run all four, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
