# 95: Camera tab: a visible Full screen button

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/camera-layout-and-draft-roi/spec.md` (Solution 7, D8)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/camera_tab.py` (`_FullscreenWindow` with its `keyPressEvent` and `closeEvent`; `CameraTab._toggle_fullscreen`; the top row in `_build_ui` holding `_chk_crosshair`; the module docstring's "Double-click feed to go fullscreen; Esc to exit"); `tests/test_camera_tab.py` (`setup` fixture).

## What to build

A `QPushButton("Full screen")`, `CameraTab.btn_fullscreen`, in the top controls row
immediately after `_chk_crosshair`, with tooltip `"Full screen (Esc to exit). Double-click
the picture also works."`. Clicking it calls the existing `_toggle_fullscreen`. Double-click
and Esc keep working exactly as now. Add the button to the module docstring's extras list.

## Acceptance criteria

Use the existing `setup` fixture; the full-screen window is a real `_FullscreenWindow`
(offscreen platform). Close any window a test opens.

- [x] `test_fullscreen_button_opens_and_esc_closes`: `btn_fullscreen.click()` sets
  `tab._fullscreen_win` to a `_FullscreenWindow`; `QTest.keyClick(tab._fullscreen_win, Qt.Key.Key_Escape)`
  then leaves `tab._fullscreen_win` None.
- [x] `test_fullscreen_button_and_double_click_agree`: clicking the button while the window
  is open closes it (`_fullscreen_win` None), and `video_view.double_clicked.emit()` opens it
  again.
- [x] `btn_fullscreen` is in the same `QHBoxLayout` as `_chk_crosshair`
  (`layout.indexOf(...) != -1` for both).

## Gate

Run all four, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments

2026-11-10:
- Added `CameraTab.btn_fullscreen` (`QPushButton("Full screen")`) placed immediately after `_chk_crosshair` in `_build_ui`, wired to `_toggle_fullscreen` with tooltip `"Full screen (Esc to exit). Double-click the picture also works."`.
- Added the button to `camera_tab.py` module docstring extras list.
- Covered by `test_fullscreen_button_opens_and_esc_closes`, `test_fullscreen_button_and_double_click_agree`, and `test_fullscreen_button_in_same_layout_as_crosshair` in `tests/test_camera_tab.py`.
- Note: Criterion 3 did not include a backticked test name in the ticket; covered by `test_fullscreen_button_in_same_layout_as_crosshair`.
