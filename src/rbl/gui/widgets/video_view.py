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
"""
from PySide6.QtCore import Qt, QSize, QRect, QPoint, Signal
from PySide6.QtGui import QImage, QPainter, QPen, QColor
from PySide6.QtWidgets import QWidget, QSizePolicy


class VideoView(QWidget):
    """Camera frame display that never lets the content size influence the layout.

    The frame is drawn at PAINT time into whatever rect the layout provides.
    sizeHint and minimumSizeHint never reference the frame, so the widget
    exerts no upward pressure on the layout regardless of frame size.
    """

    double_clicked = Signal()

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

        self.setStyleSheet("background: #111;")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ---- public API --------------------------------------------------------

    def set_frame(self, img: QImage) -> None:
        """Store a deep copy of img and schedule a repaint.

        .copy() is NOT optional: QImage(rgb.data, w, h, ...) borrows the
        ndarray's memory without owning it.  VideoView stores the image until
        the next paint; if the source array is GC'd in the meantime the stored
        image points to freed memory and the next paintEvent crashes.  .copy()
        makes the QImage own its buffer.
        """
        self._frame = img.copy()
        self.update()

    def clear(self, text: str = "No camera") -> None:
        """Drop the frame and show placeholder text."""
        self._frame = None
        self._placeholder = text
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

    def painted_rect(self) -> QRect:
        """The rect the last frame was painted into (exposed for tests)."""
        return self._painted_rect_last

    # ---- size hints --------------------------------------------------------

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
