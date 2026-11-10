"""VideoView rectangle drawing (ticket 75)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from rbl.gui.widgets.video_view import VideoView


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _drag(view, dx, dy):
    start = QPoint(20, 20)
    QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(view, start + QPoint(dx // 2, dy // 2))
    QTest.mouseMove(view, start + QPoint(dx, dy))
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier, start + QPoint(dx, dy))


def test_rect_drawn_on_drag(qapp):
    view = VideoView()
    view.resize(300, 200)
    view.show()
    got = []
    view.rect_drawn.connect(lambda *r: got.append(r))

    assert not view.rect_drawing_enabled()
    _drag(view, 40, 30)
    assert got == []          # drawing off: nothing

    view.set_rect_drawing(True)
    assert view.rect_drawing_enabled()
    _drag(view, 40, 30)
    assert got == [(20, 20, 40, 30)]

    _drag(view, 2, 2)
    assert got == [(20, 20, 40, 30)]   # too small: nothing more


def test_double_click_still_works_when_drawing_off(qapp):
    view = VideoView()
    view.show()
    hits = []
    view.double_clicked.connect(lambda: hits.append(1))
    QTest.mouseDClick(view, Qt.MouseButton.LeftButton)
    assert hits == [1]


def test_height_follows_width_is_opt_in(qapp):
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QImage

    view = VideoView()
    assert not view.hasHeightForWidth()
    view.set_height_follows_width(True)
    assert view.hasHeightForWidth()
    view.set_frame(QImage(2000, 500, QImage.Format.Format_RGB888))
    assert view.heightForWidth(800) == 200
    assert view.minimumSizeHint() == QSize(64, 48)

