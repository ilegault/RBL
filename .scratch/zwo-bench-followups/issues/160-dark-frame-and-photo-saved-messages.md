# 160: Take Darks and Take Photo say what they saved, on screen and in rbl.log

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/zwo-bench-followups/spec.md` (D4; user stories 4-8)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md`, `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/services/session_recorder.py` (`take_darks`, `take_photo`, `_announce`, `_set_camera_message`, `_on_zwo_reply` including its `"files"` branch, and `state()`'s `camera_message` keys), `src/rbl/services/capture_client.py` (`take_darks`, `photo`, `_handle_reply`: an `error` reply is logged through `_fail` and then also emitted on `reply`), `src/rbl/capture/process.py` (`_handle_take_darks` and the still-job writer: the darks `ok` reply carries `file`, an absolute path; a write failure replies `error` with code `camera_error`), `tests/test_session_recorder_zwo.py` (its `capture_client` fixture and test_zwo_photo_writes_fits_and_sidecar, for redirecting `_logs_dir` and `paths.ZWO_DARKS_DIR` and starting the fake child).

## Why

On the bench, three Dark Frames were saved, but nothing appeared on screen and nothing reached `rbl.log`. The `darks_saved` event only reaches `events.csv`, which exists only inside a session. The operator could not tell that it worked, or where the files went.

## What to build

1. `session_recorder.py`: add `_log = logging.getLogger(__name__)` if the module has no logger.
2. `take_darks`: after its existing guards pass, call `self._set_camera_message(f"Taking {frames} Dark Frames…", "neutral")` before sending. When the returned id is `>= 0`, add it to a new `self._pending_darks_ids: set[int]`.
3. `_on_zwo_reply`: first, if `reply_to` is in `_pending_darks_ids`, remove it and handle the reply, then return. For `ok`: `self._announce(f"Dark Frame saved: {msg['file']}", "ok")` and `_log.info("Dark Frame saved: %s", msg["file"])`. For `error`: `self._announce(f"Dark Frames not saved: {msg.get('message', '')}", "warn")` and `_log.warning("Dark Frames not saved: %s", msg.get("message", ""))`.
4. In the existing `"files"` branch (photos), after the sidecars are written: `self._announce("Photo saved: " + ", ".join(os.path.basename(f) for f in files), "ok")` and `_log.info("Photo saved: %s", ", ".join(files))` (full paths in the log).
5. `events.csv` keeps receiving `zwo_darks_saved` and `photo` exactly as now.
6. Update the module docstring's camera-message paragraph with the three new messages.

## Acceptance criteria

- [ ] Test `test_take_darks_announces_progress_then_the_saved_path` in `tests/test_session_recorder_zwo.py` (fake child; darks dir redirected to `tmp_path`): right after `rec.take_darks(2)`, `state()["camera_message"] == "Taking 2 Dark Frames…"`. Then `wait_until` the message starts with `Dark Frame saved: `. The text after the prefix is an existing `.fits` file under the redirected darks dir, `camera_message_level == "ok"`, and `caplog` holds an INFO record whose message is the same text.
- [ ] Test `test_dark_frames_that_cannot_be_written_say_why` in `tests/test_session_recorder_zwo.py`: create `tmp_path/"blocker"` as a regular file and redirect the darks dir to `tmp_path/"blocker"/"darks"`, so the Capture Process cannot create its folder. After `rec.take_darks(1)`, `wait_until` `camera_message` starts with `Dark Frames not saved: `, the level is `"warn"`, and `caplog` holds a WARNING with that text.
- [ ] Test `test_take_photo_announces_the_saved_file_names` in `tests/test_session_recorder_zwo.py`: camera open, no session, `rec.take_photo(1)`. `wait_until` `camera_message` starts with `Photo saved: `, the named file exists in the photos folder, and `caplog` has an INFO `Photo saved: ` record that contains the full path.
- [ ] (no test: CI's full suite) No test is deleted; the existing darks, photo and session tests in `tests/test_session_recorder_zwo.py`, `tests/test_zwo_controls.py` and `tests/test_capture_process.py` pass unchanged.

**What tests may fake:** the camera (the fake Capture Process child, as the existing tests use). The recorder, the client and the files on disk are real, in `tmp_path`.

**Out of scope:** the Take Darks confirm dialog; the Dark Frame file name or folder layout; a "show in folder" button; USB camera photos.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_session_recorder_zwo.py
```

Run pytest on the touched test files only (implement-ticket-parallel, section 4). CI runs the full suite on the PR.

## Comments

