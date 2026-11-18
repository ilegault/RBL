"""GalilPollWorker survives isolated poll errors; a streak of them disconnects."""
from unittest.mock import MagicMock, PropertyMock

import pytest
from PySide6.QtWidgets import QApplication

from rbl.gui.motor_tab import MotorTab
from rbl.hardware.galil_workers import GalilPollWorker
from rbl.state.beamline import Beamline


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def run_worker(monkeypatch, fails, connected_checks):
    """fails(call_number) -> True to raise on that get_position call."""
    monkeypatch.setattr(GalilPollWorker, "msleep", lambda self, ms: None)
    galil = MagicMock()
    calls = {"n": 0}

    def get_position(axis):
        calls["n"] += 1
        if fails(calls["n"]):
            raise ConnectionError("Timeout")
        return 0

    galil.get_position.side_effect = get_position
    type(galil).connected = PropertyMock(
        side_effect=[True] * connected_checks + [False] * 50)
    worker = GalilPollWorker(galil)
    seen = {"warning": [], "error": [], "state": []}
    worker.warning.connect(seen["warning"].append)
    worker.error.connect(seen["error"].append)
    worker.state.connect(seen["state"].append)
    worker.run()
    return seen


def test_poll_survives_isolated_errors(monkeypatch):
    seen = run_worker(monkeypatch, lambda n: n <= 2, 12)
    assert seen["warning"] == ["Poll error 1/3: Timeout", "Poll error 2/3: Timeout"]
    assert seen["error"] == []
    assert len(seen["state"]) >= 1


def test_poll_gives_up_after_three_in_a_row(monkeypatch):
    seen = run_worker(monkeypatch, lambda n: True, 12)
    assert seen["warning"] == ["Poll error 1/3: Timeout", "Poll error 2/3: Timeout"]
    assert seen["error"] == ["Timeout"]


def test_a_good_poll_resets_the_error_count(monkeypatch):
    seen = run_worker(monkeypatch, lambda n: n in (1, 2, 7, 8), 12)
    assert seen["error"] == []
    assert seen["warning"] == [
        "Poll error 1/3: Timeout", "Poll error 2/3: Timeout",
        "Poll error 1/3: Timeout", "Poll error 2/3: Timeout",
    ]


def test_poll_warning_reaches_the_console_without_disconnecting(qapp):
    beamline = Beamline()
    beamline.galil = MagicMock(connected=True)
    tab = MotorTab(beamline)
    worker = GalilPollWorker(beamline.galil)
    tab.wire_poll_worker(worker)
    worker.warning.emit("Poll error 1/3: Timeout")
    assert "! Poll error 1/3: Timeout" in tab.console.toPlainText()
    beamline.galil.disconnect.assert_not_called()
