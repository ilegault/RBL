# 93: Camera tab: Draft ROI with aspect presets, Apply and Discard

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 91, 92

**Spec:** `.scratch/camera-layout-and-draft-roi/spec.md` (Solution 6, D6, D7, D8)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 6: an ROI change starts a new Segment, so it happens only on an explicit Apply), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Rewrites in place:** `tests/test_camera_tab.py::test_drawing_a_rect_sets_the_roi` (same name; it now draws, then clicks Apply). No test is deleted.

**Read first:** `src/rbl/gui/camera_tab.py` (module docstring "ROI drawing (ticket 75)", `_build_ui`, `_on_zwo_preview_frame`, `_on_rect_drawn`, `_render` where `set_rect_drawing` is decided); `src/rbl/gui/widgets/video_view.py` (`paintEvent`, `painted_rect`, `rect_drawn`); `src/rbl/hardware/roi_geometry.py` (92); `src/rbl/services/session_recorder.py` (`swap_zwo_roi`, `state()["zwo_last_roi"]`, from 91); `src/rbl/gui/widgets/inputs.py` (`NoScrollComboBox`, `NoScrollSpinBox`); `tests/test_camera_tab.py` (`zwo_tab`).

## What to build

Drawing on the Camera tab's ZWO Preview no longer changes the ROI. It creates a **Draft
ROI**, held in sensor pixels on `CameraTab` (`self._draft: tuple[int, int, int, int] | None`,
exposed read-only as `CameraTab.draft()`), drawn as an outline, and applied only by
**Apply ROI**. Moving/resizing by drag is ticket 94, not this one.

1. **Overlay in `VideoView`.** `set_draft_fraction(f: tuple[float, float, float, float] | None)`
   and `draft_fraction()`. `paintEvent` draws it, after the frame, as a 2 px dashed outline
   in `QColor(255, 200, 0)`, mapped into `painted_rect()` at paint time (so a window resize
   needs no callback). Never burned into frames.
2. **Row** below `zwo_controls`, ZWO-only (hide it under USB in `_render`):
   `Aspect: [combo_aspect] [spin_aspect_w] : [spin_aspect_h] [btn_apply_roi "Apply ROI"] [btn_discard_roi "Discard"]`, then `addStretch(1)`.
   `combo_aspect` items, text → data: `"Free"→None`, `"1:1"→(1,1)`, `"4:3"→(4,3)`,
   `"16:9"→(16,9)`, `"Custom"→"custom"`. The two spin boxes (`NoScrollSpinBox`, 1-100,
   default 1) and the ":" label are visible only for Custom. Starts at Free every launch.
3. **Draw → Draft.** `_on_rect_drawn` maps the view rect with `view_rect_to_sensor_roi` as
   today, then sets `self._draft = fit_ratio(that, <current ratio>, <applied ROI>)` and
   sends **nothing** to the recorder. Changing the preset or a Custom value with a Draft
   present applies `fit_ratio` to it (same centre).
4. **Apply / Discard / Esc.** `btn_apply_roi` calls
   `rec.set_zwo_settings(roi={"x", "y", "width", "height"} of the Draft)` and clears the
   Draft. `btn_discard_roi` and Esc (`QShortcut(QKeySequence(Qt.Key.Key_Escape), self)` with
   `Qt.ShortcutContext.WidgetWithChildrenShortcut`) clear it. Both buttons are enabled only
   with a Draft present and drafting allowed.
5. **Last ROI seeds the Draft.** In `_render`: when the applied ROI is the full sensor,
   `zwo_last_roi` is not None, there is no Draft, and the Draft was not discarded at this
   same applied ROI, set the Draft to the Last ROI exactly. Discard records the applied ROI
   it was discarded at, so the Draft does not reappear until the applied ROI changes.
6. **When drafting is off** (not ZWO, not open, or `zwo_recording`; the existing
   `set_rect_drawing` condition): the Draft is cleared and the overlay set to None. The
   full-screen window never shows or edits a Draft. Update the module docstring's "ROI
   drawing" paragraph to describe the Draft.

## Acceptance criteria

Use `zwo_tab` (real fake Capture Process, 1920 × 1080). Emit `tab.video_view.rect_drawn`
directly to simulate a drag, as the current test does. The ROI is read from
`rec.state()["zwo_settings"]["roi"]` (the confirmed reply).

- [ ] `test_drawing_a_rect_sets_the_roi` (rewritten in place): after `rect_drawn` the
  confirmed ROI is still the full sensor 0.5 s later and `video_view.draft_fraction()` is not
  None; after `btn_apply_roi.click()` it equals `snap_roi(*view_rect_to_sensor_roi(...), 1920, 1080)`
  as the current test computes, and `tab.draft()` is None.
- [ ] `test_aspect_presets_shape_the_draft`: with `"16:9"` chosen, a drawn rect gives
  `draft()` with `w * 9 == h * 16` within ±1; switching to `"1:1"` gives a square with the
  same centre (±1 px); the W/H spin boxes are visible only while `"Custom"` is chosen; Custom
  2:1 gives `w == 2 * h` within ±1.
- [ ] `test_discard_and_escape_drop_the_draft`: each of `btn_discard_roi.click()` and
  `QTest.keyClick(tab, Qt.Key.Key_Escape)` leaves `draft()` None, `draft_fraction()` None,
  `btn_apply_roi` disabled, and the confirmed ROI unchanged.
- [ ] `test_last_roi_becomes_the_draft_at_full_sensor`: confirm ROI A with
  `rec.set_zwo_settings(roi=...)`, then `rec.swap_zwo_roi()` to full sensor: `draft()` equals
  the confirmed A exactly; `btn_apply_roi.click()` makes A the confirmed ROI again. Repeat to
  full sensor, Discard, then emit `rec.state_changed` twice: `draft()` stays None.
- [ ] `test_drawing_is_off_while_recording` (existing, unchanged) passes, and a new
  `test_recording_clears_the_draft`: with a Draft present, starting ZWO video leaves
  `draft()` None and `btn_apply_roi` disabled.

## Gate

Run all four, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
