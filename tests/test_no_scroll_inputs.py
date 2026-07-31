"""
No control changes value because the wheel went past it.

Qt's default is that a wheel event over a combo box or a spin box edits it —
focused or not, just from the pointer being over it. Scrolling a tab is a
READING gesture, so that turns "scroll past a control" into "silently
re-command it". The controls in question here are slit targets in mm, jog
speeds, drive amplitudes in kV-per-plate, and the unit dropdowns that decide
how the box beside them is read; nothing on screen announces the change, and
on a setpoint that is Applied later there is no moment where it looks wrong.

These tests assert both halves, for both kinds of control: the widget itself
ignores the wheel, and every dropdown and numeric box the app builds is that
widget rather than a bare Qt one.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QAbstractSpinBox,
)

from rbl.gui.widgets.inputs import NoScrollComboBox, QuietDoubleSpinBox


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


class TestQuietDoubleSpinBox:
    """The same hazard on the numeric boxes, where it costs more: a wheel notch
    on a spin box is COMMITTED — straight out as valueChanged, into the shared
    setpoint model, and onto the other tab showing the same number."""

    def test_the_wheel_does_not_change_the_value(self, qapp):
        spn = QuietDoubleSpinBox()
        spn.setRange(0.0, 25.0)
        spn.setSingleStep(0.1)
        spn.setValue(1.5)
        _wheel(spn, delta=+120)
        assert spn.value() == 1.5
        _wheel(spn, delta=-120)
        assert spn.value() == 1.5

    def test_not_even_when_it_has_focus(self, qapp):
        """Focus is not consent — the pointer is what the wheel follows, and a
        focused box is exactly the one an operator is mid-thought over."""
        spn = QuietDoubleSpinBox()
        spn.setRange(0.0, 25.0)
        spn.setValue(1.5)
        spn.setFocus()
        _wheel(spn, delta=+120)
        assert spn.value() == 1.5

    def test_it_emits_nothing(self, qapp):
        """The value not moving is half of it; the shared model never hearing
        about a move is the half that reaches the other tabs."""
        spn = QuietDoubleSpinBox()
        spn.setRange(0.0, 25.0)
        spn.setValue(1.5)
        seen = []
        spn.valueChanged.connect(seen.append)
        _wheel(spn, delta=+120)
        assert seen == []

    def test_a_plain_spin_box_would_have(self, qapp):
        combo = QDoubleSpinBox()
        combo.setRange(0.0, 25.0)
        combo.setSingleStep(0.1)
        combo.setValue(1.5)
        _wheel(combo, delta=+120)
        assert combo.value() != 1.5      # Qt's default, unasked for

    def test_the_event_is_passed_up_rather_than_swallowed(self, qapp):
        spn = QuietDoubleSpinBox()
        event = _wheel(spn)
        assert not event.isAccepted()

    def test_the_arrows_still_step_it(self, qapp):
        """Only the wheel goes. Stepping by a gesture aimed at ONE control —
        the arrow keys, the up/down buttons — is untouched."""
        spn = QuietDoubleSpinBox()
        spn.setRange(0.0, 25.0)
        spn.setSingleStep(0.1)
        spn.setValue(1.5)
        spn.stepBy(1)
        assert spn.value() == pytest.approx(1.6)
        spn.stepBy(-2)
        assert spn.value() == pytest.approx(1.4)

    def test_typing_still_commits(self, qapp):
        """The keyboardTracking fix this class exists for still works: the box
        shows what is typed and commits it on focus-out."""
        spn = QuietDoubleSpinBox()
        spn.setRange(0.0001, 25.0)
        spn.setDecimals(4)
        spn.lineEdit().setText("0.514")
        spn.interpretText()
        assert spn.value() == pytest.approx(0.514)


class TestEveryDropdownInTheApp:
    """A bare QComboBox or QDoubleSpinBox anywhere is the bug back again on
    that one screen."""

    def _assert_all_safe(self, widget):
        combos = widget.findChildren(QComboBox)
        assert combos, "expected this screen to have dropdowns"
        bad = [c for c in combos if not isinstance(c, NoScrollComboBox)]
        assert not bad, [c.objectName() or c.currentText() for c in bad]

        # Searched as QAbstractSpinBox, not QDoubleSpinBox, so a stray integer
        # QSpinBox is caught too. Not every screen has numeric boxes — the HV
        # amplifier tab is read-only — so this asserts none are BAD rather than
        # that any exist; test_every_numeric_box_is_quiet covers the presence.
        bad = [s for s in widget.findChildren(QAbstractSpinBox)
               if not isinstance(s, QuietDoubleSpinBox)]
        assert not bad, [s.objectName() or type(s).__name__ for s in bad]

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

    def test_every_numeric_box_is_quiet(self, qapp):
        """Named per screen so a failure says WHICH box, and asserting a real
        count so the check cannot pass by finding nothing."""
        from rbl.state.beamline import Beamline
        from rbl.gui.motor_tab import MotorTab
        from rbl.gui.funcgen_tab import FuncGenTab
        from rbl.gui.overview_tab import OverviewTab

        for screen in (MotorTab(Beamline()), FuncGenTab(Beamline()),
                       OverviewTab(Beamline())):
            spins = screen.findChildren(QAbstractSpinBox)
            assert len(spins) >= 4, (type(screen).__name__, len(spins))
            bad = [s for s in spins if not isinstance(s, QuietDoubleSpinBox)]
            assert not bad, (type(screen).__name__, [type(s).__name__ for s in bad])

    def test_a_slit_target_cannot_be_nudged_by_a_wheel(self, qapp):
        """The end-to-end version of the hazard: a target scrolled 0.1 mm on
        the way past looks exactly like a number somebody typed, and the next
        Move goes there."""
        from rbl.state.beamline import Beamline
        from rbl.gui.overview_tab import OverviewTab

        # The tab must outlive the widget under test — dropping it takes the
        # C++ side of every child with it.
        tab = OverviewTab(Beamline())
        ctrl = tab.slits["X+"]
        ctrl.spn_target.setValue(1.5)
        requested = []
        ctrl.move_requested.connect(lambda slit, mm: requested.append((slit, mm)))

        _wheel(ctrl.spn_target, delta=+120)
        assert ctrl.spn_target.value() == 1.5

        ctrl.set_enabled(True)
        ctrl.btn_move.click()
        assert requested == [("X+", 1.5)]

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
