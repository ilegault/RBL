# 23: Video can start and stop several times in one session without overwriting

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 22

**Spec:** `.scratch/logging-and-sessions/spec.md` (section B2)
**Binding:** `docs/adr/0004-monitoring-log-and-sessions.md` (decision 7), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/services/video_recorder.py` (`__init__` where `_segment_index = 0`,
`start` where `frames.csv` is opened with mode `"w"`, the segment naming
`video_{index:03d}`), `src/rbl/services/session_recorder.py` (`_start_video`, `stop`,
`_on_segment_closed`, `_on_camera_closed`, `_write_manifest`),
`tests/test_video_recorder.py` (`_patch_cv2`), `tests/test_session_recorder.py`.

## What to build

The recorder half of mid-session video (expand step; the UI and removing
`set_video_enabled` are ticket 24). Today a second video run would restart at
`video_000` and reopen `frames.csv` with `"w"`, destroying the first.

- `VideoRecorder.__init__` gains `first_segment_index: int = 0`; `start()` opens
  `frames.csv` in append mode and writes the header only when the file is new or empty.
  **Both are requirements**: they are what stops a second run overwriting the first.
- `SessionRecorder.start_video() -> bool`: refuses (returns `False`, emits `status` with
  the reason) when not recording, camera not open, or video already running; otherwise
  builds the recorder as `_start_video` does, passing the next unused segment index.
  `stop_video()`: stops it and disconnects `frame_ready`. `stop()` calls `stop_video()`.
- `events.csv` gets `video_started` and `video_stopped` rows (detail `camera_closed` when
  `_on_camera_closed` ends a run; the session keeps running).
- `session.json` `video` gains `"runs": [{"started_t_rel", "stopped_t_rel",
  "first_segment", "last_segment"}]`.
- `set_video_enabled` and the start-with-session behaviour remain for now.

Tests may fake: `cv2` (use `_patch_cv2` from `tests/test_video_recorder.py`) and the
camera (an open fake camera QObject with `frame_ready`). Must be real: `SessionRecorder`,
`VideoRecorder`, and the files under `tmp_path`.

## Acceptance criteria

- [x] `VideoRecorder` constructed with `first_segment_index=3` names its first segment
      `video_003.*`.
- [x] Two `VideoRecorder` runs in one folder: `frames.csv` has one header row and the
      rows of both runs.
- [x] Session test: start session, `start_video()`, offer frames, `stop_video()`,
      `start_video()`, offer frames, stop session. No segment file name repeats, the first
      run's segment file still exists, `events.csv` has two `video_started` and two
      `video_stopped`, and `session.json` `video.runs` has two entries.
- [x] `start_video()` before `start()` returns `False` and creates no file; called twice
      while running, the second returns `False`.
- [x] Every existing test in `tests/test_video_recorder.py` and
      `tests/test_session_recorder.py` passes unchanged.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments

Implemented 2026-10-07:
- `VideoRecorder.__init__` gains `first_segment_index: int = 0`; `start()` opens `frames.csv` in append mode, writing the header only when the file is new or empty.
- `SessionRecorder.start_video() -> bool`: guards against not recording, video already running, and closed camera; constructs `VideoRecorder` with `first_segment_index` pointing to next unused segment index.
- `SessionRecorder.stop_video(detail="")`: disconnects `frame_ready`, stops `VideoRecorder`, sets `stopped_t_rel` and `last_segment` on the run, increments `_next_segment_index`, and writes `video_stopped` event.
- `events.csv` logs `video_started` and `video_stopped` (detail `camera_closed` when `_on_camera_closed` ends a run).
- `session.json` `video` records `"runs": [{"started_t_rel", "stopped_t_rel", "first_segment", "last_segment"}]`.
- Tests in `tests/test_video_recorder.py` (`test_first_segment_index`, `test_two_video_runs_append_frames_csv`) and `tests/test_session_recorder.py` (`test_start_video_guards_and_refusals`, `test_session_video_start_stop_multiple_runs_no_overwrite`, `test_camera_closed_ends_video_run_session_continues`, `test_session_start_with_video_enabled_backwards_compat`) cover all criteria. All 2325 tests pass.
