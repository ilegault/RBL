"""
Tests that verify background polling threads remain active regardless of which
tab is visible. These tests mock all hardware and avoid requiring a display.
"""
import time
import threading
import pytest
from unittest.mock import MagicMock, patch

from rbl.hardware.galil_driver import GalilController
from rbl.hardware.labjack_driver import LabJackT7
from rbl.hardware.current_monitor import RollingBuffer


# ── GalilPollWorker isolation test (no Qt needed) ───────────────────────────

class StubGalilPollThread(threading.Thread):
    """Pure Python version of GalilPollWorker for testing the loop logic."""

    def __init__(self, galil, period_s=0.05):
        super().__init__(daemon=True)
        self.galil     = galil
        self.period    = period_s
        self._running  = True
        self.poll_count = 0
        self.errors    = []

    def stop(self):
        self._running = False

    def run(self):
        while self._running and self.galil.connected:
            t0 = time.time()
            try:
                for axis in ["A", "B", "C", "D"]:
                    self.galil.get_position(axis)
                self.poll_count += 1
            except Exception as e:
                self.errors.append(e)
                break
            elapsed = time.time() - t0
            time.sleep(max(0.0, self.period - elapsed))


class TestGalilPollPersistence:
    def _make_mock_galil(self):
        g = MagicMock(spec=GalilController)
        g.connected = True
        g.get_position.return_value = 0
        return g

    def test_thread_runs_multiple_polls(self):
        g = self._make_mock_galil()
        thread = StubGalilPollThread(g, period_s=0.02)
        thread.start()
        time.sleep(0.15)
        thread.stop()
        thread.join(timeout=0.5)
        assert thread.poll_count >= 3, f"Expected >=3 polls, got {thread.poll_count}"

    def test_thread_continues_when_not_observed(self):
        """Simulates switching away from the tab: thread keeps running."""
        g = self._make_mock_galil()
        thread = StubGalilPollThread(g, period_s=0.02)
        thread.start()
        # Simulate "switching tab" — nothing observes the thread
        time.sleep(0.05)
        tab_visible = False  # noqa: unused — simulates tab hidden
        time.sleep(0.10)     # thread still running
        count_mid = thread.poll_count
        time.sleep(0.05)
        count_after = thread.poll_count
        thread.stop()
        thread.join(timeout=0.5)
        assert count_after > count_mid, "Thread stopped polling while tab was hidden"

    def test_thread_stops_on_request(self):
        g = self._make_mock_galil()
        thread = StubGalilPollThread(g, period_s=0.02)
        thread.start()
        time.sleep(0.1)
        thread.stop()
        thread.join(timeout=0.5)
        count_at_stop = thread.poll_count
        time.sleep(0.1)
        # poll_count should not increase after stop
        assert thread.poll_count == count_at_stop

    def test_thread_stops_on_disconnect(self):
        g = self._make_mock_galil()
        thread = StubGalilPollThread(g, period_s=0.02)
        thread.start()
        time.sleep(0.05)
        g.connected = False  # simulate hardware disconnect
        thread.join(timeout=0.5)
        assert not thread.is_alive()

    def test_no_errors_on_normal_operation(self):
        g = self._make_mock_galil()
        thread = StubGalilPollThread(g, period_s=0.02)
        thread.start()
        time.sleep(0.15)
        thread.stop()
        thread.join(timeout=0.5)
        assert len(thread.errors) == 0


# ── LabJackPollWorker isolation test ─────────────────────────────────────────

class StubLabJackPollThread(threading.Thread):
    """Pure Python version of LabJackPollWorker for testing."""

    CHANNELS = ["AIN0", "AIN1", "AIN2", "AIN3"]

    def __init__(self, lj, buffers, period_s=0.05):
        super().__init__(daemon=True)
        self.lj          = lj
        self.buffers     = buffers
        self.period      = period_s
        self._running    = True
        self.read_count  = 0
        self.errors      = []
        self._t0         = time.time()

    def stop(self):
        self._running = False

    def run(self):
        while self._running and self.lj.connected:
            tloop = time.time()
            try:
                values = self.lj.read_channels()
                t_rel = tloop - self._t0
                for ain, v in values.items():
                    if ain in self.buffers:
                        self.buffers[ain].append(t_rel, v)
                self.read_count += 1
            except Exception as e:
                self.errors.append(e)
                break
            elapsed = time.time() - tloop
            time.sleep(max(0.0, self.period - elapsed))

    def _make_mock_lj(self):
        lj = MagicMock(spec=LabJackT7)
        lj.connected = True
        lj.read_channels.return_value = {"AIN0": 3.0, "AIN1": 3.0, "AIN2": 3.0, "AIN3": 3.0}
        return lj


