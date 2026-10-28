# 71: `SessionRecorder` drives the ZWO camera: kind, settings, video, photos, darks, events

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 67, 68, 70

**Spec:** `.scratch/zwo-capture/spec.md`, sections D10, D3 ("Defaults"), D6, D7 (sidecar),
"Testing Decisions" → Seam B (`test_session_recorder_zwo.py`), "Regression"
**Binding:** `docs/adr/0004-monitoring-log-and-sessions.md` (decision 7: video starts and stops
inside a session, numbering continues), `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md`,
`docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/services/session_recorder.py` — the module docstring (the invariant:
CSV-only is first-class), `__init__`, `start`, `stop`, `take_photo`, `state`, `start_video`,
`stop_video`, `_derive_wall`, `_on_camera_error`, `_write_event`, `_write_manifest`.
`src/rbl/services/capture_client.py` (70). `src/rbl/config/zwo_settings_store.py` (64).
`tests/test_session_recorder.py` lines 30-60 (how a recorder is built in tests).

## What to build

All in `src/rbl/services/session_recorder.py`. **The USB path is unchanged: every existing
test passes untouched, and a session that never selects the ZWO camera writes a
`session.json` with exactly today's keys.**

1. Constructor gains a keyword-only `capture: CaptureClient | None = None` **after** `parent`,
   so the nine existing `SessionRecorder(fn, cam)` / `(fn, cam, parent)` calls are unchanged.
   It loads `zwo_settings_store.load_settings()`; the camera kind is the stored kind when
   `capture` is given, else always `"usb"`. New signal `zwo_preview = Signal(QImage, dict)`,
   forwarded from `capture.preview`.
2. `camera_kind() -> str`; `set_camera_kind(kind)` with `kind` in `{"usb", "zwo"}`: refused
   (status `"Stop video before switching cameras"`, `"warn"`) while any video runs, and refused
   for `"zwo"` when `capture` is None; switching to `"zwo"` closes the USB camera if open;
   switching to `"usb"` calls `disconnect_zwo()`; saves the kind to the store; emits
   `settings_changed`.
3. `connect_zwo()`: if the client is not running, `capture.start("zwo")` and send `open(0)` when
   `hello` arrives; if it is running, `open(0)` now. On the `opened` signal: remember the reply
   (identity, controls, sensor size), then `capture.apply(...)` with the stored exposure, gain,
   bit depth, keep 1 in N and ROI (the full sensor when the stored ROI is None) and
   `capture.set_preview(mode, brightness)`. `disconnect_zwo()`: ends any ZWO video run
   (step 5), then `capture.close()` and `capture.shutdown()`.
   `set_zwo_settings(**fields)` → `capture.apply(**fields)`; **the applied values come from the
   `set` reply** (after snapping), are kept for `state()`, and are saved to the store; emits
   `settings_changed`. `set_zwo_preview(mode, brightness)` → `capture.set_preview`, saved.
4. `state()` adds `camera_kind`, `zwo_open`, `zwo_recording`, `zwo_status` (the last `status`
   message's fields, or `{}`), `zwo_info` (the `open` reply, or `{}`), `zwo_settings` (the last
   applied settings, or `{}`).
5. `start_video` / `stop_video` dispatch on the kind. ZWO `start_video` refuses with status
   warnings when not recording a session, when a ZWO run is active, or when
   `not capture.is_open()`; otherwise sends `start_recording(folder, session_t0_utc=
   self._derive_wall(0.0), first_segment_index=self._zwo_next_segment,
   first_frame_index=self._zwo_next_frame)`. On its `ok` reply the run is recorded
   (`started_t_rel`, `first_segment`) and `video_started` (detail `"zwo"`) is written; on an
   `error` reply, status `"Cannot start ZWO video: <message>"` in `"warn"` and no run.
   ZWO `stop_video(detail)` sends `stop_recording` and writes `video_stopped` (detail as
   given); its reply updates `_zwo_next_segment`, `_zwo_next_frame` and the segment list and
   rewrites `session.json`. `stop()` (end of session) stops a ZWO run the same way.
6. Events and loss: `capture.event` → `_write_event(f"zwo_{event}", detail)` (which already
   writes only while a session runs). A ZWO run also ends when the child sends `event`
   `recording_stopped` with a reason other than `"stop"` (low disk, camera lost): record the
   run's end, status `f"ZWO recording stopped ({reason}) — continuing CSV only"` in `"warn"`.
   `capture.exited` while a ZWO run is active: end the run locally (no command),
   `_write_event("zwo_capture_lost", f"exit code {code}")`, status
   `"ZWO camera lost — continuing CSV only"` in `"warn"`. The CSV timer is never touched.
   (Refusals arriving on `capture.error` do **not** end a run: a refused `take_darks` is not a
   lost camera.)
7. `take_photo(burst: int = 1)` for `"zwo"` sends `photo(folder, burst)` with the folder
   `take_photo` uses today (`<session>/photos` or `logs/photos`) and returns `None`; on the
   reply, for each file writes the beamline sidecar with `dump_json` next to it, shaped as the
   USB sidecar plus `"camera": "zwo"`, appends each to the photo list, writes one `photo` event
   (detail: relative names joined with `", "`) while a session runs, and emits `photo_taken`
   with the first path. `take_darks(frames: int)` (ZWO only, refused while a ZWO run is active)
   sends `take_darks(str(paths.ZWO_DARKS_DIR), frames)`, reading `paths.ZWO_DARKS_DIR` at call
   time.
