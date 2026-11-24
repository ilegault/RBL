# 152: Tests give the Capture Process 30 s to say hello; the app keeps 10 s

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** none (single ticket from the planning session, 2026-11-20)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md`

**Read first:** `src/rbl/services/capture_client.py` (the imports from `rbl.config.capture_config`,
`CaptureClient.__init__`, `hello_timeout_s`, `_on_hello_timeout`), `src/rbl/config/capture_config.py`
(`CAPTURE_HELLO_TIMEOUT_S`), `tests/conftest.py` (the existing autouse fixtures, for style),
`tests/test_capture_client.py` (the `qapp` fixture, `test_no_hello_times_out_and_kills_the_child`).

## Why

CI on Windows / Python 3.14 failed twice on 2026-11-20 (PRs 221 and 223) in the setup of
`tests/test_zwo_controls.py`: `Capture Process sent no hello within 10 s`. The fake Capture Process is
a real child (`python -m rbl.main --capture-process`) that imports OpenCV and numpy before it sends
`hello`. On a CI runner running `-n auto` workers, that can take longer than 10 s. Every test that
starts the child through `CaptureClient()` (6 test files, plus the main window's
`CaptureClient(self)` in `src/rbl/gui/app.py`) has the same exposure. The app's 10 s limit is right for
the lab PC and does not change.

## What to build

1. `src/rbl/services/capture_client.py`, `CaptureClient.__init__`: change the parameter to
   `hello_timeout_s: float | None = None` and set
   `self._hello_timeout_s = float(CAPTURE_HELLO_TIMEOUT_S if hello_timeout_s is None else hello_timeout_s)`.
   The default **must** be read from the module-level name `CAPTURE_HELLO_TIMEOUT_S` inside
   `__init__`, not bound as the parameter default, because the test override in step 2 replaces that
   module attribute and a default bound at definition time never sees it. Leave
   `open_reply_timeout_s` and everything else in the class unchanged.
2. `tests/conftest.py`: add an autouse fixture `_capture_process_gets_ci_time_to_say_hello(monkeypatch)`
   that runs `monkeypatch.setattr("rbl.services.capture_client.CAPTURE_HELLO_TIMEOUT_S", 30.0)`, with a
   two-line comment: CI runners can take over 10 s to start the child and import OpenCV; the app's
   own limit (`capture_config.CAPTURE_HELLO_TIMEOUT_S`, 10 s) is unchanged. 30 s matches
   `tests/qt_wait.py`'s `wait_until` default, so a test never waits longer than the client would.
   Tests that pass `hello_timeout_s=` explicitly (0.3, 0.5) are unaffected.
3. `tests/test_capture_client.py`, `test_timeout_defaults_come_from_config`: rewrite it in place,
   under the same name, so that it first undoes the step 2 override for itself, with
   `monkeypatch.setattr("rbl.services.capture_client.CAPTURE_HELLO_TIMEOUT_S", CAPTURE_HELLO_TIMEOUT_S)`
   (the value imported from `rbl.config.capture_config`), and then keeps both of its assertions
   unchanged. It takes `monkeypatch` as a new argument. Its job is still to prove the app's defaults come
   from `capture_config`; without this step it fails under the step 2 fixture (`30.0 == 10.0`).

## Acceptance criteria

- [x] Test `test_tests_give_the_capture_process_30_s_to_say_hello` in `tests/test_capture_client.py`
      (takes `qapp` and `monkeypatch`; starts no child): `CaptureClient().hello_timeout_s == 30.0`;
      `rbl.config.capture_config.CAPTURE_HELLO_TIMEOUT_S == 10.0`; then after
      `monkeypatch.setattr("rbl.services.capture_client.CAPTURE_HELLO_TIMEOUT_S", 7.0)`,
      `CaptureClient().hello_timeout_s == 7.0` (the default is read at construction); and
      `CaptureClient(hello_timeout_s=0.3).hello_timeout_s == 0.3`. Call `shutdown()` on each client
      in a `finally`.
- [x] (no test: an in-place rewrite of an existing test, which CI runs) test_timeout_defaults_come_from_config
      in `tests/test_capture_client.py` is rewritten in place as step 3 says, keeping both of its
      assertions (`hello_timeout_s == CAPTURE_HELLO_TIMEOUT_S == 10.0` and
      `open_reply_timeout_s == CAPTURE_OPEN_REPLY_TIMEOUT_S == 15.0`).
- [x] (no test: CI's full suite) No test is deleted, and every other existing test passes unchanged,
      including test_no_hello_times_out_and_kills_the_child and
      test_hello_in_time_cancels_the_hello_timeout.

**What tests may fake:** nothing. The test constructs real `CaptureClient` objects and starts no
child process.

**Out of scope:** the app's 10 s limit and anything else in `capture_config.py`; making the Capture
Process send `hello` before importing OpenCV (ticket 153, a design question); `wait_until`'s default;
the `pytest.ini` timeout; `open_reply_timeout_s`; the 0.5 s limit in
test_hello_in_time_cancels_the_hello_timeout.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_capture_client.py tests/test_zwo_controls.py
```

pytest runs on the touched test files only; CI runs the full suite on the PR. Passing: every command
exits 0 and both files report no failures or errors.

## Comments

**Unblocked (2026-11-24, planner):** the 2026-11-20 attempt stopped on test_timeout_defaults_come_from_config,
which the autouse fixture makes fail. Decision: rewrite that test in place (step 3 and its criterion).
Resume from the existing branch `ticket/capture-hello-on-ci-152-tests-give-the-capture-process-30-s-to-say-hello`,
which already has steps 1-2 and the new test. Merge `origin/master` into it, add step 3, and set this
ticket's `Status:` on that branch back to `in-progress`. This flake fails CI on unrelated PRs
(ticket 179's, 2026-11-23), so it goes before other agent work.

**Done (2026-11-23):**
- Merged `origin/master` into `ticket/capture-hello-on-ci-152-tests-give-the-capture-process-30-s-to-say-hello`.
- Rewrote `test_timeout_defaults_come_from_config` in `tests/test_capture_client.py` to restore `rbl.services.capture_client.CAPTURE_HELLO_TIMEOUT_S` via `monkeypatch` before asserting both config defaults (`10.0` and `15.0`).
- Local gate green: `ruff check .`, `python scripts/check_tests_first.py`, `python tools/type_gate.py`, and `pytest` on `tests/test_capture_client.py` and `tests/test_zwo_controls.py` (35 passed).
