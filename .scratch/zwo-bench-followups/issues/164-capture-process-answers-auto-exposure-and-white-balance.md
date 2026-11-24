# 164: The Capture Process answers "auto exposure" and "auto white balance" (protocol 2)

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 159 (both edit `process.py`'s `main`), 163 (the maths it calls)

**Spec:** `.scratch/zwo-bench-followups/spec.md` (D7, "Protocol version 2"; user stories 19-25, 33-34)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 4: the language-neutral contract; decision 6), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/hardware/capture_protocol.py` (module docstring, `ERROR_CODES`, `_COMMANDS`, `_NOTIFICATIONS`: only `ok` accepts extra fields; `PreviewCmd`, `TakeDarksCmd`), `src/rbl/capture/process.py` (`build_arg_parser` and its `--fake-*` flags, `main`'s "need --camera fake" check, `_dispatch_command`, `_handle_preview`, `_refuse_not_open`, `_latest_frame` / `_latest_frame_lock`, `_send_preview`), `src/rbl/capture/camera.py` (`FakeCamera`: `__init__`, `get_frame`, `FAKE_BAYER_PATTERN`, `FAKE_CONTROLS`), `src/rbl/services/capture_client.py` (`set_preview`, `take_darks`, `_command`), `src/rbl/hardware/auto_adjust.py` (ticket 163), `tests/test_capture_process.py` (the `child` fixture, `_open`), `tests/test_capture_client.py` (the `client` fixture, `Spy`, test_open_sequence_is_narrated), `tests/test_capture_config.py` (`EXPECTED`).

## What to build

1. **Protocol** (`capture_config.py`, `capture_protocol.py`):
   - `CAPTURE_PROTOCOL_VERSION = 2` (and `EXPECTED` updated);
   - `ERROR_CODES` gains `"no_frame"`;
   - the `preview` command gains optional `wb_r` and `wb_b` (finite, within `[PREVIEW_WB_MIN, PREVIEW_WB_MAX]`);
   - new commands `auto_exposure` and `auto_white_balance`, with no fields;
   - `PreviewCmd` gains `wb_r: float = PREVIEW_WB_DEFAULT` and `wb_b: float = PREVIEW_WB_DEFAULT`, both always sent;
   - new frozen dataclasses `AutoExposureCmd` and `AutoWhiteBalanceCmd`, shaped like `StopRecordingCmd`;
   - the module docstring's command list and reply list describe both, including their `ok` fields (`exposure_us`; `wb_r`, `wb_b`).
2. **Capture Process** (`process.py`):
   - `_handle_preview` stores `wb_r` and `wb_b` (default to the current values), and `_send_preview` passes them to `stretch_preview`.
   - New `_handle_auto_exposure(cmd_id)`: `_refuse_not_open` first. Then copy the latest frame under `_latest_frame_lock`. If there is none, reply `error` code `no_frame`, message `no frame has arrived since open`. Otherwise, outside the lock, compute `suggest_exposure_us(frame, applied bit_depth, applied exposure_us, controls()["Exposure"]["min"], controls()["Exposure"]["max"])` and reply `{"type": "ok", "reply_to": id, "exposure_us": <int>}`. It does **not** apply the value (requirement: the application applies it through `set`, so one code path records every exposure change).
   - New `_handle_auto_white_balance(cmd_id)`: the same refusals, then reply `ok` with `wb_r` and `wb_b` from `grey_world_gains(frame, camera_info.bayer_pattern)`. It does not apply them.
   - Wire both into `_dispatch_command`.
3. **Fake seams** (fake camera only, added to `main`'s "need --camera fake" check, and to `FakeCamera.__init__` as keyword arguments):
   - `--fake-no-frames`: `get_frame` sleeps its timeout and returns `None`;
   - `--fake-red-scale F` (float, default 1.0): after a frame is generated, the red sites of `FAKE_BAYER_PATTERN` (even rows, even columns for `"RG"`) are multiplied by F and cast back to the frame dtype.

   These seams are a requirement: without them, `no_frame` and a non-trivial white balance cannot be reached in CI.
4. **CaptureClient**: `set_preview(mode, brightness, wb_r=PREVIEW_WB_DEFAULT, wb_b=PREVIEW_WB_DEFAULT)`, plus `auto_exposure() -> int` and `auto_white_balance() -> int` (command ids, through `_command` as `take_darks` does).

## Acceptance criteria

- [ ] Test `test_protocol_two_carries_auto_commands_and_preview_balance` in `tests/test_capture_protocol.py`: `CAPTURE_PROTOCOL_VERSION == 2`; `auto_exposure`, `auto_white_balance` and a `preview` with `wb_r=2.0, wb_b=0.5` round-trip through encode and decode; a `preview` with `wb_r` 0.1, 5.0 or NaN is rejected; an `error` with code `no_frame` decodes. Also rewrite the protocol constant in `EXPECTED` in `tests/test_capture_config.py` and the `protocol 1` text in the narration test in `tests/test_capture_client.py` (test_open_sequence_is_narrated) in place, to 2.
- [ ] Test `test_auto_exposure_suggests_without_applying` in `tests/test_capture_process.py` (`child` fixture, `_open`): `auto_exposure` replies `ok` with `exposure_us == 5000`. That is a quarter of the 20 000 µs default: about 2 % of the fake 16-bit frame lies at or above 0.98 of full scale, and the value equals `suggest_exposure_us` on the fake frame for k = 0. A following `set` with `keep_one_in_n: 1` replies settings whose `exposure_us` is still 20 000.
- [ ] Test `test_auto_white_balance_reads_the_frames_colour` in `tests/test_capture_process.py`: a child started with `--fake-red-scale 0.5`, opened, answers `auto_white_balance` with `wb_r` within 0.05 of 2.0 and `wb_b` within 0.05 of 1.0.
- [ ] Test `test_auto_commands_refuse_before_open_and_before_a_frame` in `tests/test_capture_process.py`: before `open`, both commands get `error` `not_open`. A child started with `--fake-no-frames` and opened gets `error` `no_frame` for both.
- [ ] Test `test_preview_white_balance_tints_only_the_preview` in `tests/test_capture_process.py`: open, then `preview` with mode `linear`, brightness 1, `wb_r=4.0`, `wb_b=0.25`. In the next `preview` message, the decoded JPEG (`cv2.imdecode`, BGR) has a mean red at least 2× its mean blue; with `wb_r = wb_b = 1.0` the ratio is between 0.8 and 1.25. A recording made after that (as test_recording_writes_ser_frame_log_and_settings_record does) still has frames equal to the fake camera's frames.
- [ ] Test `test_client_sends_auto_commands_and_preview_balance` in `tests/test_capture_client.py` (real fake child): after open, `client.auto_exposure()` and `client.auto_white_balance()` return ids `>= 0`, and their `reply` signals carry `exposure_us` and `wb_r`/`wb_b`; `client.set_preview("auto", 1.0, 2.0, 0.5)` gets an `ok`.

**What tests may fake:** the camera (`FakeCamera` and its new seams). The child process, the pipe, the protocol and the JPEG are real.

**Out of scope:** any button or recorder change (165); applying a suggestion inside the Capture Process; the camera's own auto modes; a WB field in the `preview` notification.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_capture_protocol.py tests/test_capture_process.py tests/test_capture_client.py tests/test_capture_config.py tests/test_zwo_camera.py
```

Run pytest on the touched test files only (implement-ticket-parallel, section 4). CI runs the full suite on the PR.

## Comments

