"""
Tests for per-axis switch status labels on the Stepper Motors tab.

Each slit axis's home switch and reverse limit trip at the same point on this
beamline, but they are separate inputs. The status label must name whichever
switch(es) are actually tripped rather than lumping them as "REV/HOME LIMIT
active".
"""
import pytest
from PySide6.QtWidgets import QApplication

from rbl.gui.motor_tab import MotorTab
from rbl.state.beamline import Beamline


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _axis_state(*, forward=False, reverse=False, home=False):
    return {
        "pos": -6537,
        "moving": False,
        "enabled": True,
        "switches": {
            "forward_switch": forward,
            "reverse_switch": reverse,
            "home_switch": home,
        },
    }


def test_status_names_both_switches_when_both_trip(qapp):
    tab = MotorTab(Beamline())
    tab.axes["B"].update_state(_axis_state(reverse=True, home=True))
    assert tab.axes["B"].lbl_status.text() == "HOME + REV LIMIT"


def test_status_names_the_reverse_limit_alone(qapp):
    tab = MotorTab(Beamline())
    tab.axes["B"].update_state(_axis_state(reverse=True, home=False))
    assert tab.axes["B"].lbl_status.text() == "REV LIMIT"


def test_status_names_the_home_switch_alone(qapp):
    tab = MotorTab(Beamline())
    tab.axes["B"].update_state(_axis_state(reverse=False, home=True))
    assert tab.axes["B"].lbl_status.text() == "HOME"

    tab.axes["B"].update_state(_axis_state(reverse=False, home=False))
    assert tab.axes["B"].lbl_status.text() == "Idle  [enabled]"
