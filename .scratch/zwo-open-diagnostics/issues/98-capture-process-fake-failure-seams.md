# 98: Capture Process: four fake-camera seams for the Open failure cases

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/zwo-open-diagnostics/spec.md` (D17)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 4: the line protocol is unchanged; these are command-line flags, not messages), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/capture/process.py` (`build_arg_parser`, its existing `--fake-fail-after`; `main`, from `parse_args` to `process.run()`; `CaptureProcess.run`, how `camera_list` becomes the hello's `cameras`); `src/rbl/capture/camera.py` (`FakeCamera.__init__`, `FakeCamera.open`); `tests/capture_child.py` (`CaptureChild`: `extra_args`, `wait_for`, `all_messages`, `raw_stdout_lines`, `stderr_lines`, `close_stdin`, `wait_exit`); `tests/test_capture_process.py` (`capture_child` fixture).

## What to build

The fake Capture Process can be told to fail the four ways the real one can on the lab PC, so
later tickets can drive `CaptureClient` against a real child for each case.

1. `build_arg_parser` gains, after `--fake-fail-after`:
   - `--fake-cameras-empty` (`store_true`): the hello carries `cameras: []`.
   - `--fake-fatal MESSAGE` (`type=str`, default `None`): send `{"type": "fatal", "message": MESSAGE}` as the first and only line, then exit 1.
   - `--fake-no-hello` (`store_true`): send nothing; block reading stdin until it closes, then exit 0.
   - `--fake-open-delay-s SECONDS` (`type=float`, default `0.0`): the fake camera sleeps this long in `open` before the reply goes out.
2. In `main`, **immediately after `parse_args` and before the `--sdk-dll` check or anything is
   sent**: if `args.camera != "fake"` and any of the four is set, call
   `parser.error("--fake-cameras-empty, --fake-fatal, --fake-no-hello and --fake-open-delay-s need --camera fake")`
   (argparse exits 2). `--fake-fail-after` is not part of this check.
3. Still in `main`, in the `fake` branch: `--fake-fatal` sends the fatal line with the existing
   `send` and returns 1 before a camera or `CaptureProcess` is built. `--fake-no-hello` reads
   `sys.stdin` (fd) until EOF and returns 0, starting no reader thread and no `CaptureProcess`.
   `--fake-cameras-empty` sets `camera_list = []` (`CaptureProcess.run` already sends a non-`None`
   list as is). The camera is `FakeCamera(fail_after=args.fake_fail_after, open_delay_s=args.fake_open_delay_s)`.
4. `FakeCamera.__init__` gains `open_delay_s: float = 0.0`; `FakeCamera.open` calls
   `time.sleep(self._open_delay_s)` first when it is above zero. Existing constructions are unchanged.

## Acceptance criteria

Tests go in `tests/test_capture_process.py` and run a real child through the `capture_child`
fixture (`extra_args=[...]`). Nothing is faked.

- [x] `test_fake_cameras_empty_sends_an_empty_hello`: the child's `hello` has `cameras == []`, and `check_hello` accepts it.
- [x] `test_fake_fatal_is_the_only_line_and_exits_1`: with `["--fake-fatal", "zwoasi.init failed: bad DLL"]`, `wait_exit` returns 1 and `all_messages == [{"type": "fatal", "message": "zwoasi.init failed: bad DLL"}]`.
- [x] `test_fake_no_hello_waits_for_stdin_to_close`: after 1.0 s the child has sent no message and `proc.poll()` is `None`; after `close_stdin()`, `wait_exit(10)` returns 0 and still no message was sent.
- [x] `test_fake_open_delay_delays_the_open_reply`: with `["--fake-open-delay-s", "1.0"]`, the `ok` reply to `open` arrives no sooner than 1.0 s after the request (measured with `time.monotonic()`).
- [x] `test_fake_flags_need_the_fake_camera`, parametrized over the four flags (with a value where the flag takes one): `extra_args=["--camera", "zwo", "--sdk-dll", str(tmp_path / "x.dll"), *flag]` exits 2, `raw_stdout_lines` is empty, and the joined `stderr_lines` contain `need --camera fake`.

## Gate

Run all four, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments

2026-11-10: Implemented four fake-camera failure seams for the Capture Process (ticket 98).
- Added `--fake-cameras-empty`, `--fake-fatal`, `--fake-no-hello`, and `--fake-open-delay-s` to `build_arg_parser`.
- Validated in `main` that all four require `--camera fake` (argparse error).
- Handled each flag in `main` fake branch: `--fake-fatal` sends fatal message and exits 1; `--fake-no-hello` reads stdin to EOF and exits 0; `--fake-cameras-empty` sets `camera_list = []`; `--fake-open-delay-s` sleeps in `FakeCamera.open`.
- Covered by 5 new acceptance tests in `tests/test_capture_process.py`:
  - `test_fake_cameras_empty_sends_an_empty_hello`
  - `test_fake_fatal_is_the_only_line_and_exits_1`
  - `test_fake_no_hello_waits_for_stdin_to_close`
  - `test_fake_open_delay_delays_the_open_reply`
  - `test_fake_flags_need_the_fake_camera` (parametrized across all four flags)
- Bench verification: Covered by ticket 104.
