"""
picoammeter_worker.py
QThread worker that polls the Keithley 6482 picoammeter over GPIB and emits readings.

THREADING CONTRACT
------------------
This worker owns the Keithley 6482 PyVISA instrument handle for its lifetime.
No other object may send commands to the picoammeter while this worker is running.
The Beamline (via PicoammeterLinkMixin) owns the worker's lifecycle: it creates the
worker, signals it to stop via stop(), and calls wait() before cleanup.
Communication is strictly via Qt signals.

MISSING HARDWARE AND RECONNECTION
---------------------------------
If the instrument fails to connect or disconnects mid-run:
1. Emits error(msg).
2. Emits connected_changed(False, "").
3. Closes any open VISA handle.
4. Waits according to CUP_RECONNECT_BACKOFF_S ladder, checking for stop() requests.
5. Attempts to reconnect on the next cycle.

POLLING RATES
-------------
- Idle rate: 2 Hz (CUP_POLL_INTERVAL_IDLE_S = 0.5 s) by default.
- Acquiring rate: 10 Hz (CUP_POLL_INTERVAL_ACQUIRING_S = 0.1 s) during active runs.
Rates are safely adjusted at runtime via set_poll_interval() / set_acquiring().
"""
from __future__ import annotations

import logging
import time
from typing import Any

from PySide6.QtCore import QMutex, QMutexLocker, QThread, Signal

from rbl.config.cup_config import (
    CUP_POLL_INTERVAL_ACQUIRING_S,
    CUP_POLL_INTERVAL_IDLE_S,
    CUP_RECONNECT_BACKOFF_S,
    KEITHLEY_6482_DEFAULT_RESOURCE,
)
from rbl.hardware.keithley6482_driver import (
    Keithley6482,
    discover,
)

log = logging.getLogger(__name__)


class PicoammeterWorker(QThread):
    """Polls Keithley 6482 over GPIB on its own thread; emits Keithley6482Reading objects.

    Signals:
        reading_ready(object): Emitted when a new Keithley6482Reading is acquired.
        raw_ready(str, float): Emitted with (raw_scpi_string, host_timestamp).
        error(str): Emitted when an error or communication drop occurs.
        connected_changed(bool, str): Emitted when connection status changes (connected, idn).
    """

    reading_ready = Signal(object)      # Keithley6482Reading
    raw_ready = Signal(str, float)      # (raw_scpi_string, host_timestamp)
    error = Signal(str)
    connected_changed = Signal(bool, str)

    def __init__(
        self,
        resource_name: str | None = None,
        *,
        poll_interval_s: float = CUP_POLL_INTERVAL_IDLE_S,
        parent: Any = None,
    ):
        super().__init__(parent)
        self._resource_name = resource_name
        self._poll_interval_s = float(poll_interval_s)
        self._mutex = QMutex()
        self._running = False
        self._driver: Keithley6482 | None = None

    def stop(self) -> None:
        """Signal the poll loop to exit. Call wait() afterwards."""
        with QMutexLocker(self._mutex):
            self._running = False

    def is_running(self) -> bool:
        """Check if worker is running."""
        with QMutexLocker(self._mutex):
            return self._running

    def set_poll_interval(self, interval_s: float) -> None:
        """Update polling interval in seconds."""
        with QMutexLocker(self._mutex):
            self._poll_interval_s = max(0.01, float(interval_s))

    def set_acquiring(self, acquiring: bool) -> None:
        """Switch between acquiring rate (10 Hz) and idle rate (2 Hz)."""
        interval = CUP_POLL_INTERVAL_ACQUIRING_S if acquiring else CUP_POLL_INTERVAL_IDLE_S
        self.set_poll_interval(interval)

    def get_poll_interval(self) -> float:
        """Return current poll interval in seconds."""
        with QMutexLocker(self._mutex):
            return self._poll_interval_s

    # ── Connection helpers ──────────────────────────────────────────────────

    def _find_resource(self) -> str | None:
        """Resolve target resource name explicitly or via GPIB discovery."""
        if self._resource_name:
            return self._resource_name

        # Auto-discover Keithley on GPIB
        found = discover()
        if found:
            res = found[0]["resource"]
            log.info("picoammeter_worker: auto-discovered %s (%s)", res, found[0].get("idn", ""))
            return res

        # Default fallback
        return KEITHLEY_6482_DEFAULT_RESOURCE

    def _connect_driver(self) -> Keithley6482 | None:
        """Attempt to open VISA session and configure Keithley 6482."""
        res = self._find_resource()
        if not res:
            return None

        try:
            log.info("picoammeter_worker: connecting to Keithley 6482 on %s", res)
            driver = Keithley6482(res)
            idn = driver.idn()
            log.info("picoammeter_worker: connected to %s (%s)", res, idn)
            self.connected_changed.emit(True, idn)
            return driver
        except Exception as exc:
            msg = f"Failed to connect to Keithley 6482 on {res}: {exc}"
            log.warning("picoammeter_worker: %s", msg)
            self.error.emit(msg)
            self.connected_changed.emit(False, "")
            return None

    def _close_driver(self) -> None:
        """Close driver session if open."""
        if self._driver is not None:
            try:
                self._driver.close()
            except Exception:
                log.exception("picoammeter_worker: error closing driver")
            finally:
                self._driver = None
                self.connected_changed.emit(False, "")

    # ── QThread Run Loop ────────────────────────────────────────────────────

    def run(self) -> None:
        """Worker thread entry point."""
        with QMutexLocker(self._mutex):
            self._running = True

        log.info("picoammeter_worker: thread started")
        backoff_idx = 0

        try:
            while self.is_running():
                # Ensure driver is connected
                if self._driver is None:
                    self._driver = self._connect_driver()
                    if self._driver is None:
                        # Wait before retry with backoff schedule
                        backoff_s = CUP_RECONNECT_BACKOFF_S[
                            min(backoff_idx, len(CUP_RECONNECT_BACKOFF_S) - 1)
                        ]
                        backoff_idx += 1
                        log.info("picoammeter_worker: reconnect backoff %.1f s", backoff_s)
                        self._sleep_interruptible(backoff_s)
                        continue
                    else:
                        backoff_idx = 0

                # Read instrument
                try:
                    t_host = time.time()
                    raw = self._driver.read_raw()
                    reading = self._driver.read_reading() if False else None
                    # Parse using driver's protocol mode
                    from rbl.hardware.keithley6482_driver import parse_reading
                    reading = parse_reading(raw, protocol_mode=self._driver.protocol_mode)

                    self.raw_ready.emit(raw, t_host)
                    self.reading_ready.emit(reading)
                except Exception as exc:
                    err_msg = f"Keithley 6482 read error: {exc}"
                    log.warning("picoammeter_worker: %s", err_msg)
                    self.error.emit(err_msg)
                    self._close_driver()
                    continue

                # Sleep between polls
                interval_s = self.get_poll_interval()
                self._sleep_interruptible(interval_s)

        finally:
            log.info("picoammeter_worker: thread exiting, closing driver")
            self._close_driver()

    def _sleep_interruptible(self, duration_s: float) -> None:
        """Sleep for duration_s in small slices so stop() returns promptly."""
        deadline = time.monotonic() + duration_s
        while self.is_running():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.05, remaining))
