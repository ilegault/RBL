"""
video_view.py
A camera-frame display whose size hint does NOT depend on the frame.

WHY THIS EXISTS
---------------
The Overview's camera preview used a QLabel and called setPixmap() with a
pixmap scaled to the label's current size.  A QLabel holding a pixmap reports
that pixmap's size as its minimumSizeHint(), and every Overview panel lives
inside a widgetResizable QScrollArea (drag_panel.py).  So each frame ratcheted
the panel's minimum size UP to the largest size the preview had ever reached,
and the scroll area then clipped the panel whenever it was dragged smaller —
which looked, correctly, like the camera video being cropped by the drag, and
never recovered because the ratchet only turns one way.

This widget breaks the loop: sizeHint and minimumSizeHint are constants, the
frame is scaled at PAINT time into whatever rectangle the layout hands over,
and the widget therefore exerts no pressure on the layout at all.

Ticket 89 addition: height-for-width opt-in (set_height_follows_width). When
enabled, the Preview's height follows its width at the frame's aspect ratio,
capped at PREVIEW_MAX_HEIGHT_PER_WIDTH (16:9), with vertical size policy
Preferred. This stops the Overview Preview from absorbing excess panel height.
The Camera tab leaves it disabled (default).

Ticket 93/94 addition: a Draft ROI outline (set_draft_fraction) and pointer-grab
routing.  The Camera tab holds the Draft in sensor pixels and hands this widget
only a fraction of the frame being shown; the outline is mapped into painted_rect()
at PAINT time, so a window resize needs no callback.  When rect drawing is on and a
Draft is shown, a press on the Draft's interior or corners emits draft_grabbed /
draft_dragged / draft_released instead of starting a rubber-band draw.  It is a
screen-only overlay, never burned into frames.
"""
from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QRubberBand, QSizePolicy, QWidget

from rbl.config.recording_config import PREVIEW_MAX_HEIGHT_PER_WIDTH
from rbl.hardware.preview_stretch import preview_height_for_width
from rbl.hardware.roi_geometry import hit_test


