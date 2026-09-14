"""
test_picoammeter_worker.py
Unit tests for PicoammeterWorker QThread lifecycle, polling rates, and error recovery.
"""
import time
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from rbl.config.cup_config import (
    CUP_POLL_INTERVAL_ACQUIRING_S,
    CUP_POLL_INTERVAL_IDLE_S,
)
from rbl.hardware.keithley6482_driver import Keithley6482Reading
from rbl.hardware.picoammeter_worker import PicoammeterWorker


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _wait_until(predicate, timeout_s=3.0):
    start = time.time()
    while time.time() - start < timeout_s:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class TestPicoammeterWorker:
    def test_worker_initialization(self):
        worker = PicoammeterWorker("GPIB0::14::INSTR", poll_interval_s=0.5)
        assert worker.get_poll_interval() == 0.5
        assert not worker.isRunning()

    def test_set_poll_interval_and_acquiring(self):
        worker = PicoammeterWorker()
        assert worker.get_poll_interval() == CUP_POLL_INTERVAL_IDLE_S

        worker.set_acquiring(True)
        assert worker.get_poll_interval() == CUP_POLL_INTERVAL_ACQUIRING_S

        worker.set_acquiring(False)
        assert worker.get_poll_interval() == CUP_POLL_INTERVAL_IDLE_S

        worker.set_poll_interval(0.2)
        assert worker.get_poll_interval() == 0.2

    @patch("rbl.hardware.picoammeter_worker.Keithley6482")
    def test_worker_run_and_stop_lifecycle(self, mock_driver_cls, qapp):
        mock_inst = MagicMock()
        mock_inst.idn.return_value = "Keithley model 6482"
        mock_inst.protocol_mode = 1
        mock_inst.read_raw.return_value = "+1.234567E-06,+0.123456,+00000000"
        mock_driver_cls.return_value = mock_inst

        worker = PicoammeterWorker("GPIB0::14::INSTR", poll_interval_s=0.01)

        readings: list[Keithley6482Reading] = []
        raws: list[tuple[str, float]] = []
        connected_events: list[tuple[bool, str]] = []

        worker.reading_ready.connect(readings.append)
        worker.raw_ready.connect(lambda raw, t: raws.append((raw, t)))
        worker.connected_changed.connect(lambda conn, idn: connected_events.append((conn, idn)))

        worker.start()

        # Wait until at least one reading is received
        assert _wait_until(lambda: len(readings) > 0, timeout_s=3.0)

        assert connected_events[0][0] is True
        assert "6482" in connected_events[0][1]
        assert readings[0].current == pytest.approx(1.234567e-06)
        assert len(raws) > 0

        # Stop worker
        worker.stop()
        worker.wait(1000)
        assert not worker.isRunning()
        mock_inst.close.assert_called()

    @patch("rbl.hardware.picoammeter_worker.Keithley6482")
    def test_worker_handles_read_exception(self, mock_driver_cls, qapp):
        mock_inst = MagicMock()
        mock_inst.idn.return_value = "Keithley model 6482"
        mock_inst.protocol_mode = 1

        call_count = 0

        def _mock_read():
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("GPIB bus communication failure")
            return "+1.000000E-06,+0.100000,+00000000"

        mock_inst.read_raw.side_effect = _mock_read
        mock_driver_cls.return_value = mock_inst

        worker = PicoammeterWorker("GPIB0::14::INSTR", poll_interval_s=0.01)

        errors: list[str] = []
        worker.error.connect(errors.append)

        with patch("rbl.hardware.picoammeter_worker.CUP_RECONNECT_BACKOFF_S", [0.01, 0.01]):
            worker.start()
            assert _wait_until(lambda: len(errors) > 0, timeout_s=3.0)

            worker.stop()
            worker.wait(1000)

        assert any("GPIB bus communication failure" in e for e in errors)

