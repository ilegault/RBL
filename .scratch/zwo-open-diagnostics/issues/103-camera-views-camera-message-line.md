# 103: Both camera views: a camera message line by Open, and Open held while opening

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 102, camera-layout-and-draft-roi/90, camera-layout-and-draft-roi/94, camera-layout-and-draft-roi/95

**Spec:** `.scratch/zwo-open-diagnostics/spec.md` (Solution 1, 2; D13, D14, D15, D16)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 7: the USB path is otherwise unchanged), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/widgets/camera_kind_mixin.py` (`_make_preview_caption` for the construction pattern, `_render_kind`, `_toggle_zwo_camera`); `src/rbl/gui/widgets/recording_panel.py` (`_build_ui`: the row holding `_btn_open` and `_lbl_res` as set 8's ticket 89 left it; `_render`: the compact pass from set 8's ticket 90 and `compact_hidden_widgets`); `src/rbl/gui/camera_tab.py` (`_build_ui`: the `top` row holding `_btn_open`, `_lbl_res`, `_chk_crosshair` and set 8's `btn_fullscreen`; `_render`); `src/rbl/gui/theme.py` (`NEUTRAL`, `OK`, `WARN`, `FAULT`, `FS_CAPTION`); `tests/test_recording_panel.py` (`zwo_rig`, `_make_view`, `_StubRecorder`, and around line 391 how the Open button is found by its text with `findChildren(QPushButton)`).

## What to build

Under both cameras, both views show the recorder's camera message on the row with Open, in
its role's colour. While a ZWO Open is in progress, Open cannot be clicked again. Set 8's
tickets are not edited.

1. `CameraKindMixin._make_camera_message_label() -> QLabel` creates
   `self.lbl_camera_message = QLabel("")` with `setWordWrap(True)`, hidden, and returns it.
2. Each host calls it in `_build_ui` and inserts the label into the `QHBoxLayout` holding
   `_btn_open`, immediately after `_lbl_res`, with stretch 1 (`RecordingPanel` and `CameraTab`).
3. At the end of `CameraKindMixin._render_kind(st)`: text = `st.get("camera_message", "")`,
   role = `st.get("camera_message_level", "neutral")`; colour from
   `{"neutral": theme.NEUTRAL, "ok": theme.OK, "warn": theme.WARN, "fault": theme.FAULT}`;
   stylesheet `f"color: {colour}; font-size: {theme.FS_CAPTION}px;"`; set the text; visible
   exactly when the text is non-empty. Then
   `self._btn_open.setEnabled(not (zwo and st.get("zwo_opening", False)))`. Use `st.get` with
   these defaults: `_StubRecorder.state()` has none of the new keys and its tests must still pass.
4. Compact mode (D16): `lbl_camera_message` is **not** added to `compact_hidden_widgets`, so the
   compact pass in `RecordingPanel._render`, which runs after `_render_kind`, leaves its
   visibility as `_render_kind` set it.
5. Update the mixin's module docstring: the camera message line and why Open is held.

## Acceptance criteria

Parametrize over `which` in `["panel", "tab"]` with `_make_view` unless the criterion says panel
only. Find Open as the `QPushButton` whose text is `"Open"` or `"Close"`, as the existing test
near line 391 does. **No new private-attribute access** in this Qt test module: the ratchet in
`tests/test_layering.py::test_private_access_ratchet_does_not_exceed_stored_figure` is at its
limit. Use a real `CaptureClient` and fake child; shorten nothing but the fake open delay.

- [ ] `test_camera_message_sits_on_the_open_row`: `lbl_camera_message` and the Open button are in the same `QHBoxLayout` (walk the view's layouts; `layout.indexOf(widget) != -1` for both).
- [ ] `test_missing_dll_message_in_fault_colour`: a fresh `CaptureClient` not started, `RBL_ZWO_SDK_DLL` set to a missing path, kind `"zwo"`, view shown; clicking Open leaves `lbl_camera_message` visible to the view with the D4 text and `theme.FAULT` in its stylesheet, and Open enabled.
- [ ] `test_open_is_held_while_opening`: a client started with `["--fake-open-delay-s", "1.5"]` and its hello received, kind `"zwo"`; just after clicking Open the button is disabled and the line reads `Opening camera…`; once `rec.state()["zwo_open"]` is True the button is enabled, reads `Close`, and the line reads `f"Opened {FAKE_MODEL} {FAKE_SERIAL}"` with `theme.OK` in its stylesheet.
- [ ] `test_camera_message_under_usb`: kind `"usb"`; `rec.start_video()` with no session makes the line visible with `Cannot start video: session is not recording` and `theme.WARN`; Open stays enabled.
- [ ] `test_close_clears_the_camera_message`: after an opened ZWO camera, clicking Close leaves `lbl_camera_message` not visible to the view and its text empty.
- [ ] `test_camera_message_survives_compact_mode` (panel only): with `btn_compact` checked and the USB warning above shown, the line is visible to the view; after `rec.state_changed.emit()` it is still visible.

## Gate

Run all four, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
