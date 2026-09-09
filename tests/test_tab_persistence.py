"""
Tests that verify background polling threads remain active regardless of which
tab is visible. These tests mock all hardware and avoid requiring a display.
"""
import threading
import time
from unittest.mock import MagicMock

from rbl.hardware.current_monitor import RollingBuffer
from rbl.hardware.galil_driver import GalilController
from rbl.hardware.labjack_driver import LabJackT7


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