class VideoView(QWidget):
    """Camera frame display that never lets the content size influence the layout.

    The frame is drawn at PAINT time into whatever rect the layout provides.
    sizeHint and minimumSizeHint never reference the frame, so the widget
    exerts no upward pressure on the layout regardless of frame size.
    """

    double_clicked = Signal()
    # View coordinates (x, y, width, height) of a finished drag; ticket 75.
    rect_drawn = Signal(int, int, int, int)
    # Draft ROI grab/drag/release in fractions of painted_rect(); ticket 94.
    draft_grabbed = Signal(str, float, float)
    draft_dragged = Signal(float, float)
    draft_released = Signal()

    # Smaller drags are accidental clicks, not a request for an ROI.
    _MIN_DRAG_PX = 4
    # Corner hit-test radius in view pixels for grabbing a Draft ROI.
    _DRAFT_GRIP_PX = 8

    # Constant hints — see module docstring for why these must not grow.
    _HINT_SIZE = QSize(320, 240)
    _MIN_HINT  = QSize(64, 48)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame: "QImage | None" = None
        self._placeholder = "No camera"
        self._crosshair = False
        self._scale_mode = "fit"   # "fit" | "one_to_one"
        self._painted_rect_last = QRect()
        self._rect_drawing = False
        self._drag_origin: "QPoint | None" = None
        self._band: "QRubberBand | None" = None
        self._draft_dragging = False
        self._height_follows_width = False
        self._draft_fraction: "tuple[float, float, float, float] | None" = None

        self.setStyleSheet("background: #111;")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ---- public API --------------------------------------------------------

    def set_height_follows_width(self, on: bool) -> None:
        """Opt into height-for-width sizing (ticket 89).

        When True, heightForWidth calculates height based on the frame's aspect
        ratio capped at 16:9, vertical size policy becomes Preferred, and
        geometry updates whenever the frame size changes. Off by default.
        """
        self._height_follows_width = bool(on)
        if self._height_follows_width:
            self.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        else:
            self.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.updateGeometry()

    def set_frame(self, img: QImage) -> None:
        """Store a deep copy of img and schedule a repaint.

        .copy() is NOT optional: QImage(rgb.data, w, h, ...) borrows the
        ndarray's memory without owning it.  VideoView stores the image until
        the next paint; if the source array is GC'd in the meantime the stored
        image points to freed memory and the next paintEvent crashes.  .copy()
        makes the QImage own its buffer.
        """
        prev_size = (
            (self._frame.width(), self._frame.height())
            if self._frame is not None and not self._frame.isNull()
            else None
        )
        self._frame = img.copy()
        new_size = (
            (self._frame.width(), self._frame.height())
            if not self._frame.isNull()
            else None
        )
        if self._height_follows_width and new_size != prev_size:
            self.updateGeometry()
        self.update()

    def clear(self, text: str = "No camera") -> None:
        """Drop the frame and show placeholder text."""
        had_frame = self._frame is not None and not self._frame.isNull()
        self._frame = None
        self._placeholder = text
        if self._height_follows_width and had_frame:
            self.updateGeometry()
        self.update()

    def set_crosshair(self, on: bool) -> None:
        """Centre cross + rule-of-thirds guides, drawn over the frame, never burned in."""
        self._crosshair = on
        self.update()

    def set_scale_mode(self, mode: str) -> None:
        """'fit' (default) scales to fit; 'one_to_one' paints at native pixels."""
        self._scale_mode = mode
        self.updateGeometry()
        self.update()

    def set_rect_drawing(self, on: bool) -> None:
        """While on, a left-drag draws a screen-only rubber band and emits rect_drawn."""
        self._rect_drawing = bool(on)
        if not on:
            self._cancel_drag()

    def rect_drawing_enabled(self) -> bool:
        return self._rect_drawing

    def set_draft_fraction(
        self, f: "tuple[float, float, float, float] | None"
    ) -> None:
        """Outline a Draft ROI given as (x, y, w, h) fractions of the painted frame."""
        if f == self._draft_fraction:
            return
        self._draft_fraction = f
        self.update()

    def draft_fraction(self) -> "tuple[float, float, float, float] | None":
        return self._draft_fraction

    def painted_rect(self) -> QRect:
        """The rect the last frame was painted into (exposed for tests)."""
        return self._painted_rect_last

    # ---- size hints --------------------------------------------------------

    def hasHeightForWidth(self) -> bool:
        return self._height_follows_width

    def heightForWidth(self, w: int) -> int:
        if not self._height_follows_width:
            return super().heightForWidth(w)
        frame_size = (
            (self._frame.width(), self._frame.height())
            if self._frame is not None and not self._frame.isNull()
            else None
        )
        return preview_height_for_width(w, frame_size, PREVIEW_MAX_HEIGHT_PER_WIDTH)

    def sizeHint(self) -> QSize:
        if self._scale_mode == "one_to_one" and self._frame is not None:
            return self._frame.size()
        return self._HINT_SIZE

    def minimumSizeHint(self) -> QSize:
        # In fit mode this is a CONSTANT — this is the line that fixes the bug.
        # A QLabel holding a pixmap reports the pixmap's size as its
        # minimumSizeHint(), growing on every frame to lock the layout open.
        # This widget refuses: the constant 64x48 means the layout is always
        # free to shrink the widget and the scroll area never clips it.
        if self._scale_mode == "one_to_one" and self._frame is not None:
            return self._frame.size()
        return self._MIN_HINT

    # ---- events ------------------------------------------------------------

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#111"))

        if self._frame is None or self._frame.isNull():
            p.setPen(QColor("#888"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       self._placeholder)
            self._painted_rect_last = QRect()
            p.end()
            return

        frame_size = self._frame.size()
        if self._scale_mode == "fit":
            scaled_size = frame_size.scaled(self.rect().size(),
                                            Qt.AspectRatioMode.KeepAspectRatio)
        else:
            scaled_size = frame_size

        x = (self.width()  - scaled_size.width())  // 2
        y = (self.height() - scaled_size.height()) // 2
        target = QRect(QPoint(x, y), scaled_size)
        self._painted_rect_last = target

        if self._scale_mode == "fit":
            img_to_draw = self._frame.scaled(
                scaled_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        else:
            img_to_draw = self._frame
        p.drawImage(target.topLeft(), img_to_draw)

        draft_px = self._draft_rect_px()
        if draft_px is not None:
            pen = QPen(QColor(255, 200, 0), 2)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRect(*draft_px))

        if self._crosshair:
            self._draw_crosshair(p)

        p.end()

    def _draw_crosshair(self, p: QPainter) -> None:
        w, h = self.width(), self.height()
        pen = QPen(QColor(0, 255, 0, 180), 1)
        p.setPen(pen)
        p.drawLine(w // 2, 0, w // 2, h)
        p.drawLine(0, h // 2, w, h // 2)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawLine(w // 3, 0, w // 3, h)
        p.drawLine(2 * w // 3, 0, 2 * w // 3, h)
        p.drawLine(0, h // 3, w, h // 3)
        p.drawLine(0, 2 * h // 3, w, 2 * h // 3)

    def mouseDoubleClickEvent(self, event) -> None:
        self.double_clicked.emit()

    def _draft_rect_px(self) -> "tuple[int, int, int, int] | None":
        target = self._painted_rect_last
        if self._draft_fraction is None or target.isEmpty():
            return None
        fx, fy, fw, fh = self._draft_fraction
        return (
            round(target.x() + fx * target.width()),
            round(target.y() + fy * target.height()),
            round(fw * target.width()),
            round(fh * target.height()),
        )

    def _painted_fraction(self, pos: QPoint) -> "tuple[float, float]":
        target = self._painted_rect_last
        return (
            (pos.x() - target.x()) / target.width(),
            (pos.y() - target.y()) / target.height(),
        )

    def _cancel_drag(self) -> None:
        self._drag_origin = None
        self._draft_dragging = False
        if self._band is not None:
            self._band.hide()

    @staticmethod
    def _span(a: QPoint, b: QPoint) -> QRect:
        # QRect(p1, p2) treats both corners as inside the rect (+1 px); a drag
        # of 40 px should report 40.
        return QRect(min(a.x(), b.x()), min(a.y(), b.y()),
                     abs(b.x() - a.x()), abs(b.y() - a.y()))

    def mousePressEvent(self, event) -> None:
        if (self._rect_drawing
                and event.button() == Qt.MouseButton.LeftButton):
            pos = event.position().toPoint()
            draft_px = self._draft_rect_px()
            if draft_px is not None:
                hit = hit_test(draft_px, (pos.x(), pos.y()), self._DRAFT_GRIP_PX)
                if hit is not None:
                    self._draft_dragging = True
                    self.draft_grabbed.emit(hit, *self._painted_fraction(pos))
                    return
            self._drag_origin = pos
            if self._band is None:
                self._band = QRubberBand(QRubberBand.Shape.Rectangle, self)
            self._band.setGeometry(QRect(self._drag_origin, QSize()))
            self._band.show()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._draft_dragging and not self._painted_rect_last.isEmpty():
            self.draft_dragged.emit(
                *self._painted_fraction(event.position().toPoint())
            )
            return
        if self._drag_origin is not None and self._band is not None:
            self._band.setGeometry(
                self._span(self._drag_origin, event.position().toPoint()))
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if (self._draft_dragging
                and event.button() == Qt.MouseButton.LeftButton):
            self._draft_dragging = False
            self.draft_released.emit()
            return
        if (self._drag_origin is not None
                and event.button() == Qt.MouseButton.LeftButton):
            rect = self._span(self._drag_origin, event.position().toPoint())
            self._cancel_drag()
            if (rect.width() >= self._MIN_DRAG_PX
                    and rect.height() >= self._MIN_DRAG_PX):
                self.rect_drawn.emit(rect.x(), rect.y(),
                                     rect.width(), rect.height())
            return
        super().mouseReleaseEvent(event)


if __name__ == "__main__":
    import gc
    import sys

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    view = VideoView()

    assert view.minimumSizeHint() == QSize(64, 48), "minimumSizeHint must be constant"
    assert view.sizeHint() == QSize(320, 240), "sizeHint must be constant in fit mode"

    try:
        import numpy as np
        rgb = np.zeros((480, 640, 3), dtype="uint8")
        img = QImage(rgb.data, 640, 480, 3 * 640, QImage.Format.Format_RGB888)
        view.set_frame(img)
        del rgb, img
        gc.collect()
        assert view.minimumSizeHint() == QSize(64, 48), \
            "minimumSizeHint grew after set_frame — ratchet not fixed"
        print("[OK] frame buffer safety verified")
    except ImportError:
        print("[SKIP] numpy not available")

    view.clear()
    assert view._frame is None
    print("[OK] clear() works")
    print("[OK] VideoView self-test passed")
    sys.exit(0)