8. `_write_manifest` adds a `"zwo"` block `{"runs": [...], "segments": [...], "camera": {model,
   serial, sdk_version, bayer_pattern}}` **only when the session has had a ZWO run**. The
   `"video"` block is unchanged and keeps describing the USB camera.
9. Update the module docstring: the ZWO camera is driven through `CaptureClient`, and
   CSV-only stays first-class when the Capture Process dies.

## Acceptance criteria

`tests/test_session_recorder_zwo.py` (new), through real objects: a real `CaptureClient`
started with `start("fake")` by the test **before** `connect_zwo()` (so no DLL is needed), a
real `SessionRecorder` with `capture=client`, a `CameraSource` that is never opened, and
`rbl.services.session_recorder._logs_dir` / `paths.ZWO_DARKS_DIR` monkeypatched into
`tmp_path` as `tests/test_session_recorder.py` does. Waits spin `processEvents` (30 s cap).

- [x] `test_zwo_video_in_a_session`: kind `zwo`, connect, `set_zwo_settings(roi={"x": 0, "y": 0, "width": 64, "height": 32},
  exposure_us=10000)`, start session, `start_video`, wait 2 s, `stop_video`, stop session:
  `<session>/zwo/segment_000.ser` and `frames.csv` exist; `events.csv` contains
  `video_started`, `zwo_recording_started`, `zwo_segment_opened`, `zwo_recording_stopped` and
  `video_stopped`; `session.json` has a `zwo` block whose `camera.serial == "FAKE0001"` and
  whose `segments` lists `segment_000.ser`; `state()["zwo_settings"]["roi"]["width"] == 64`
  (from the reply). A second run in the same session writes `segment_001.ser`.
- [x] `test_zwo_photo_writes_fits_and_sidecar`: during a session, `take_photo()`; a
  `zwo_photo_000_0.fits` and `zwo_photo_000_0.json` appear in `<session>/photos`, the JSON has
  `"camera": "zwo"` and a `"beamline"` key, `events.csv` has one `photo` row, `photo_taken`
  fired with the FITS path.
- [x] `test_capture_death_keeps_csv`: during a ZWO run, kill the child from outside (pid from
  `hello`); `events.csv` gets `zwo_capture_lost`; the CSV row count keeps increasing over the
  next 3 s; `state()["zwo_recording"]` is False; status `"ZWO camera lost — continuing CSV
  only"` was emitted.
- [x] `test_switching_kind_is_refused_while_video_runs`: during a ZWO run,
  `set_camera_kind("usb")` leaves `camera_kind() == "zwo"` and emits the warn status; with no
  `capture`, `set_camera_kind("zwo")` leaves `"usb"`.
- [x] `test_settings_persist_across_recorders`: after `set_zwo_settings(gain=150)` on one
  recorder, a new recorder + client, once `opened`, shows `state()["zwo_settings"]["gain"] ==
  150` (the stored value re-applied, read back from the reply).
- [x] `test_usb_only_session_has_no_zwo_block`: a session that never selects the ZWO camera
  writes a `session.json` with no `"zwo"` key, and `state()["camera_kind"] == "usb"`.
- [x] (no test: regression, proved by the existing suite in the gate) The whole existing suite passes with no existing test edited; in
  particular `tests/test_session_recorder.py`, `tests/test_session_cup_log.py`,
  `tests/test_recording_panel.py`, `tests/test_camera_tab.py`, `tests/test_e2e_session.py`.
  

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile

Soft type-gate count must not rise above `tools/mypy_ratchet.txt`; no `_`-prefixed attribute
access in the new test file except the module-level `_logs_dir` monkeypatch target that
`tests/test_session_recorder.py` already uses (a string target, not attribute access).

## Out of scope

- Any widget (tickets 73-75) and `MainWindow` (72).
- Changes to the USB path, `CameraSource`, `VideoRecorder` or the transcoder.
- A burst control in the UI.

## Comments

Land summary (2026-10-28):
- Implemented ZWO camera orchestration in `SessionRecorder` (`src/rbl/services/session_recorder.py`):
  - Added keyword-only `capture: CaptureClient | None = None` to constructor.
  - Forwarded `capture.preview` to `zwo_preview` signal (`Signal(QImage, dict)`).
  - Implemented `camera_kind()` and `set_camera_kind()`, persisting kind to `zwo_settings_store`.
  - Implemented `connect_zwo()`, `disconnect_zwo()`, `set_zwo_settings()`, `set_zwo_preview()`, and `take_darks()`.
  - Implemented ZWO dispatch in `start_video()`, `stop_video()`, and `take_photo()`.
  - Handled child signals: `opened` (applies stored settings and preview), `event` (logged to `events.csv` with `zwo_` prefix), `exited` and unplanned `recording_stopped` (cleanly ends ZWO run while leaving CSV logging intact), and `reply` (tracks settings, segments, and photo sidecars).
  - Updated `state()` to expose `camera_kind`, `zwo_open`, `zwo_recording`, `zwo_status`, `zwo_info`, `zwo_settings`.
  - Updated `_write_manifest()` to include the `"zwo"` block only when a ZWO run occurred during the session.
  - Updated module docstring with reasoning on `CaptureClient` and CSV-first resilience.
- Created `tests/test_session_recorder_zwo.py` covering all criteria without private attribute access.
- Verified regression against existing tests and confirmed all gates pass.
