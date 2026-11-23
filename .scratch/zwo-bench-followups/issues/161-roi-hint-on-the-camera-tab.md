# 161: The Camera tab says how to make an ROI

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/zwo-bench-followups/spec.md` (D5; user stories 9-11)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md`, `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/camera_tab.py` (module docstring's ROI drawing paragraph, `_make_roi_row`, `_update_draft`, `_sync_draft_buttons`, `_discard_draft`), `src/rbl/gui/theme.py` (`NEUTRAL`), `tests/test_camera_tab.py` (the `zwo_tab` fixture, `_open_full_sensor`, `_draw_half`, test_drawing_is_off_while_recording for starting ZWO video, test_discard_and_escape_drop_the_draft).

## Why

On the bench, the operator's first instinct was to press **Apply ROI** to start an ROI. Nothing on the tab says to draw on the Preview first.

## What to build

1. `_make_roi_row`: add `self.lbl_roi_hint = QLabel("")` to the Aspect row, after **Discard**, in `theme.NEUTRAL` (the same way other secondary labels on the tab are coloured).
2. `_update_draft(st, zwo)`: also store `self._roi_locked = zwo and bool(st.get("zwo_open", False)) and bool(st.get("zwo_recording", False))`.
3. `_sync_draft_buttons`: set the hint from the current state:
   - `_roi_locked` → `ROI is locked while video records.`, shown;
   - else `_drafting` and no Draft → `Draw a box on the Preview to start an ROI.`, shown;
   - else `_drafting` and a Draft → `Drag inside to move, drag a corner to resize, then Apply ROI.`, shown;
   - otherwise hidden (`setVisible(False)`).

   Keep the three strings as module-level constants in `camera_tab.py`. Apply ROI and Discard keep their current enablement.
4. Add the hint to the ROI drawing paragraph of the module docstring.

## Acceptance criteria

- [ ] Test `test_roi_hint_says_draw_first` in `tests/test_camera_tab.py`: ZWO camera open at the full sensor with no Last ROI (`_open_full_sensor`). `tab.lbl_roi_hint.isHidden()` is False, its text is `Draw a box on the Preview to start an ROI.`, and `btn_apply_roi` is disabled.
- [ ] Test `test_roi_hint_follows_the_draft_and_the_recording_lock` in `tests/test_camera_tab.py`: after drawing with real mouse events (as `_draw_half` does), the text is `Drag inside to move, drag a corner to resize, then Apply ROI.`. After clicking Discard (`QTest.mouseClick`), it is the draw-first text again. After starting ZWO video as test_drawing_is_off_while_recording does, it is `ROI is locked while video records.`.
- [ ] Test `test_roi_hint_is_hidden_for_the_usb_camera` in `tests/test_camera_tab.py`: with the camera kind USB (or ZWO not open), `tab.lbl_roi_hint.isHidden()` is True.
- [ ] (no test: CI's full suite) No test is deleted; the existing Camera tab tests pass.

**What tests may fake:** the camera (the fake Capture Process, as the `zwo_tab` fixture does). The tab, the recorder and the mouse events are real.

**Out of scope:** changing how drawing, moving or Apply work; a tooltip tour; the Overview panel (it has no Draft).

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_camera_tab.py
```

Run pytest on the touched test files only (implement-ticket-parallel, section 4). CI runs the full suite on the PR.

## Comments