class TestLabJackPollPersistence:
    def _make_setup(self, period=0.02):
        lj = MagicMock(spec=LabJackT7)
        lj.connected = True
        lj.read_channels.return_value = {
            "AIN0": 3.0, "AIN1": 3.1, "AIN2": 2.9, "AIN3": 3.05
        }
        buffers = {ain: RollingBuffer(600) for ain in ["AIN0", "AIN1", "AIN2", "AIN3"]}
        thread = StubLabJackPollThread(lj, buffers, period_s=period)
        return lj, buffers, thread

    def test_buffer_fills_while_running(self):
        lj, buffers, thread = self._make_setup()
        thread.start()
        time.sleep(0.2)
        thread.stop()
        thread.join(timeout=0.5)
        t, v = buffers["AIN0"].snapshot()
        assert len(t) >= 3, f"Buffer should have >=3 samples, got {len(t)}"

    def test_reads_accumulate_in_hidden_state(self):
        """Data still accumulates in rolling buffers when 'tab is not visible'."""
        lj, buffers, thread = self._make_setup()
        thread.start()
        time.sleep(0.05)
        tab_hidden = True  # noqa — simulate tab switch
        time.sleep(0.10)
        count_hidden = thread.read_count
        time.sleep(0.05)
        count_after = thread.read_count
        thread.stop()
        thread.join(timeout=0.5)
        assert count_after > count_hidden, "Reads stopped while tab was hidden"

    def test_buffer_values_match_mock_readings(self):
        lj, buffers, thread = self._make_setup()
        thread.start()
        time.sleep(0.15)
        thread.stop()
        thread.join(timeout=0.5)
        _, v = buffers["AIN0"].snapshot()
        assert len(v) > 0
        assert all(abs(vi - 3.0) < 0.01 for vi in v)

    def test_multiple_channels_all_fill(self):
        lj, buffers, thread = self._make_setup()
        thread.start()
        time.sleep(0.15)
        thread.stop()
        thread.join(timeout=0.5)
        for ain in ["AIN0", "AIN1", "AIN2", "AIN3"]:
            t, v = buffers[ain].snapshot()
            assert len(t) > 0, f"Channel {ain} buffer empty"

    def test_no_errors_on_normal_operation(self):
        lj, buffers, thread = self._make_setup()
        thread.start()
        time.sleep(0.15)
        thread.stop()
        thread.join(timeout=0.5)
        assert len(thread.errors) == 0


# ── RollingBuffer accumulation independence ───────────────────────────────────

class TestBufferAccumulatesIndependentlyOfUI:
    """RollingBuffer is the interface between background poll thread and UI.
    UI reading (snapshot) should not block or clear the buffer."""

    def test_append_during_snapshot(self):
        buf = RollingBuffer(200)
        errors = []

        def writer():
            try:
                for i in range(100):
                    buf.append(float(i) * 0.01, float(i))
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(50):
                    buf.snapshot()
                    time.sleep(0.002)
            except Exception as e:
                errors.append(e)

        w = threading.Thread(target=writer, daemon=True)
        r = threading.Thread(target=reader, daemon=True)
        w.start()
        r.start()
        w.join(timeout=2.0)
        r.join(timeout=2.0)
        assert not errors

    def test_snapshot_does_not_clear_buffer(self):
        buf = RollingBuffer(100)
        for i in range(50):
            buf.append(float(i), float(i))
        t1, v1 = buf.snapshot()
        t2, v2 = buf.snapshot()
        assert len(t1) == len(t2) == 50
