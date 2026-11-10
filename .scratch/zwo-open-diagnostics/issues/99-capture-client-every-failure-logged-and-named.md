# 99: CaptureClient: every failure is logged, and the child's own reason survives

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 98

**Spec:** `.scratch/zwo-open-diagnostics/spec.md` (Problem Statement; Solution 1, 3, 4; D2, D4, D5, D6, D17)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decisions 4 and 5: the protocol is unchanged; refusals never end anything by themselves), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/services/capture_client.py` (all of it: `start`, `shutdown`, `_command`, `_write`, `_handle_line`, `_handle_reply`, `_handle_preview`, `_on_process_error`, `_on_finished`, the module docstring); `src/rbl/config/paths.py` (`zwo_sdk_dll_path`); `src/rbl/capture/process.py` (`main`: the two `fatal` sends; `CaptureProcess._refuse_not_open`); `tests/test_capture_client.py` (`client` fixture, `Spy`, `test_missing_dll_launches_nothing`).

## What to build

Nothing `CaptureClient` reports is lost any more, and the two lab-PC cases that were silent or
mislabelled get their own message.

1. **One way out for errors.** Add `CaptureClient._fail(self, text: str) -> None`:
   `_log.error("%s", text)` then `self.error.emit(text)`. Replace **every** `self.error.emit(...)`
   in the module with `self._fail(...)`, same text, so `_fail` holds the only `error.emit` call.
2. **Missing DLL message** (D4), in `start`, exactly:
   `f"ZWO SDK library not found: {dll}. Copy ASICamera2.dll there or set RBL_ZWO_SDK_DLL."`
   Still returns before any child is started.
3. **`start(..., extra_args: list[str] | None = None)`**, a new last parameter: its items are
   appended to `args` after `--heartbeat-timeout-s`, before `capture_command_line` is called.
   This is the seam the tests use with ticket 98's flags.
4. **`fatal` before `hello`** (D5). In `_handle_line`, after `proto.decode` succeeds and before
   `check_hello`: if no hello has arrived yet and `msg["type"] == "fatal"`, set
   `self._shutting_down = True` (the flag `_on_finished` reads to call an exit expected), do
   **not** write `shutdown`, call `self._fail(msg["message"])` verbatim, and return. Any other
   non-`hello` first line keeps today's path (protocol error, `shutdown`).
5. **Empty camera list** (D6). In `_handle_line`'s `hello` branch: if `msg["cameras"] == []`,
   set `self._got_hello = True`, `self._shutting_down = True`, write `proto.ShutdownCmd()`, call
   `self._fail("No ZWO camera detected on USB")`, and do **not** emit `hello`. A non-empty list
   behaves as today.
6. Update the module docstring: every error is logged to `rbl.log` through `_fail`; a pre-hello
   `fatal` is passed through verbatim; an empty camera list ends the attempt.

## Acceptance criteria

Tests go in `tests/test_capture_client.py` and drive a **real** fake child with
`client.start("fake", extra_args=[...])` (ticket 98's flags). Logging is checked with pytest's
`caplog` on logger `rbl.capture`. Nothing is faked. This is a Qt test module: add **no**
private-attribute access (`obj._x`) in tests; the ratchet in
`tests/test_layering.py::test_private_access_ratchet_does_not_exceed_stored_figure` is at its limit.

- [ ] `test_missing_dll_launches_nothing` (**rewrite in place, same name**): with `RBL_ZWO_SDK_DLL` pointing at a missing file, `spy.errors` equals exactly the D4 text, `client.is_running()` is False, and `caplog` has an `ERROR` record on `rbl.capture` whose `getMessage()` is that same text.
- [ ] `test_fatal_before_hello_is_reported_verbatim`: with `["--fake-fatal", "zwoasi.init failed: bad DLL"]`, after `spy.exits` fills, `spy.errors == ["zwoasi.init failed: bad DLL"]` (no "expected hello", no "exited unexpectedly") and `spy.hello == []`.
- [ ] `test_empty_camera_list_ends_the_attempt`: with `["--fake-cameras-empty"]`, after `spy.exits` fills, `spy.errors == ["No ZWO camera detected on USB"]`, `spy.hello == []` and `spy.exits == [0]`.
- [ ] `test_refusals_are_logged`: start fake, wait for hello, call `client.take_darks(str(tmp_path), 1)` before `open` (the child refuses with `not_open`); the emitted error text appears as an `ERROR` record on `rbl.capture`, and the child is still running.
- [ ] `test_only_fail_emits_error`: parse `src/rbl/services/capture_client.py` with `ast`; exactly one call whose function is `self.error.emit` exists, and it is inside the function `_fail`.

## Gate

Run all four, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
