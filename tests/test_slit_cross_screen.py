"""
One slit, two screens, one story.

A slit can be moved from the Stepper Motors tab or from the Overview tab, and
until this wiring existed the two screens only heard about their own moves. A
move commanded on the Overview reached the Galil without ever appearing in the
Command Console — the app's record of what was sent — and without touching the
Stepper Motors tab's Target box, which went on showing whatever was in it
before. The operator's own action was invisible on the screen that exists to
show it.

Both directions now travel through Beamline (motor_logged + slit_target_
changed), which is already the single path to the controller. These tests
assert that path from both ends, with a fake Galil so nothing needs hardware.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from rbl.config import hardware_config as SC
from rbl.state.beamline import Beamline

# One half-step, in mm. A stepper lands on whole counts, so every commanded
# target is quantised to within one of these — 6.000 mm is commanded as 3653
# counts and reached at 6.001 mm. Both screens are told the ACHIEVABLE target,
# so comparisons against a typed number carry this tolerance.
STEP_MM = 1.0 / SC.STEPS_PER_MM["A"]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeGalil:
    """Just enough Galil to accept a move and remember it."""

    def __init__(self):
        self.connected = True
        self.moves = []

    def move_absolute(self, axis, counts):
        self.moves.append((axis, counts))


@pytest.fixture
def screens(qapp):
    """A motor tab and an overview tab on ONE Beamline, as the app builds them."""
    from rbl.gui.motor_tab import MotorTab
    from rbl.gui.overview_tab import OverviewTab

    beamline = Beamline()
    beamline.galil = _FakeGalil()
    motors = MotorTab(beamline)
    overview = OverviewTab(beamline)
    overview._visible = True
    return motors, overview, beamline


# ---- Overview -> Stepper Motors ---------------------------------------------

def test_overview_move_reaches_the_command_console(screens):
    """The console is the record of what was sent; a move missing from it
    reads as a move that never happened."""
    motors, overview, beamline = screens
    beamline.move_slit("X+", 3.0)
    text = motors.console.toPlainText()
    assert "PA A=" in text and "X+" in text and "mm" in text


def test_both_screens_are_told_the_same_achievable_target(screens):
    """A stepper lands on whole counts. Publishing the typed number instead
    would leave every caret a fraction of a step off the position that
    eventually arrives under it, and would disagree with the Stepper Motors
    tab, which has always worked in counts."""
    motors, overview, beamline = screens
    beamline.move_slit("X+", 6.0)
    reachable = SC.counts_to_mm("A", SC.mm_to_counts("A", 6.0))
    assert reachable != 6.0                    # the quantisation is real
    # Both boxes show three decimals, so they hold the achievable target to
    # their own resolution rather than to the full float.
    assert motors.axes["A"].spn_target.value() == pytest.approx(
        reachable, abs=5e-4)
    assert overview.slits["X+"].spn_target.value() == pytest.approx(
        reachable, abs=5e-4)


def test_overview_move_updates_the_stepper_tab_target_box(screens):
    motors, overview, beamline = screens
    beamline.move_slit("Y-", 4.5)
    assert motors.axes["D"].spn_target.value() == pytest.approx(4.5, abs=STEP_MM)


def test_overview_move_lands_in_the_stepper_tabs_current_unit(screens):
    """The other tab can be showing counts; the target still has to be right."""
    motors, overview, beamline = screens
    motors.axes["A"].cbo_target_unit.setCurrentText("counts")
    beamline.move_slit("X+", 2.0)
    assert motors.axes["A"].spn_target.value() == pytest.approx(
        SC.mm_to_counts("A", 2.0))


def test_a_refused_move_is_logged_too(screens):
    """An attempt that failed is exactly what an operator hunts for later."""
    motors, overview, beamline = screens
    beamline.galil.connected = False
    assert beamline.move_slit("X+", 3.0) is False
    assert "not connected" in motors.console.toPlainText()


# ---- Stepper Motors -> Overview ---------------------------------------------

def test_stepper_tab_move_marks_the_target_on_the_overview_bar(screens):
    motors, overview, beamline = screens
    motors.axes["C"].cbo_target_unit.setCurrentText("mm")
    motors.axes["C"].spn_target.setValue(6.0)
    motors.axes["C"]._move_absolute()

    assert overview.slits["Y+"].spn_target.value() == pytest.approx(
        6.0, abs=STEP_MM)
    assert overview.slits["Y+"].bar.track._target == pytest.approx(
        6.0 / SC.SLIT_DISPLAY_MAX_MM, abs=STEP_MM)


def test_stepper_tab_move_still_reaches_the_controller(screens):
    """Routing the log through Beamline must not have cost us the move."""
    motors, overview, beamline = screens
    motors.axes["A"].cbo_target_unit.setCurrentText("mm")
    motors.axes["A"].spn_target.setValue(1.5)
    motors.axes["A"]._move_absolute()
    assert beamline.galil.moves == [("A", SC.mm_to_counts("A", 1.5))]


def test_stepper_tab_move_is_logged_once_not_twice(screens):
    """Both the panel and the console-forwarding signal used to want this line."""
    motors, overview, beamline = screens
    motors.axes["B"].cbo_target_unit.setCurrentText("mm")
    motors.axes["B"].spn_target.setValue(2.5)
    motors.axes["B"]._move_absolute()
    assert motors.console.toPlainText().count("PA B=") == 1


# ---- The 25 mm range ---------------------------------------------------------

def test_the_overview_accepts_a_target_the_other_tab_would_accept(screens):
    """A display convention that silently caps what can be commanded is not a
    display convention. The old 10 mm ceiling refused targets the Stepper
    Motors tab took without complaint."""
    motors, overview, beamline = screens
    overview.slits["X-"].spn_target.setValue(22.0)
    assert overview.slits["X-"].spn_target.value() == pytest.approx(22.0)
    assert SC.SLIT_DISPLAY_MAX_MM == 25.0
