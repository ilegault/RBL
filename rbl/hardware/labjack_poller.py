"""
labjack_poller.py
The single, shared LabJack T7 poll thread.

There is exactly one physical T7 and therefore exactly one instance of this
worker in the running app. MainWindow owns it. Every tab that needs analog data
subscribes to its `reading` signal and filters out the AINs it cares about.

Do NOT construct a second LabJackT7 or a second poller. LJM will let you open a
second handle to the same device, and the two threads' eReadNames calls will
interleave on the USB endpoint and corrupt each other's data.
"""
import time

from PySide6.QtCore import QThread, Signal

from rbl.hardware.labjack_driver import LabJackT7
from rbl.config import hardware_config as SC


class LabJackPollWorker(QThread):
    """Polls all 14 AINs at a fixed period and emits one dict per cycle.

    reading: (t_seconds_since_start, {"AIN0": volts, ..., "AIN13": volts})
    """
    reading = Signal(float, dict)   # t_seconds, {AIN: volts}
    error   = Signal(str)

    def __init__(self, lj: LabJackT7, period_s: float = 0.1):
        super().__init__()
        self.lj       = lj
        self.period   = period_s
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        t0 = time.time()
        channels = tuple(SC.ALL_AIN_NAMES)   # 12 channels, one round trip
        while self._running and self.lj.connected:
            tloop = time.time()
            try:
                values = self.lj.read_channels(channels)
                self.reading.emit(tloop - t0, values)
            except Exception as e:
                self.error.emit(str(e))
                break
            elapsed   = time.time() - tloop
            remaining = max(0.0, self.period - elapsed)
            self.msleep(int(remaining * 1000))


# --- Self-test ---------------------------------------------------------------

if __name__ == "__main__":
    print(f"Poll worker will read {len(SC.ALL_AIN_NAMES)} channels per cycle:")
    print(f"  {SC.ALL_AIN_NAMES}")
    assert len(SC.ALL_AIN_NAMES) == 12
    assert len(set(SC.ALL_AIN_NAMES)) == 12
    print("[OK] labjack_poller imports and channel set is sane")
