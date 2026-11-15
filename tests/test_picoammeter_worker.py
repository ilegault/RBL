"""
test_picoammeter_worker.py
Unit tests for PicoammeterWorker QThread lifecycle, polling rates, and error recovery.

WHY THESE TESTS NEVER PUMP THE EVENT LOOP
-----------------------------------------
These tests once called QCoreApplication.processEvents() while they waited, and
connected the worker's signals to plain callables (``readings.append``, lambdas).
PySide queues such a connection to the main thread, so every emission became an
event the main thread had to deliver. Two things then went wrong together, and
together they killed a CI worker with "Fatal Python error: Aborted" / "Windows
fatal exception: access violation" inside matplotlib (jobs 132270457599,
132011959569):

1. processEvents() runs EVERY pending event in the process, not just this
   test's. Under ``pytest -n auto --dist loadfile`` this file shares a worker
   process with GUI tests that ran earlier and left matplotlib canvases alive
   with a ``draw_idle`` timer pending. Waiting here redrew other tests' figures.
2. In acquiring mode the worker polls back to back (ticket 82). Against a
   MagicMock instrument that answers instantly it emitted ~50 000 readings a
   second, each one a queued event plus a recorded mock call. While the main
   thread was busy in those foreign redraws (and starved of the GIL by the
   spinning worker) the queue grew by ~125 MB a second.

So: every connection below is a DirectConnection (the slot runs on the worker
thread; ``list.append`` is atomic under the GIL), ``_wait_until`` only polls,
and the fake instrument takes ``_FAKE_READ_S`` per read, as a real 6482 does,
because "back to back" means the instrument sets the pace, not that a read is
free. What these tests assert about the worker is unchanged.
"""
import time
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
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


# Slots run on the worker thread: nothing is queued to the main thread, so the
# tests never need to run the main event loop (see the module docstring).
_DIRECT = Qt.ConnectionType.DirectConnection

# One read of the fake 6482. A real one takes milliseconds per reading even at its
# fastest integration time; a fake that answers in microseconds makes acquiring
# mode a busy loop that no real instrument produces.
_FAKE_READ_S = 0.002
_FAKE_LINE = "+1.234567E-06,+0.123456,+00000000"


def _paced_read(line=_FAKE_LINE):
    def _read():
        time.sleep(_FAKE_READ_S)
        return line
    return _read


