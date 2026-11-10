"""
roi_geometry.py
Pure geometry functions for Camera tab Draft ROI manipulation.

WHY THIS EXISTS
---------------
Draft ROI (ticket 92, spec Solution 6, D7) allows an operator to interactively
position, resize, and lock the aspect ratio of a region of interest before
committing it to the camera Capture Process via Apply ROI.

If this geometry math lived inside the GUI widgets (e.g. CameraTab or VideoView),
testing it would require constructing heavy Qt widgets or driving real/simulated
GUI events. Furthermore, mixing widget coordinates with sensor pixels easily
leads to rounding bugs, aspect drift, or boundary overflows.

By keeping all geometry in this pure module—operating strictly on integer sensor
pixel tuples without Qt, numpy, or clocks—every rule (aspect fitting, edge
clamping, corner anchoring, and coordinate translation) is completely
deterministic, unit-testable in isolation, and independent of GUI lifecycle or
display scaling.
"""
from __future__ import annotations

import math

from rbl.config.capture_config import ROI_DRAFT_MIN_PX

Rect = tuple[int, int, int, int]
Point = tuple[int, int]
Ratio = tuple[int, int] | None


def fit_ratio(rect: Rect, ratio: Ratio, bounds: Rect) -> Rect:
    """Fit rect to ratio and bounds.

    With a ratio, shrink the too-long side, keeping the centre, until
    ``width * ratio_h == height * ratio_w`` within rounding (±1 px); then shift
    it inside ``bounds``; if still larger than ``bounds``, scale it down about
    its centre keeping the ratio until it fits.  ``ratio=None`` only
    shifts/shrinks into ``bounds``.
    """
    x, y, w, h = rect
    bx, by, bw, bh = bounds

    if ratio is None:
        w = min(w, bw)
        h = min(h, bh)
        x = max(bx, min(bx + bw - w, x))
        y = max(by, min(by + bh - h, y))
        return (x, y, w, h)

    rw, rh = ratio
    cx = x + w / 2.0
    cy = y + h / 2.0

    # Shrink the too-long side, keeping the centre
    if w * rh > h * rw:
        w = round(h * rw / rh)
    elif w * rh < h * rw:
        h = round(w * rh / rw)

    # If still larger than bounds, scale down about centre keeping ratio
    if w > bw or h > bh:
        scale = min(bw / w, bh / h)
        w = round(w * scale)
        h = round(h * scale)
        if w * rh > h * rw:
            w = round(h * rw / rh)
        elif w * rh < h * rw:
            h = round(w * rh / rw)

    x = round(cx - w / 2.0)
    y = round(cy - h / 2.0)

    # Shift inside bounds
    x = max(bx, min(bx + bw - w, x))
    y = max(by, min(by + bh - h, y))

    return (x, y, w, h)


def rect_from_corners(
    anchor: Point,
    pointer: Point,
    ratio: Ratio,
    bounds: Rect,
) -> Rect:
    """Create a rect anchored at ``anchor`` and extended towards ``pointer``.

    ``pointer`` is clamped into ``bounds`` first. Free (``ratio=None``): the
    rect spanned by the two points. With a ratio: the largest rect of that
    ratio, anchored at ``anchor``, that fits inside the span (never passing the
    pointer). Each side is at least ``ROI_DRAFT_MIN_PX`` when ``bounds`` allows,
    growing away from ``anchor``.
    """
    bx, by, bw, bh = bounds
    ax, ay = anchor
    px, py = pointer

    # Clamp pointer into bounds
    px = max(bx, min(bx + bw, px))
    py = max(by, min(by + bh, py))

    dx = px - ax
    dy = py - ay

    dir_x = -1 if dx < 0 else 1
    dir_y = -1 if dy < 0 else 1

    span_w = abs(dx)
    span_h = abs(dy)

    if ratio is None:
        w = span_w
        h = span_h
    else:
        rw, rh = ratio
        if span_w * rh <= span_h * rw:
            w = span_w
            h = round(span_w * rh / rw)
        else:
            h = span_h
            w = round(span_h * rw / rh)

    # Minimum side constraint: at least ROI_DRAFT_MIN_PX
    min_side = ROI_DRAFT_MIN_PX
    if ratio is None:
        w = max(w, min_side)
        h = max(h, min_side)
    else:
        rw, rh = ratio
        w_from_min_h = round(min_side * rw / rh)
        h_from_min_w = round(min_side * rh / rw)
        min_w = max(min_side, w_from_min_h)
        min_h = max(min_side, h_from_min_w)
        if w < min_w or h < min_h:
            w = max(w, min_w)
            h = max(h, min_h)

    # Available space away from anchor in growth direction
    avail_w = max(0, (bx + bw - ax) if dir_x > 0 else (ax - bx))
    avail_h = max(0, (by + bh - ay) if dir_y > 0 else (ay - by))

    if ratio is None:
        w = min(w, avail_w)
        h = min(h, avail_h)
    else:
        rw, rh = ratio
        if w > avail_w:
            w = avail_w
            h = round(w * rh / rw)
        if h > avail_h:
            h = avail_h
            w = round(h * rw / rh)

    x = ax if dir_x > 0 else ax - w
    y = ay if dir_y > 0 else ay - h

    return (x, y, w, h)


