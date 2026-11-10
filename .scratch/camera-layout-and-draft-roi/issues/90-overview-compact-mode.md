# 90: Overview Session Recorder: compact mode

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 89

**Spec:** `.scratch/camera-layout-and-draft-roi/spec.md` (Solution 3, D3)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/widgets/recording_panel.py` (`_build_ui` after 89, `_render`); `src/rbl/gui/widgets/camera_kind_mixin.py` (`_render_kind`: it sets visibility of `usb_only_widgets`, `zwo_controls` and `lbl_preview_caption` on **every** render); `tests/test_recording_panel.py` (`zwo_rig`, `test_picker_shows_and_hides_the_right_rows` for the visibility-assertion style).

## What to build

A checkable **Compact** button on the right end of row 1 of the Overview's Session Recorder
(`RecordingPanel.btn_compact`, after the trailing stretch). Checked = compact mode: the
panel shows only what is needed during a run. Unchecked on construction; never saved.
`CameraTab` gets no such button.

1. `RecordingPanel.compact_hidden_widgets`: a list built in `_build_ui` holding
   `lbl_camera_kind`, `combo_camera_kind`, every widget in `usb_only_widgets`,
   `zwo_controls`, the "CSV every:" label, `_spin_csv`, `_lbl_stats`, `_btn_video`,
   `_btn_folder`.
2. Shown in compact mode (D3): `_btn_open`, `_lbl_res`, `btn_compact`, `_feed`,
   `lbl_preview_caption` (ZWO only, as today), `_btn_session`, `_lbl_status`, `_btn_photo`,
   `_btn_note`.
3. Compact visibility is applied **last** in `RecordingPanel._render`, after `_render_kind`:
   when compact, every widget in `compact_hidden_widgets` is hidden; when not compact, the
   widgets in that list that `_render_kind` does not manage are shown, and `_render_kind`'s
   own result stands for the rest. This ordering is a requirement: `_render_kind` re-shows
   kind rows on every state change, and compact mode must survive it.
4. Toggling `btn_compact` calls `_render()`. No recorder call, no signal to the recorder.

## Acceptance criteria

Tests use `zwo_rig` with `which="panel"`; visibility is checked with `isVisibleTo(view)`.

- [ ] `test_compact_mode_shows_only_run_controls` (parametrized over `"usb"`, `"zwo"`):
  after `btn_compact.setChecked(True)`, every widget in `compact_hidden_widgets` is not
  visible and every widget in the D3 shown list (caption only under ZWO) is visible.
- [ ] `test_compact_mode_survives_state_updates`: compact on under ZWO, then
  `rec.state_changed.emit()` and `rec.settings_changed.emit()`; `zwo_controls` and
  `combo_camera_kind` are still not visible.
- [ ] `test_leaving_compact_restores_the_kind_rows` (parametrized `"usb"`, `"zwo"`): compact
  on then off; visibility of `usb_only_widgets`, `zwo_controls` and `lbl_preview_caption`
  equals what it was before compact was turned on.
- [ ] `test_compact_starts_off`: a newly constructed `RecordingPanel` has
  `btn_compact.isChecked()` False and `zwo_controls`/`usb_only_widgets` visible per kind.
- [ ] `test_compact_buttons_still_act`: in compact mode with the fake ZWO camera open,
  clicking `_btn_session` starts a session (`rec.state()["recording"]` True) and clicking
  `_btn_photo` emits `rec.photo_taken` (wait with `tests.qt_wait.wait_until`).

## Gate

Run all four, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
