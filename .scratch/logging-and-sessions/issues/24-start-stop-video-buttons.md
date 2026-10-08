# 24: Start Video / Stop Video buttons, and video never starts by itself

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 23

**Spec:** `.scratch/logging-and-sessions/spec.md` (sections B2, B3)
**Binding:** `docs/adr/0004-monitoring-log-and-sessions.md` (decision 7), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/widgets/recording_panel.py` (`_chk_video`,
`_on_video_enabled_changed`, `_render`), `src/rbl/gui/camera_tab.py` (`_chk_video` and
the `set_video_enabled(True)` call near line 294), `src/rbl/services/session_recorder.py`
(`set_video_enabled`, `_video_enabled`, the `_start_video()` call in `start`),
`tests/test_recording_panel.py`.

## What to build

The contract step. Both views that bind the one `SessionRecorder` replace the `Record
video` checkbox with a button reading `Start Video` / `Stop Video` that calls
`start_video()` / `stop_video()`. It is enabled only while a session is recording and the
camera is open; when disabled its tooltip says which is missing (`Start a session first`
or `Open the camera first`). Frame rate, segment length, quality and codec stay editable
only while video is not running. Then `SessionRecorder.set_video_enabled` and
`_video_enabled` are removed and `start()` no longer starts video. The session button
text stays `● START SESSION` / `■ STOP SESSION`.

Tests may fake: the camera and `cv2` as in ticket 23. Must be real: `RecordingPanel` and
`SessionRecorder`.

## Acceptance criteria

- [ ] `RecordingPanel`: before a session the video button is disabled with tooltip
      `Start a session first`; during a session with the fake camera closed, tooltip
      `Open the camera first`; with it open, enabled and reading `Start Video`.
- [ ] Clicking it during a session makes `recorder.state()` report video running and the
      button read `Stop Video`; clicking again stops it. Same test for the Camera tab.
- [ ] Starting a session never creates a video segment until the button is pressed.
- [ ] `grep -rn "set_video_enabled\|_video_enabled" src/` returns nothing.
- [ ] Tests in `tests/test_recording_panel.py` (or others) that used the checkbox or
      `set_video_enabled` are rewritten in place under the same names to use the button.
      No test function is deleted.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