def move_rect(rect: Rect, dx: int, dy: int, bounds: Rect) -> Rect:
    """Translate rect by (dx, dy) keeping it entirely within bounds. Size is preserved."""
    x, y, w, h = rect
    bx, by, bw, bh = bounds
    new_x = x + dx
    new_y = y + dy

    if w <= bw:
        new_x = max(bx, min(bx + bw - w, new_x))
    else:
        new_x = bx

    if h <= bh:
        new_y = max(by, min(by + bh - h, new_y))
    else:
        new_y = by

    return (new_x, new_y, w, h)


def resize_from_corner(
    rect: Rect,
    corner: str,
    pointer: Point,
    ratio: Ratio,
    bounds: Rect,
) -> Rect:
    """Resize rect by dragging ``corner`` ('tl', 'tr', 'bl', 'br') to ``pointer``.

    Anchors at the opposite corner of the rect.
    """
    x, y, w, h = rect
    if corner == "tl":
        anchor = (x + w, y + h)
    elif corner == "tr":
        anchor = (x, y + h)
    elif corner == "bl":
        anchor = (x + w, y)
    elif corner == "br":
        anchor = (x, y)
    else:
        raise ValueError(f"unknown corner {corner!r}; expected 'tl', 'tr', 'bl', or 'br'")

    return rect_from_corners(anchor, pointer, ratio, bounds)


def _parse_roi(roi: Rect | dict[str, int]) -> tuple[int, int, int, int]:
    if isinstance(roi, dict):
        return roi["x"], roi["y"], roi["width"], roi["height"]
    return roi[0], roi[1], roi[2], roi[3]


def to_fraction(
    rect: Rect,
    roi: Rect | dict[str, int],
) -> tuple[float, float, float, float]:
    """Map a rect in sensor pixels to fractions of the applied ROI (0..1)."""
    rx, ry, rw, rh = _parse_roi(roi)
    x, y, w, h = rect
    if rw <= 0 or rh <= 0:
        return 0.0, 0.0, 0.0, 0.0
    return (
        (x - rx) / float(rw),
        (y - ry) / float(rh),
        w / float(rw),
        h / float(rh),
    )


def point_from_fraction(
    fx: float,
    fy: float,
    roi: Rect | dict[str, int],
) -> Point:
    """Map fractional coordinates (0..1) back to sensor pixel points in ROI."""
    rx, ry, rw, rh = _parse_roi(roi)
    px = round(rx + fx * rw)
    py = round(ry + fy * rh)
    px = max(rx, min(rx + rw, px))
    py = max(ry, min(ry + rh, py))
    return px, py


def hit_test(rect: Rect, point: Point, grip: float) -> str | None:
    """Hit test point against rect with grip tolerance around corners.

    Returns corner name ('tl', 'tr', 'bl', 'br') when within grip distance of a
    corner (corners win over inside), 'inside' when inside the rect, else None.
    """
    x, y, w, h = rect
    px, py = point

    corners = [
        ("tl", math.hypot(px - x, py - y)),
        ("tr", math.hypot(px - (x + w), py - y)),
        ("bl", math.hypot(px - x, py - (y + h))),
        ("br", math.hypot(px - (x + w), py - (y + h))),
    ]
    candidates = [(name, d) for name, d in corners if d <= grip]
    if candidates:
        candidates.sort(key=lambda item: item[1])
        return candidates[0][0]

    if x <= px <= x + w and y <= py <= y + h:
        return "inside"

    return None
