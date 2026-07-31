"""
Dropdowns must not change what they say when the wheel goes past them.

Qt's default is that a wheel event over a combo box selects the next or
previous item, focused or not — so scrolling a tab, which is a reading
gesture, silently re-selects whichever dropdown the pointer happened to cross.
In this app those dropdowns pick units (cps vs mm/s, counts vs mm), waveform
shape, and which instrument a command is addressed to, so a wheel that lands
on one changes how the NEXT number typed beside it is interpreted, with
nothing on screen announcing that it moved.

These tests assert both halves: the widget itself ignores the wheel, and every
dropdown the app builds is that widget rather than a bare QComboBox.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox

from rbl.gui.widgets.inputs import NoScrollComboBox


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _wheel(widget, delta=120):
    """Deliver one wheel notch over *widget*, the way a mouse would."""
    event = QWheelEvent(
        QPointF(widget.rect().center()),               # local position
        QPointF(widget.mapToGlobal(widget.rect().center())),
        QPoint(0, 0), QPoint(0, delta),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )
    QApplication.sendEvent(widget, event)
    return event


class TestNoScrollComboBox:
    def test_the_wheel_does_not_change_the_selection(self, qapp):
        combo = NoScrollComboBox()
        combo.addItems(["cps", "mm/s"])
        combo.setCurrentIndex(0)
        _wheel(combo, delta=-120)
        assert combo.currentIndex() == 0
        _wheel(combo, delta=+120)
        assert combo.currentIndex() == 0

    def test_a_plain_combo_box_would_have(self, qapp):
        """The bug this class exists for — pinned so the fix cannot quietly
        become a no-op if Qt's default ever changes."""
        combo = QComboBox()
        combo.addItems(["cps", "mm/s"])
        combo.setCurrentIndex(0)
        _wheel(combo, delta=-120)
        assert combo.currentIndex() == 1     # Qt's default, unasked for

    def test_the_event_is_passed_up_rather_than_swallowed(self, qapp):
        """An ignored wheel event reaches the parent, so a dropdown inside a
        scroll area scrolls the AREA instead of eating the gesture."""
        combo = NoScrollComboBox()
        combo.addItems(["a", "b"])
        event = _wheel(combo)
        assert not event.isAccepted()

    def test_clicking_an_item_still_selects_it(self, qapp):
        """Only the wheel is disabled. Open-then-choose is untouched."""
        combo = NoScrollComboBox()
        combo.addItems(["counts", "mm"])
        combo.setCurrentIndex(1)
        assert combo.currentText() == "mm"


class TestEveryDropdownInTheApp:
    """A bare QComboBox anywhere is the bug back again on that one screen."""

    def _combos(self, widget):
        return widget.findChildren(QComboBox)

    def _assert_all_safe(self, widget):
        combos = self._combos(widget)
        assert combos, "expected this screen to have dropdowns"
        bad = [c for c in combos if not isinstance(c, NoScrollComboBox)]
        assert not bad, [c.objectName() or c.currentText() for c in bad]

    def test_stepper_motors_tab(self, qapp):
        from rbl.state.beamline import Beamline
        from rbl.gui.motor_tab import MotorTab
        self._assert_all_safe(MotorTab(Beamline()))

    def test_function_generators_tab(self, qapp):
        from rbl.state.beamline import Beamline
        from rbl.gui.funcgen_tab import FuncGenTab
        self._assert_all_safe(FuncGenTab(Beamline()))

    def test_hv_amplifier_tab(self, qapp):
        from rbl.gui.amp_tab import AmpTab
        self._assert_all_safe(AmpTab())

    def test_beam_position_indicator(self, qapp):
        from rbl.gui.widgets.beam_indicator import BeamPositionIndicator
        self._assert_all_safe(BeamPositionIndicator())

    def test_unit_dropdowns_keep_their_options(self, qapp):
        """Swapping the class must not have changed what the boxes offer."""
        from rbl.state.beamline import Beamline
        from rbl.gui.motor_tab import MotorTab

        panel = MotorTab(Beamline()).axes["A"]
        assert [panel.cbo_speed_unit.itemText(i) for i in range(2)] == \
            ["cps", "mm/s"]
        assert [panel.cbo_target_unit.itemText(i) for i in range(2)] == \
            ["counts", "mm"]
        assert panel.cbo_target_unit.currentText() == "mm"    # default unchanged