def _wait_until(predicate, timeout_s=10.0):
    """Poll predicate. Never runs the event loop: that would run other tests'
    leftover Qt timers in this process (see the module docstring)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
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
        assert worker.get_poll_interval() == 0.0
        assert worker.get_poll_interval() == CUP_POLL_INTERVAL_ACQUIRING_S

        worker.set_acquiring(False)
        assert worker.get_poll_interval() == CUP_POLL_INTERVAL_IDLE_S

        worker.set_poll_interval(0.2)
        assert worker.get_poll_interval() == 0.2

        worker.set_poll_interval(0.0)
        assert worker.get_poll_interval() == 0.0

    @patch("rbl.hardware.picoammeter_worker.Keithley6482")
    def test_worker_acquiring_skips_sleep_interruptible(self, mock_driver_cls, qapp):
        mock_inst = MagicMock()
        mock_inst.idn.return_value = "Keithley model 6482"
        mock_inst.protocol_mode = 1
        mock_inst.read_raw.side_effect = _paced_read()
        mock_driver_cls.return_value = mock_inst

        worker = PicoammeterWorker("GPIB0::14::INSTR")
        worker.set_acquiring(True)

        readings: list[Keithley6482Reading] = []
        worker.reading_ready.connect(readings.append, _DIRECT)

        sleep_calls: list[float] = []

        with patch.object(
            PicoammeterWorker, "_sleep_interruptible", side_effect=sleep_calls.append
        ):
            worker.start()
            try:
                assert _wait_until(lambda: len(readings) >= 5, timeout_s=10.0)
            finally:
                worker.stop()
                assert worker.wait(2000)

            assert len(readings) >= 5
            assert not any(d > 0 for d in sleep_calls)

    @patch("rbl.hardware.picoammeter_worker.Keithley6482")
    def test_worker_idle_sleeps_idle_interval_between_reads(self, mock_driver_cls, qapp):
        mock_inst = MagicMock()
        mock_inst.idn.return_value = "Keithley model 6482"
        mock_inst.protocol_mode = 1
        mock_inst.read_raw.side_effect = _paced_read()
        mock_driver_cls.return_value = mock_inst

        worker = PicoammeterWorker("GPIB0::14::INSTR")
        worker.set_acquiring(False)

        readings: list[Keithley6482Reading] = []
        worker.reading_ready.connect(readings.append, _DIRECT)

        sleep_calls: list[float] = []

        def fake_sleep(duration_s: float):
            sleep_calls.append(duration_s)
            time.sleep(0.001)

        with patch.object(PicoammeterWorker, "_sleep_interruptible", side_effect=fake_sleep):
            worker.start()
            try:
                assert _wait_until(lambda: len(readings) >= 3, timeout_s=10.0)
            finally:
                worker.stop()
                assert worker.wait(2000)

            assert len(readings) >= 3
            assert len(sleep_calls) >= 3
            assert all(d == pytest.approx(CUP_POLL_INTERVAL_IDLE_S) for d in sleep_calls)

    @patch("rbl.hardware.picoammeter_worker.Keithley6482")
    def test_worker_stop_acquiring_within_timeout(self, mock_driver_cls, qapp):
        mock_inst = MagicMock()
        mock_inst.idn.return_value = "Keithley model 6482"
        mock_inst.protocol_mode = 1
        mock_inst.read_raw.side_effect = _paced_read()
        mock_driver_cls.return_value = mock_inst

        worker = PicoammeterWorker("GPIB0::14::INSTR")
        worker.set_acquiring(True)

        readings: list[Keithley6482Reading] = []
        worker.reading_ready.connect(readings.append, _DIRECT)

        worker.start()
        try:
            assert _wait_until(lambda: len(readings) > 0, timeout_s=10.0)
        finally:
            worker.stop()
            stopped = worker.wait(2000)

        assert stopped is True
        assert not worker.isRunning()

    @patch("rbl.hardware.picoammeter_worker.Keithley6482")
    def test_worker_run_and_stop_lifecycle(self, mock_driver_cls, qapp):
        mock_inst = MagicMock()
        mock_inst.idn.return_value = "Keithley model 6482"
        mock_inst.protocol_mode = 1
        mock_inst.read_raw.side_effect = _paced_read()
        mock_driver_cls.return_value = mock_inst

        with patch("rbl.hardware.picoammeter_worker.CUP_RECONNECT_BACKOFF_S", [0.01, 0.01]):
            worker = PicoammeterWorker("GPIB0::14::INSTR", poll_interval_s=0.01)

            readings: list[Keithley6482Reading] = []
            raws: list[tuple[str, float]] = []
            connected_events: list[tuple[bool, str]] = []

            worker.reading_ready.connect(readings.append, _DIRECT)
            worker.raw_ready.connect(lambda raw, t: raws.append((raw, t)), _DIRECT)
            worker.connected_changed.connect(
                lambda conn, idn: connected_events.append((conn, idn)), _DIRECT
            )

            worker.start()

            try:
                # Wait until at least one reading is received
                assert _wait_until(lambda: len(readings) > 0, timeout_s=10.0)

                assert connected_events[0][0] is True
                assert "6482" in connected_events[0][1]
                assert readings[0].current == pytest.approx(1.234567e-06)
                assert len(raws) > 0
            finally:
                # Stop worker
                worker.stop()
                worker.wait(2000)

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
            time.sleep(_FAKE_READ_S)
            return "+1.000000E-06,+0.100000,+00000000"

        mock_inst.read_raw.side_effect = _mock_read
        mock_driver_cls.return_value = mock_inst

        worker = PicoammeterWorker("GPIB0::14::INSTR", poll_interval_s=0.01)

        errors: list[str] = []
        worker.error.connect(errors.append, _DIRECT)

        with patch("rbl.hardware.picoammeter_worker.CUP_RECONNECT_BACKOFF_S", [0.01, 0.01]):
            worker.start()
            try:
                assert _wait_until(lambda: len(errors) > 0, timeout_s=10.0)
            finally:
                worker.stop()
                worker.wait(2000)

        assert any("GPIB bus communication failure" in e for e in errors)



    @patch("rbl.hardware.picoammeter_worker.Keithley6482")
    def test_connect_failure_message_carries_hint(self, mock_driver_cls, qapp):
        mock_driver_cls.side_effect = Exception(
            "VI_ERROR_LIBRARY_NFOUND (-1073807202): A code library required by VISA "
            "could not be located or loaded."
        )
        worker = PicoammeterWorker("GPIB0::2::INSTR", poll_interval_s=0.01)
        errors: list[str] = []
        worker.error.connect(errors.append, _DIRECT)

        with patch("rbl.hardware.picoammeter_worker.CUP_RECONNECT_BACKOFF_S", [0.01]):
            worker.start()
            try:
                assert _wait_until(lambda: len(errors) > 0, timeout_s=10.0)
            finally:
                worker.stop()
                worker.wait(2000)

        assert "VI_ERROR_LIBRARY_NFOUND" in errors[0]
        assert "Keysight IO Libraries Suite" in errors[0]
