"""
Edge-return routine: repeatability measurement for slit homing without
redefining zero.

Each cycle backs out away from home, runs one HM pass back onto the home
switch at a single pass speed, checks stop code 10, and records the commanded
position (_RP) without calling DP=0.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import MagicMock, call

import pytest
from PySide6.QtWidgets import QApplication

from rbl.config import hardware_config as SC
from rbl.hardware import galil_workers
from rbl.hardware.galil_driver import GalilController
from rbl.hardware.galil_workers import EdgeReturnRoutine, EdgeReturnWorker


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(galil_workers.time, "sleep", lambda *a, **k: None)


def _galil(home=False, reverse=False, moving=False):
    g = MagicMock(spec=GalilController)
    g.connected = True
    g.get_switch_states.return_value = {
        "home_switch": home,
        "reverse_switch": reverse,
        "forward_switch": False,
    }
    g.is_moving.return_value = moving
    g.get_stop_code.return_value = SC.HOME_STOP_CODE
    return g


def test_edge_return_collects_one_reading_per_cycle():
    g = _galil()
    g.get_position.side_effect = [1000, 1002, 999]

    ok, msg, readings = EdgeReturnRoutine(
        g, "A", 58, cycles=3, out_counts=2000
    ).run()

    assert ok is True
    assert msg == "A: 3 edge returns at 58 cps"
    assert readings == [1000, 1002, 999]


def test_edge_return_never_defines_zero():
    g = _galil()
    g.get_position.side_effect = [1000, 1002, 999]

    ok, _, _ = EdgeReturnRoutine(g, "A", 58, cycles=3, out_counts=2000).run()

    assert ok is True
    g.define_zero.assert_not_called()
    g.define_zero_multi.assert_not_called()
    assert g.move_relative.call_args_list == [call("A", 2000)] * 3
    assert g.begin_home.call_args_list == [call("A", 58, fine_speed=58)] * 3


def test_edge_return_stops_on_a_bad_stop_code():
    g = _galil()
    g.get_stop_code.side_effect = [10, 3]
    g.get_position.side_effect = [1000, 1002]

    ok, msg, readings = EdgeReturnRoutine(
        g, "A", 58, cycles=3, out_counts=2000
    ).run()

    assert ok is False
    assert "cycle 2/3 did not stop on the home switch (stop code 3)" in msg
    assert len(readings) == 1
    assert readings == [1000]


def test_edge_return_cancel_keeps_the_readings_so_far():
    g = _galil()
    readings_read = 0

    def get_pos(_axis):
        nonlocal readings_read
        readings_read += 1
        return 1000 + readings_read

    g.get_position.side_effect = get_pos

    ok, msg, readings = EdgeReturnRoutine(
        g, "A", 58, cycles=3, out_counts=2000,
        cancelled=lambda: readings_read >= 1,
    ).run()

    assert ok is False
    assert "cancelled after 1 cycles" in msg
    assert len(readings) == 1


def test_edge_return_worker_emits_done_with_readings(qapp):
    g = _galil()
    g.get_position.side_effect = [1000, 1002, 999]

    worker = EdgeReturnWorker(g, "A", 58, cycles=3, out_counts=2000)
    emissions = []
    worker.done.connect(lambda ok, msg, readings: emissions.append((ok, msg, readings)))

    worker.run()

    assert len(emissions) == 1
    ok, msg, readings = emissions[0]
    assert ok is True
    assert msg == "A: 3 edge returns at 58 cps"
    assert readings == [1000, 1002, 999]
