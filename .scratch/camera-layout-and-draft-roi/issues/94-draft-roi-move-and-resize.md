# 94: Draft ROI: move and resize by dragging

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 93

**Spec:** `.scratch/camera-layout-and-draft-roi/spec.md` (Solution 6, D7, D8)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/widgets/video_view.py` (`mousePressEvent`, `mouseMoveEvent`, `mouseReleaseEvent`, `_MIN_DRAG_PX`, `set_draft_fraction` from 93); `src/rbl/gui/camera_tab.py` (`_draft`, `draft()`, the aspect combo and `_on_rect_drawn` from 93); `src/rbl/hardware/roi_geometry.py` (`hit_test`, `move_rect`, `resize_from_corner`, `point_from_fraction`); `tests/test_video_view.py::test_rect_drawn_on_drag` (how mouse events are driven).

## What to build

With a Draft on screen, dragging inside it moves it; dragging one of its corners resizes it
with the opposite corner fixed and the aspect preset held. Nothing is sent to the camera
until **Apply ROI** (93). Dragging anywhere else draws a new Draft, as in 93.

1. **`VideoView`.** New class constant `_DRAFT_GRIP_PX = 8` and three signals:
   `draft_grabbed = Signal(str, float, float)` (hit kind, pointer x and y as fractions of
   `painted_rect()`), `draft_dragged = Signal(float, float)`, `draft_released = Signal()`.
   In `mousePressEvent`, when rect drawing is on and a draft fraction is set: map the
   fraction to view pixels inside `painted_rect()` and call
   `roi_geometry.hit_test(that, (x, y), _DRAFT_GRIP_PX)`. On a hit, emit `draft_grabbed`
   and route following moves to `draft_dragged` and the release to `draft_released`; **no
   rubber band and no `rect_drawn`** for that gesture. On a miss, behave exactly as today.
   Pointer fractions may fall outside `0..1`; the tab clamps.
2. **`CameraTab`.** On `draft_grabbed`, store the kind, a copy of the Draft, and the start
   point `point_from_fraction(fx, fy, applied ROI)`. On `draft_dragged`, compute the pointer
   the same way and set the Draft to `move_rect(start, dx, dy, applied ROI)` for `"inside"`,
   or `resize_from_corner(start, corner, pointer, <current ratio>, applied ROI)` for a
   corner; update the overlay with `to_fraction`. On `draft_released`, clear the grab
   state. All geometry goes through `roi_geometry`; no arithmetic on rects in the tab.

## Acceptance criteria

`VideoView` tests drive real `QTest.mousePress/mouseMove/mouseRelease` on a shown view
with a 1920 × 1080 frame. `CameraTab` tests use `zwo_tab` and emit the three signals
directly. The recorder and the fake Capture Process are real.

- [ ] `tests/test_video_view.py::test_press_on_draft_grabs_instead_of_drawing`: with
  `set_draft_fraction((0.25, 0.25, 0.5, 0.5))`, a drag starting at the painted centre emits
  `draft_grabbed("inside", ~0.5, ~0.5)` and `draft_dragged`, and **no** `rect_drawn`; a drag
  starting 2 px from the draft's top-left corner emits `draft_grabbed("tl", ...)`; a drag
  starting outside the draft emits `rect_drawn` as before.
- [ ] `tests/test_camera_tab.py::test_dragging_inside_moves_the_draft`: Draft
  `(400, 200, 640, 360)` at full sensor; `draft_grabbed("inside", f0)`, `draft_dragged(f0 + 0.1 in x)`:
  `draft()` is `(592, 200, 640, 360)` (±1 px); dragging far past the right edge gives
  `x + width == 1920` and the same size.
- [ ] `test_dragging_a_corner_resizes_with_the_preset`: preset `"16:9"`, grab `"br"`, drag
  outward: the Draft's top-left is unchanged and `w * 9 == h * 16` within ±1.
- [ ] `test_dragging_sends_nothing_until_apply`: after any sequence of grab/drag/release,
  the confirmed ROI 0.5 s later is unchanged; `btn_apply_roi.click()` then makes it
  `snap_roi(*draft(), 1920, 1080)`.

## Gate

Run all four, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
