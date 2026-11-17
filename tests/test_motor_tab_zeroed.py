"""Tests for Beamline-owned per-axis zeroed state and MotorTab triggers."""
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from rbl.config import hardware_config as SC
from rbl.gui.motor_tab import MotorTab, dp_axes
from rbl.hardware.galil_driver import GalilError
from rbl.state.beamline import Beamline


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_reconnect_clears_zeroed(qapp, monkeypatch):
    beamline = Beamline()
    galil = MagicMock(connected=False)
    galil.connect.side_effect = lambda _ip: setattr(galil, "connected", True)
    galil.model_info.return_value = "DMC4103"
    galil.get_soft_limits.return_value = {"forward_counts": 0, "back_counts": 0}
    beamline.galil = galil
    monkeypatch.setattr("rbl.gui.motor_tab.GalilPollWorker", MagicMock())

    tab = MotorTab(beamline)
    for axis in SC.AXIS_LETTERS:
        beamline.mark_zeroed(axis)
    assert beamline.all_zeroed() is True

    tab.btn_connect.click()

    assert beamline.all_zeroed() is False
    assert beamline.is_zeroed("A") is False


def test_dp_axes_parses_the_console_forms():
    assert dp_axes("DPA=0") == "A"
    assert dp_axes("DP C=0") == "C"
    assert dp_axes("DP 0,,5") == "AC"
    assert dp_axes("DP ,,,0") == "D"
    assert dp_axes("DP") == ""
    assert dp_axes("MG _RPA") == ""


def test_console_dp_marks_the_named_axis_zeroed(qapp):
    beamline = Beamline()
    galil = MagicMock(connected=True)
    galil.cmd.return_value = ""
    beamline.galil = galil
    tab = MotorTab(beamline)

    tab.manual_cmd.setText("DP ,0")
    tab.manual_cmd.returnPressed.emit()

    assert beamline.is_zeroed("B") is True
    assert beamline.is_zeroed("A") is False

    beamline.clear_zeroed()
    galil.cmd.side_effect = GalilError(1, "syntax error")
    tab.manual_cmd.setText("DP ,0")
    tab.manual_cmd.returnPressed.emit()

    assert beamline.is_zeroed("B") is False
    assert beamline.is_zeroed("A") is False
