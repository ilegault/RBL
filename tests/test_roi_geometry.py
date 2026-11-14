"""tests/test_roi_geometry.py
Tests for pure Draft ROI geometry functions (no Qt, no numpy, no clock).
"""
import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rbl.config.capture_config import ROI_DRAFT_MIN_PX
from rbl.hardware.roi_geometry import (
    fit_ratio,
    hit_test,
    move_rect,
    point_from_fraction,
    rect_from_corners,
    resize_from_corner,
    to_fraction,
)


def test_no_qt_or_numpy_imports():
    """Verify rbl.hardware.roi_geometry imports no Qt, numpy, or clock modules."""
    base_dir = Path(__file__).resolve().parent.parent
    module_path = base_dir / "src" / "rbl" / "hardware" / "roi_geometry.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    banned_prefixes = ("PySide6", "PyQt", "numpy", "cv2", "time")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for banned in banned_prefixes:
                    assert not alias.name.startswith(banned), f"Banned import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for banned in banned_prefixes:
                assert not mod.startswith(banned), f"Banned import: {mod}"

    # Also verify in a fresh subprocess that importing roi_geometry does not pull in PySide6
    src_dir = str(base_dir / "src")
    env = dict(os.environ, PYTHONPATH=src_dir)
    check_code = (
        "import sys; from rbl.hardware import roi_geometry; "
        "assert 'PySide6' not in sys.modules, 'PySide6 imported!'"
    )
    cmd = [
        sys.executable,
        "-c",
        check_code,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 0, f"Subprocess check failed: {res.stderr}"


@pytest.mark.parametrize(
    "rect, ratio, bounds, expected_w, expected_h, expected_cx, expected_cy",
    [
        ((0, 0, 400, 400), (16, 9), (0, 0, 1920, 1080), 400, 225, 200, 200),
        ((100, 100, 300, 300), (4, 3), (0, 0, 1920, 1080), 300, 225, 250, 250),
        ((100, 100, 300, 300), (1, 1), (0, 0, 1920, 1080), 300, 300, 250, 250),
        ((0, 0, 400, 100), (16, 9), (0, 0, 1920, 1080), 178, 100, 200, 50),
    ],
)
def test_fit_ratio(rect, ratio, bounds, expected_w, expected_h, expected_cx, expected_cy):
    res = fit_ratio(rect, ratio, bounds)
    x, y, w, h = res
    assert w == expected_w
    assert h == expected_h
    # Centre within rounding (±0.5 px)
    cx = x + w / 2.0
    cy = y + h / 2.0
    assert abs(cx - expected_cx) <= 0.5
    assert abs(cy - expected_cy) <= 0.5

    # Overhanging bounds comes back fully inside bounds
    overhanging = (-50, -50, 400, 400)
    res_over = fit_ratio(overhanging, (16, 9), (0, 0, 1920, 1080))
    ox, oy, ow, oh = res_over
    bx, by, bw, bh = bounds
    assert ox >= bx
    assert oy >= by
    assert ox + ow <= bx + bw
    assert oy + oh <= by + bh
    assert ow == 400
    assert oh == 225

    # Larger than bounds scales down to fit keeping ratio
    huge = (0, 0, 3000, 3000)
    small_bounds = (100, 100, 800, 600)
    res_scaled = fit_ratio(huge, (16, 9), small_bounds)
    sx, sy, sw, sh = res_scaled
    assert sw <= 800
    assert sh <= 600
    assert abs(sw * 9 - sh * 16) <= 1
    assert sx >= 100 and sy >= 100 and sx + sw <= 900 and sy + sh <= 700

    # ratio=None returns an inside rect unchanged
    inside = (50, 60, 200, 150)
    assert fit_ratio(inside, None, (0, 0, 1920, 1080)) == inside

    # ratio=None shifts overhanging rect into bounds
    over_none = (-20, 1050, 200, 100)
    res_none = fit_ratio(over_none, None, (0, 0, 1920, 1080))
    assert res_none == (0, 980, 200, 100)


def test_rect_from_corners():
    bounds = (0, 0, 1920, 1080)
    # Free ((100,100),(300,200)) -> (100,100,200,100)
    assert rect_from_corners((100, 100), (300, 200), None, bounds) == (100, 100, 200, 100)

    # Dragging up-left from anchor gives a rect whose bottom-right is the anchor
    up_left = rect_from_corners((300, 200), (100, 100), None, bounds)
    assert up_left == (100, 100, 200, 100)
    assert up_left[0] + up_left[2] == 300
    assert up_left[1] + up_left[3] == 200

    # (1,1) ratio with span 200 x 100 -> 100 x 100 at the anchor
    assert rect_from_corners((100, 100), (300, 200), (1, 1), bounds) == (100, 100, 100, 100)

    # (1,1) ratio dragging up-left
    sq_up_left = rect_from_corners((300, 200), (100, 100), (1, 1), bounds)
    # Largest 1:1 inside span 200 x 100 is 100 x 100 anchored at bottom-right (300, 200)
    assert sq_up_left == (200, 100, 100, 100)
    assert sq_up_left[0] + sq_up_left[2] == 300
    assert sq_up_left[1] + sq_up_left[3] == 200

    # Pointer outside bounds yields a rect inside bounds
    out = rect_from_corners((100, 100), (2500, 1500), None, bounds)
    assert out[0] >= bounds[0] and out[1] >= bounds[1]
    assert out[0] + out[2] <= bounds[0] + bounds[2]
    assert out[1] + out[3] <= bounds[1] + bounds[3]
    assert out == (100, 100, 1820, 980)

    # 2 px drag yields sides of ROI_DRAFT_MIN_PX
    tiny_free = rect_from_corners((100, 100), (102, 102), None, bounds)
    assert tiny_free[2] == ROI_DRAFT_MIN_PX
    assert tiny_free[3] == ROI_DRAFT_MIN_PX
    assert tiny_free == (100, 100, ROI_DRAFT_MIN_PX, ROI_DRAFT_MIN_PX)

    # 2 px drag with ratio
    tiny_ratio = rect_from_corners((100, 100), (102, 101), (1, 1), bounds)
    assert tiny_ratio[2] == ROI_DRAFT_MIN_PX
    assert tiny_ratio[3] == ROI_DRAFT_MIN_PX


def test_move_rect_clamps_and_keeps_size():
    bounds = (0, 0, 1920, 1080)
    res = move_rect((10, 10, 100, 50), -500, 9999, bounds)
    assert res == (0, 1030, 100, 50)

    # Normal move inside bounds
    res_normal = move_rect((100, 100, 200, 150), 50, -30, bounds)
    assert res_normal == (150, 70, 200, 150)


def test_resize_from_corner_keeps_the_opposite_corner():
    bounds = (0, 0, 1920, 1080)
    orig = (100, 100, 200, 150)
    # Corners of orig:
    # tl = (100, 100), opposite br = (300, 250)
    # tr = (300, 100), opposite bl = (100, 250)
    # bl = (100, 250), opposite tr = (300, 100)
    # br = (300, 250), opposite tl = (100, 100)

    # 1. Resize from "br" (anchor is tl = (100, 100))
    res_br = resize_from_corner(orig, "br", (400, 300), None, bounds)
    assert (res_br[0], res_br[1]) == (100, 100)

    # 2. Resize from "tl" (anchor is br = (300, 250))
    res_tl = resize_from_corner(orig, "tl", (50, 50), None, bounds)
    assert (res_tl[0] + res_tl[2], res_tl[1] + res_tl[3]) == (300, 250)

    # 3. Resize from "tr" (anchor is bl = (100, 250))
    res_tr = resize_from_corner(orig, "tr", (350, 50), None, bounds)
    assert (res_tr[0], res_tr[1] + res_tr[3]) == (100, 250)

    # 4. Resize from "bl" (anchor is tr = (300, 100))
    res_bl = resize_from_corner(orig, "bl", (50, 300), None, bounds)
    assert (res_bl[0] + res_bl[2], res_bl[1]) == (300, 100)

    # With (4, 3) ratio, w * 3 == h * 4 within +-1
    res_ratio = resize_from_corner(orig, "br", (500, 400), (4, 3), bounds)
    assert abs(res_ratio[2] * 3 - res_ratio[3] * 4) <= 1
    assert (res_ratio[0], res_ratio[1]) == (100, 100)


def test_fractions_round_trip_and_hit_test():
    roi = (320, 180, 960, 540)
    r = (400, 200, 100, 50)
    frac = to_fraction(r, roi)
    pt = point_from_fraction(frac[0], frac[1], roi)
    assert pt == r[:2]

    # hit_test
    rect = (0, 0, 100, 100)
    assert hit_test(rect, (2, 98), 6) == "bl"
    assert hit_test(rect, (50, 50), 6) == "inside"
    assert hit_test(rect, (200, 200), 6) is None

    # Test other corners
    assert hit_test(rect, (1, 1), 6) == "tl"
    assert hit_test(rect, (99, 2), 6) == "tr"
    assert hit_test(rect, (98, 98), 6) == "br"
