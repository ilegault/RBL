# 100: CaptureClient: a missing hello and a stalled open no longer hang

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 99

**Spec:** `.scratch/zwo-open-diagnostics/spec.md` (Solution 5; D7, D8, D9)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 5: after `hello`, only the child's events or its exit end things, so the open timeout warns and never kills), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/services/capture_client.py` (`__init__`, `start`, `shutdown`, `open`, `_command`, `_handle_line`, `_handle_reply`, `_on_process_error`, `_on_finished`, `_fail` from ticket 99); `src/rbl/config/capture_config.py` (numbers only, enforced by `tests/test_capture_config.py::test_numbers_only`); `tests/test_capture_client.py` (`Spy`).

## What to build

1. `src/rbl/config/capture_config.py` gains `CAPTURE_HELLO_TIMEOUT_S = 10.0` and
   `CAPTURE_OPEN_REPLY_TIMEOUT_S = 15.0`.
2. `CaptureClient.__init__(self, parent=None, *, hello_timeout_s: float = CAPTURE_HELLO_TIMEOUT_S, open_reply_timeout_s: float = CAPTURE_OPEN_REPLY_TIMEOUT_S)`.
   Keyword-only, so `CaptureClient(self)` in `src/rbl/gui/app.py` is unchanged. These arguments
   are a **requirement**: the tests exercise both expiries in well under a second. Expose both
   as read-only properties `hello_timeout_s` and `open_reply_timeout_s`; ticket 102 formats its
   message from the second.
3. New signal `open_timed_out = Signal()`.
4. **Hello timer**: a single-shot `QTimer`, interval from `hello_timeout_s`, started in `start`
   right after `proc.start(...)` (never on the missing-DLL return). Stopped when **any** `hello`
   line arrives (including ticket 99's empty-camera hello), on a pre-hello `fatal` or protocol
   error, in `_on_process_error`, in `_on_finished`, and in `shutdown`. On expiry:
   `self._shutting_down = True`; `self._fail(f"Capture Process sent no hello within {self._hello_timeout_s:g} s")`;
   kill the child (`self._proc.kill()`). With the default that reads "within 10 s".
5. **Open-reply timer**: a single-shot `QTimer`, interval from `open_reply_timeout_s`. `open()`
   keeps the id `_command` returns; when it is ≥ 0, store it as the pending open id and start
   the timer. `_handle_reply` stops the timer when `reply_to` equals that id (`ok` or `error`).
   `_on_finished` and `shutdown` stop it too. On expiry:
   `_log.warning("No reply to open (id %d) within %g s", open_id, self._open_reply_timeout_s)`
   then `self.open_timed_out.emit()`. It does **not** call `_fail`, kill, or send anything. A
   reply that arrives later is handled exactly as today (`opened` is emitted).
6. Update the module docstring with both timeouts and why the second only warns.

## Acceptance criteria

Tests go in `tests/test_capture_client.py`, against a real fake child using ticket 98's flags
through `start("fake", extra_args=[...])`. Only the timeout lengths are shortened, through the
constructor. No new private-attribute access in tests (the ratchet is at its limit).

- [ ] `test_no_hello_times_out_and_kills_the_child`: `CaptureClient(hello_timeout_s=0.3)`, `start("fake", extra_args=["--fake-no-hello"])`; within 5 s `spy.exits` fills, `spy.errors == ["Capture Process sent no hello within 0.3 s"]` (exactly one error: no "exited unexpectedly"), and `is_running()` is False.
- [ ] `test_hello_in_time_cancels_the_hello_timeout`: `CaptureClient(hello_timeout_s=0.5)`, normal fake child; after hello, wait 1.0 s more; `is_running()` is True and `spy.errors == []`.
- [ ] `test_open_reply_timeout_warns_and_keeps_the_child`: `CaptureClient(open_reply_timeout_s=0.3)`, `start("fake", extra_args=["--fake-open-delay-s", "1.5"])`, `open()` after hello. `open_timed_out` fires exactly once while `spy.opened` is still empty; at that moment `is_running()` is True, `spy.errors == []`, and `caplog` holds a `WARNING` on `rbl.capture` containing `No reply to open`. Then `spy.opened` fills (the late reply is handled) and `open_timed_out` has still fired once.
- [ ] `test_open_reply_in_time_does_not_time_out`: `CaptureClient(open_reply_timeout_s=1.0)`, normal fake; after `opened`, wait 1.5 s; `open_timed_out` never fired.
- [ ] `test_timeout_defaults_come_from_config`: `CaptureClient()` reports `hello_timeout_s == CAPTURE_HELLO_TIMEOUT_S == 10.0` and `open_reply_timeout_s == CAPTURE_OPEN_REPLY_TIMEOUT_S == 15.0`.

## Gate

Run all four, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
