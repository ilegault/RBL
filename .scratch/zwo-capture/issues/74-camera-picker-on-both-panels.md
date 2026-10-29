# 74: The camera picker on the Overview panel and the Camera tab, with the ZWO Preview

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 72, 73

**Spec:** `.scratch/zwo-capture/spec.md`, section D11 (all but "ROI drawing"), D8 (the label
under the view), user stories 1, 8 and 12
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 7: the USB path
is unchanged), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/widgets/recording_panel.py` — `_build_ui`, `_toggle_camera`,
`_on_camera_opened`, `_on_camera_closed_ui`, `_on_preview_frame`, `_render`;
`src/rbl/gui/camera_tab.py` — the same methods; `src/rbl/gui/widgets/video_view.py`
`set_frame`, `clear`; `src/rbl/gui/widgets/zwo_controls.py` (73); `tests/test_recording_panel.py`
and `tests/test_camera_tab.py` (how a panel is built in tests).

## What to build

`RecordingPanel` and `CameraTab` change identically. Every step below applies to both.

1. A **Camera:** row at the very top: public `combo_camera_kind` (`NoScrollComboBox`, items
   `"USB camera"` → `"usb"`, `"ZWO camera"` → `"zwo"`), set from
   `recorder.camera_kind()` on render; a user change calls `recorder.set_camera_kind(...)`;
   when the recorder refuses, the next render puts the combo back.
2. Embed one `ZwoControls(recorder)` as the public attribute `zwo_controls`, placed where the
   USB settings rows are. Collect every USB-only widget, **including its text label**, into a
   public list `usb_only_widgets`: the camera index combo, Res, Acquire, Record, Format, Master,
   Segment and MP4 copy (CRF) combos/boxes. (Segment is added to the spec's list: it only sets
   USB segment length and would mislead under the ZWO camera.) With `"usb"` selected,
   `zwo_controls` is hidden and every widget in `usb_only_widgets` is shown; with `"zwo"`, the
   reverse. Nothing else moves.
3. Open/Close, Start/Stop Video, Take Photo, Start/Stop Session and Add Note keep their places.
   Under `"zwo"`: Open calls `recorder.connect_zwo()`, Close calls `recorder.disconnect_zwo()`,
   and the button text follows `state()["zwo_open"]`; the video button and Take Photo use
   `zwo_open` where the USB code uses `self._camera.is_open()`, and the video button's text uses
   `zwo_recording`; the resolution label shows `f"{model} {serial} ·
   {sensor_width}x{sensor_height}"` from `zwo_info` plus ` · {t:.1f} °C` from `zwo_status`.
4. Preview: connect `recorder.zwo_preview` to a slot that, when the kind is `"zwo"` and the
   widget is visible, calls `self._feed.set_frame(image)`. The USB `frame_ready` slot draws only
   when the kind is `"usb"`. A new public label `lbl_preview_caption` directly under the video
   view shows `preview_caption(mode)` for the current preview mode under `"zwo"` and is hidden
   under `"usb"`. The application does no image processing on the Preview.
5. Update both module docstrings: one camera at a time, chosen here; the ZWO rows live in
   `ZwoControls`.

## Acceptance criteria

Extend `tests/test_recording_panel.py` and `tests/test_camera_tab.py`; parametrize the new
tests over both classes where the file layout allows, otherwise write each test once per file.
Real `SessionRecorder` + real `CaptureClient` on `start("fake")` for ZWO tests. No new
`_`-prefixed access.

- [ ] `test_picker_shows_and_hides_the_right_rows`: with a recorder whose kind is `"usb"`,
  every widget in `usb_only_widgets` is `isVisibleTo(panel)` and `zwo_controls` is not; after
  `combo_camera_kind.setCurrentIndex(1)`, the reverse, and `recorder.camera_kind() == "zwo"`;
  `lbl_preview_caption.text() == "Display stretch: not intensity"`.
- [ ] `test_zwo_preview_reaches_the_view`: `monkeypatch.setattr(VideoView, "set_frame", spy)`
  where `spy` records the `QImage` and calls the original; under `"zwo"`, connect and show the
  panel; within 10 s the spy received a non-null `QImage` whose size equals a `zwo_preview`
  message's `width` × `height`. With the recorder's kind left at `"usb"` and the client opened
  directly (`client.open()`, so previews flow and `recorder.zwo_preview` fires), the spy
  receives nothing for 2 s.
- [ ] `test_video_button_follows_the_zwo_camera`: under `"zwo"`, during a session, the video
  button is disabled with the camera closed, enabled after `opened`, reads `"Stop Video"`
  while `zwo_recording`; clicking it stops the run (`zwo_recording` False).
- [ ] `test_refused_switch_reverts_the_combo`: during a ZWO run, selecting `"USB camera"`
  leaves `combo_camera_kind.currentData() == "zwo"` after the render.
- [ ] (no test: regression, proved by the existing suite in the gate) Every existing test in `tests/test_recording_panel.py`,
  `tests/test_camera_tab.py` and `tests/test_e2e_session.py` passes unchanged.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile

Soft type-gate count must not rise above `tools/mypy_ratchet.txt`;
`tools/private_access_ratchet.txt` must not rise.

## Out of scope

- ROI drawing on the view (ticket 75).
- Converting the existing USB widgets to the no-scroll variants (the app-wide scroll-wheel
  plan). Any change in USB behaviour.
- Both cameras at once.

## Comments
