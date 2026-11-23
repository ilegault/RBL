# 165: Auto exposure, Auto white balance and Reset colour buttons

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 160 (both edit `SessionRecorder._on_zwo_reply`), 162 (both edit the ZWO controls), 164 (the commands)

**Spec:** `.scratch/zwo-bench-followups/spec.md` (D7, last two bullets; user stories 19-27, 33)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 6), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/config/zwo_settings_store.py` (`ZwoSettings`, `_check_brightness`, `_RULES`, `validate_settings`, `save_settings`), `src/rbl/services/session_recorder.py` (`set_zwo_preview`, `set_zwo_settings`, `_on_zwo_opened` where the preview is first sent, `_on_zwo_reply` including ticket 160's darks handling, `_announce`), `src/rbl/services/capture_client.py` (`auto_exposure`, `auto_white_balance`, `set_preview` from ticket 164), `src/rbl/gui/widgets/zwo_controls.py` (row 3, `_on_preview_changed`, `_render`'s enable list), `tests/test_zwo_settings_store.py` (test_defaults, test_valid_round_trip_with_roi, the invalid-key parametrisation), `tests/test_session_recorder_zwo.py`, `tests/test_zwo_controls.py` (`recorder_and_widget`, test_preview_changes_never_touch_the_recording).

## What to build

1. `ZwoSettings` gains `wb_r: float = PREVIEW_WB_DEFAULT` and `wb_b: float = PREVIEW_WB_DEFAULT`, appended after `last_roi`. Add `_check_wb` (finite, within `[PREVIEW_WB_MIN, PREVIEW_WB_MAX]`, message in `_check_brightness`'s style) and add both keys to `_RULES`.
2. `SessionRecorder`:
   - `set_zwo_preview(mode, brightness, wb_r=None, wb_b=None)`: `None` keeps the stored value. It sends all four through `set_preview`, stores and saves them, and copies `wb_r`/`wb_b` into `_zwo_settings_applied` beside `preview_mode` and `brightness`. `_on_zwo_opened` and the `set`-reply branch that copies `preview_mode`/`brightness` do the same for WB.
   - `auto_zwo_exposure() -> int` and `auto_zwo_white_balance() -> int`: refuse with `_announce("Auto … needs the ZWO camera open", "warn")` and `-1` when not open. Otherwise send and remember the id.
   - In `_on_zwo_reply`, for those ids:
     - exposure `ok` → `set_zwo_settings(exposure_us=msg["exposure_us"])`, then `_announce(f"Auto exposure: {msg['exposure_us'] / 1000:g} ms", "ok")`;
     - white-balance `ok` → `set_zwo_preview(mode, brightness, msg["wb_r"], msg["wb_b"])`, then `_announce(f"Auto white balance: R ×{r:.2f}, B ×{b:.2f}", "ok")`;
     - `error` → `_announce(f"Auto exposure failed: {message}", "warn")` (respectively `Auto white balance failed: …`).
   - `reset_zwo_white_balance()` → `set_zwo_preview(mode, brightness, PREVIEW_WB_DEFAULT, PREVIEW_WB_DEFAULT)`.
3. `ZwoControls` row 3 (after the brightness slider): `self.btn_auto_exposure = QPushButton("Auto exposure")`, `self.btn_auto_wb = QPushButton("Auto white balance")`, `self.btn_reset_colour = QPushButton("Reset colour")`, each with a one-line tooltip. The exposure tooltip: `Measures the current frame once and sets one exposure; starts a new segment if recording.` The white-balance tooltip: `Preview only; recorded data stays raw.` All three are enabled exactly while the camera is open (recording included). Clicks call the three recorder methods.
4. Docstrings: `ZwoControls` (the buttons; one-shot, never continuous), `ZwoSettings` (WB is Preview-only).

## Acceptance criteria

- [ ] (no test: an in-place rewrite of an existing test, which CI runs) Rewrite the defaults test in `tests/test_zwo_settings_store.py` (test_defaults) in place, under the same name, to also assert `wb_r == wb_b == PREVIEW_WB_DEFAULT`. Its other assertions stay.
- [ ] Test `test_white_balance_persists_and_bad_values_fall_back` in `tests/test_zwo_settings_store.py`: `wb_r=2.0, wb_b=0.5` survive `save_settings` and `load_settings` in `tmp_path`; `wb_r` of 0.1, 5.0, `"x"` and `True` each give exactly one warning and the default.
- [ ] Test `test_auto_exposure_applies_the_suggestion_as_a_recorded_setting` in `tests/test_session_recorder_zwo.py` (fake child): open, `rec.auto_zwo_exposure()`. `wait_until` the confirmed `state()["zwo_settings"]["exposure_us"] == 5000` (the fake frame's suggestion, see ticket 164) and `camera_message == "Auto exposure: 5 ms"`. During a running ZWO video, the same call ends the segment and starts the next, as a typed exposure change does (`zwo/segment_001.ser` exists after stop, and its settings record has `exposure_us` 5000).
- [ ] Test `test_auto_white_balance_tints_the_preview_and_persists` in `tests/test_session_recorder_zwo.py`: a client started with `extra_args=["--fake-red-scale", "0.5"]`, opened, `rec.auto_zwo_white_balance()`. `wait_until` `state()["zwo_settings"]["wb_r"]` is within 0.05 of 2.0. The message starts `Auto white balance: R ×2.0`. A new `SessionRecorder` built on the same settings store reads the same `wb_r`. After `reset_zwo_white_balance()`, both are 1.0. The recorded segment's frames are unchanged (equal to the fake frames) whatever the WB.
- [ ] Test `test_auto_buttons_follow_open_and_call_the_recorder` in `tests/test_zwo_controls.py`: all three buttons are disabled before open and enabled after. `QTest.mouseClick` on Auto exposure changes the confirmed exposure to 5000; on Reset colour, `wb_r` and `wb_b` are back to 1.0. Clicking Auto exposure before open is impossible (the button is disabled) and sends nothing.
- [ ] Test `test_auto_requests_on_a_closed_camera_say_so` in `tests/test_session_recorder_zwo.py`: with the camera not open, `rec.auto_zwo_exposure()` and `rec.auto_zwo_white_balance()` return -1, and `camera_message` names the need to open the camera, at level `"warn"`.

**What tests may fake:** the camera (the fake child and its `--fake-red-scale` seam). The settings store is real, on a temp path (as the existing store tests do). The recorder, the widgets and the clicks are real.

**Out of scope:** manual red and blue sliders; any continuous auto mode; applying WB to SER, photos or Dark Frames; the Overview layout beyond row 3.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_zwo_settings_store.py tests/test_session_recorder_zwo.py tests/test_zwo_controls.py
```

Run pytest on the touched test files only (implement-ticket-parallel, section 4). CI runs the full suite on the PR.

## Comments

