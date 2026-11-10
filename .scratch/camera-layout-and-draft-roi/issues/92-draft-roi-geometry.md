# 92: Draft ROI geometry as pure functions

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/camera-layout-and-draft-roi/spec.md` (Solution 6, D7)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/hardware/preview_stretch.py` (`view_rect_to_sensor_roi`: the existing view → sensor mapping, its rounding of *edges* not sizes, and its docstring style); `tests/test_preview_stretch.py::test_view_rect_to_sensor_roi` (parametrized-table test style); `src/rbl/config/capture_config.py` (where capture constants live).

## What to build

A new module `src/rbl/hardware/roi_geometry.py`: plain Python, **no Qt, no numpy, no
clock**. This is a requirement, not a preference: the Camera tab tickets (93, 94) only
route mouse events into these functions, so every geometric rule is tested here with
tuples. Every rect is `(x, y, width, height)` of ints in **sensor pixels**; `bounds` is the
rect the Draft must stay inside (the currently applied ROI). `ratio` is `(w, h)` ints, or
`None` for Free.

New constant in `src/rbl/config/capture_config.py`: `ROI_DRAFT_MIN_PX = 16` (smallest
Draft side, sensor px).

1. `fit_ratio(rect, ratio, bounds) -> rect`: with a ratio, shrink the too-long side, keeping
   the centre, until `width * ratio_h == height * ratio_w` within rounding (±1 px); then
   shift it inside `bounds`; if still larger than `bounds`, scale it down about its centre
   keeping the ratio until it fits. `ratio=None` only shifts/shrinks into `bounds`.
2. `rect_from_corners(anchor, pointer, ratio, bounds) -> rect`: `anchor` and `pointer` are
   `(x, y)`; `pointer` is clamped into `bounds` first. Free: the rect spanned by the two
   points. With a ratio: the **largest** rect of that ratio, anchored at `anchor`, that fits
   inside the span (it never passes the pointer). Each side is at least `ROI_DRAFT_MIN_PX`
   when `bounds` allows, growing away from `anchor`.
3. `move_rect(rect, dx, dy, bounds) -> rect`: translate; clamp so the whole rect stays in
   `bounds`; size never changes.
4. `resize_from_corner(rect, corner, pointer, ratio, bounds) -> rect`: `corner` is one of
   `"tl" "tr" "bl" "br"`; equals `rect_from_corners(<opposite corner of rect>, pointer, ratio, bounds)`.
5. `to_fraction(rect, roi) -> tuple[float, float, float, float]` and
   `point_from_fraction(fx, fy, roi) -> tuple[int, int]`: fractions of the applied ROI
   (`0..1`), the form the view paints and reports in; `point_from_fraction` rounds and
   clamps into `roi`.
6. `hit_test(rect, point, grip) -> str | None`: in any one coordinate space; returns the
   corner name when `point` is within `grip` of that corner (corners win over inside),
   `"inside"` when inside the rect, else `None`.

## Acceptance criteria

`tests/test_roi_geometry.py` (new), parametrized tables, no Qt import anywhere in the
module under test (assert `"PySide6" not in sys.modules` after a fresh
`importlib.import_module` in a subprocess, or by reading the module's imports with `ast`).

- [ ] `test_fit_ratio`: `((0,0,400,400), (16,9), (0,0,1920,1080))` → width 400, height 225,
  centre `(200, 200)`; a rect overhanging `bounds` comes back fully inside; `ratio=None`
  returns an inside rect unchanged.
- [ ] `test_rect_from_corners`: Free `((100,100),(300,200))` → `(100,100,200,100)`; dragging
  up-left from the anchor gives a rect whose bottom-right is the anchor; `(1,1)` ratio with
  span 200 × 100 → `100 × 100` at the anchor; a pointer outside `bounds` yields a rect inside
  `bounds`; a 2 px drag yields sides of `ROI_DRAFT_MIN_PX`.
- [ ] `test_move_rect_clamps_and_keeps_size`: moving `(10,10,100,50)` by `(-500, 9999)` in
  `(0,0,1920,1080)` gives `(0, 1030, 100, 50)`.
- [ ] `test_resize_from_corner_keeps_the_opposite_corner`: for each of the four corners the
  opposite corner of the result equals the opposite corner of the input; with `(4,3)` the
  result's `w * 3 == h * 4` within ±1.
- [ ] `test_fractions_round_trip_and_hit_test`: `point_from_fraction(*to_fraction(r, roi)[:2], roi) == r[:2]`
  for a nested `roi=(320, 180, 960, 540)`; `hit_test((0,0,100,100), (2,98), 6) == "bl"`,
  `(50,50) -> "inside"`, `(200,200) -> None`.

## Gate

Run all four, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
