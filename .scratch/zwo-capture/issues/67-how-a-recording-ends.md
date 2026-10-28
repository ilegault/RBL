# 67: How a recording ends: size limit, low disk, camera lost, heartbeat lost, shutdown, crash

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 66

**Spec:** `.scratch/zwo-capture/spec.md`, sections D4 ("Crash safety", "A segment ends"),
D5 (camera loss, heartbeat loss, low disk), D1 (arguments)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 5: if the
application disappears the Capture Process finishes cleanly and exits),
`docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/capture/process.py` (`CaptureProcess`, its injected `disk_free` and
`monotonic`), `src/rbl/capture/recording.py` (66), `tests/capture_child.py`.

## What to build

Every way a recording can end leaves every written frame readable and a settings record saying
why it ended. Each ending sends `event` `segment_closed` and `recording_stopped` with the
reason as detail, except where the process is exiting and the pipe may be gone (send them
anyway; ignore a broken pipe).

1. **Size limit.** New argument `--segment-max-bytes N` (default `CAPTURE_SEGMENT_MAX_BYTES`),
   added to `build_arg_parser`; it exists so the roll can be tested without writing 3.5 GiB.
   Not shown anywhere in the GUI. When appending the next frame would take the SER file past N
   bytes, the writer closes the segment with `closed_reason` `"size_limit"` and continues in a
   new one; recording does not stop.
2. **Low disk.** `start_recording` replies `error` `low_disk` when `disk_free(folder)` is below
   the threshold. While recording, once a second, if `disk_free(folder)` falls below it: stop
   the recording with `closed_reason` `"low_disk"`, send `event` `low_disk` (detail: free bytes)
   then `recording_stopped`. The camera stays open; Preview and status continue.
3. **Camera lost.** In the acquisition loop, `get_frame` timeout uses
   `max(2 * exposure_ms + 500, CAPTURE_FRAME_TIMEOUT_MIN_MS)`;
   `CAPTURE_CAMERA_LOST_TIMEOUTS` consecutive `None` returns, or a `CameraLost`, is camera loss:
   close any segment with `closed_reason` `"camera_lost"`, send `event` `camera_lost` (detail:
   the message), close the camera. The process stays alive; later commands that need the
   camera reply `not_open` until `open` succeeds again.
4. **Heartbeat lost.** Before closing the camera and returning 0 (ticket 65), close any segment
   with `closed_reason` `"heartbeat_lost"` and close the frame log.
5. **Shutdown and end of stdin.** Same as 4 with `closed_reason` `"shutdown"`, after replying
   `ok` to `shutdown`.
6. Update `process.py`'s module docstring with the list of endings and the sentence that a
   killed process loses at most the frames written since the last header refresh from the SER's
   frame count, never the frames before it.

## Acceptance criteria

`tests/test_capture_process.py` (Seam A, through `CaptureChild`) unless noted. ROI 64 × 32 and
exposure 10000 µs as in ticket 66. SER files are parsed with the test-side reader.

- [ ] `test_size_limit_rolls_segments`: with `--segment-max-bytes` set to header + 10 frames'
  bytes, a ~2 s recording produces several segments, each SER no larger than the limit, every
  closed one's settings record `closed_reason == "size_limit"` except the last (`"stop"`), and
  frame-log rows are continuous across them.
- [ ] `test_low_disk_refuses_to_start`: with `--disk-stop-bytes` 10**18, `start_recording` →
  `error` `low_disk` and no `zwo/segment_*.ser` exists. And, in-process (no child),
  `test_low_disk_stops_a_running_recording` (in `tests/test_capture_endings.py`): a `CaptureProcess`
  with `FakeCamera`, a list as `send`, and a `disk_free` that returns 10**12 for the first three
  calls and then 0; after recording starts, within 10 s the sent messages include `event`
  `low_disk` and `recording_stopped`, the settings record says `"low_disk"`, and `status`
  messages keep arriving afterwards.
- [ ] `test_camera_lost_leaves_a_readable_recording`: `--fake-fail-after 40`; record; `event`
  `camera_lost` arrives; the settings record says `"camera_lost"`; the SER's `FrameCount`
  equals its trailer length and the frame-log row count; a later `set` replies `not_open`; the
  child is still running.
- [ ] `test_heartbeat_loss_closes_files_and_exits`: `--heartbeat-timeout-s 2`; send heartbeats
  while recording ~2 s, then stop sending (keep stdin open); the child exits 0 within 10 s; the
  last settings record says `"heartbeat_lost"`; the SER header `FrameCount` equals the
  frame-log row count and the trailer is present. Same for `shutdown` with `"shutdown"`.
- [ ] `test_killed_process_keeps_counted_frames`: record ≥ 5 s, then `kill()` the child (no
  shutdown). The SER header `FrameCount` is > 0, the file holds at least header +
  `FrameCount` frames' bytes, and every one of those `FrameCount` frames equals the formula.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile

Soft type-gate count must not rise above `tools/mypy_ratchet.txt`.

## Out of scope

- The application's reaction to any of these (ticket 71).
- Photos and Dark Frames (68).
- Resuming a recording after camera loss without the application asking.

## Comments
