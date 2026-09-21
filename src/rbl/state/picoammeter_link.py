"""
picoammeter_link.py
Beamline's Faraday cup picoammeter half: lifecycle of the Keithley 6482 worker
and publishing of CupState snapshots.

WHY THIS EXISTS
---------------
Modelled on vacuum_link.py and labjack_link.py. The signals (cup_changed, cup_error,
cup_connected, cup_disconnected_evt) are declared on Beamline (rbl/state/beamline.py).
The methods here emit self.<signal> and resolve at runtime because self is always a
Beamline instance. This mixin must not be instantiated on its own.

ONE INSTRUMENT, ONE OWNER
-------------------------
Beamline is the single owner of the Keithley 6482 picoammeter. No widget constructs,
holds, or tears down the worker or driver.

NO UNIT CONVERSION
------------------
The Keithley 6482 returns current already scaled in Amperes. The link layer preserves
this value directly without conversion.

PURE INGESTION SEAM
-------------------
ingest_cup_raw() accepts raw SCPI response strings, parses them via the pure
keithley6482_driver.parse_reading() function, builds a CupState snapshot, and publishes
it via cup_changed. This provides the test seam for tests/payloads.py (CupFeed).
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from rbl.hardware.keithley6482_driver import (
    Keithley6482Reading,
    parse_reading,
)
from rbl.hardware.picoammeter_worker import PicoammeterWorker
from rbl.snapshots import CupState

if TYPE_CHECKING:
    from PySide6.QtCore import SignalInstance

log = logging.getLogger(__name__)


class PicoammeterLinkMixin:
    """Keithley 6482 picoammeter ownership, polling, and snapshot publishing.

    Mixed into Beamline; see module docstring. Requires host class to declare:
    cup_changed, cup_error, cup_connected, cup_disconnected_evt.
    """

    if TYPE_CHECKING:
        cup_changed: SignalInstance
        cup_error: SignalInstance
        cup_connected: SignalInstance
        cup_disconnected_evt: SignalInstance

    def _init_picoammeter(self) -> None:
        """Called from Beamline.__init__ — sets up mixin state without I/O."""
        self._picoammeter_worker: PicoammeterWorker | None = None
        self._picoammeter_resource: str = ""
        self._cup_acquiring: bool = False
        self._last_cup_state: CupState = CupState(connected=False)

    # ── Connection Lifecycle ──────────────────────────────────────────────────

    def connect_picoammeter(self, resource: str | None = None) -> None:
        """Start picoammeter polling worker. Non-blocking.

        Parameters:
            resource: VISA resource string (e.g. "GPIB0::2::INSTR") or None
                      for auto-discovery / default.
        """
        if self._picoammeter_worker is not None:
            log.info("picoammeter_link: worker already running")
            return

        self._picoammeter_resource = resource or ""
        self._cup_acquiring = False
        worker = PicoammeterWorker(resource_name=resource)
        worker.reading_ready.connect(self._on_picoammeter_reading)
        worker.error.connect(self._on_picoammeter_error)
        worker.connected_changed.connect(self._on_picoammeter_connected_changed)
        worker.start()

        self._picoammeter_worker = worker
        log.info("picoammeter_link: PicoammeterWorker started (resource=%s)", resource)

    def disconnect_picoammeter(self) -> None:
        """Stop worker and disconnect picoammeter. Non-blocking, idempotent."""
        worker = self._picoammeter_worker
        if worker is not None:
            self._picoammeter_worker = None
            self._picoammeter_resource = ""

            try:
                worker.reading_ready.disconnect(self._on_picoammeter_reading)
                worker.error.disconnect(self._on_picoammeter_error)
                worker.connected_changed.disconnect(self._on_picoammeter_connected_changed)
            except Exception:
                pass

            worker.finished.connect(worker.deleteLater)
            worker.stop()

        self._cup_acquiring = False
        self._last_cup_state = CupState(connected=False)
        self.cup_changed.emit(self._last_cup_state)
        self.cup_disconnected_evt.emit()
        log.info("picoammeter_link: disconnect requested")

    @property
    def picoammeter_connected(self) -> bool:
        """Return True if the picoammeter is connected and active."""
        return self._last_cup_state.connected

    @property
    def last_cup_state(self) -> CupState:
        """Return the most recent CupState snapshot."""
        return self._last_cup_state

    @property
    def cup_acquiring(self) -> bool:
        """Return True if the picoammeter worker is currently polling at acquiring rate."""
        return self._cup_acquiring

    def set_cup_acquiring(self, acquiring: bool) -> None:
        """Set polling rate to acquiring (10 Hz) or idle (2 Hz)."""
        self._cup_acquiring = acquiring
        if self._picoammeter_worker is not None:
            self._picoammeter_worker.set_acquiring(acquiring)

    # ── Snapshot Ingestion ────────────────────────────────────────────────────

    def ingest_cup_raw(
        self,
        raw_response: str,
        protocol_mode: int | None = 1,
        t_host: float | None = None,
    ) -> CupState:
        """Parse raw SCPI string into a CupState snapshot and publish it.

        This is the primary ingestion seam used by both production worker and
        test feed payloads.
        """
        host_time = time.time() if t_host is None else t_host
        try:
            reading = parse_reading(raw_response, protocol_mode=protocol_mode)
            state = CupState(
                connected=True,
                current=reading.current,
                timestamp=reading.timestamp,
                status_word=reading.status_word,
                over_range=reading.over_range,
                unavailable=reading.unavailable,
                valid=reading.valid,
                t_host=host_time,
                raw=reading.raw,
            )
        except Exception as exc:
            log.warning(
                "picoammeter_link: failed to parse raw SCPI response %r: %s",
                raw_response,
                exc,
            )
            state = CupState(
                connected=True,
                current=None,
                timestamp=float("nan"),
                status_word=0,
                over_range=False,
                unavailable=False,
                valid=False,
                t_host=host_time,
                raw=raw_response,
            )

        self._last_cup_state = state
        self.cup_changed.emit(state)
        return state

    def ingest_cup_reading(
        self,
        reading: Keithley6482Reading,
        t_host: float | None = None,
    ) -> CupState:
        """Convert a parsed Keithley6482Reading into a CupState snapshot and publish it."""
        host_time = time.time() if t_host is None else t_host
        state = CupState(
            connected=True,
            current=reading.current,
            timestamp=reading.timestamp,
            status_word=reading.status_word,
            over_range=reading.over_range,
            unavailable=reading.unavailable,
            valid=reading.valid,
            t_host=host_time,
            raw=reading.raw,
        )
        self._last_cup_state = state
        self.cup_changed.emit(state)
        return state

    # ── Signal Handlers from Worker ───────────────────────────────────────────

    def _on_picoammeter_reading(self, reading: Keithley6482Reading) -> None:
        """Ingest reading from worker and publish snapshot."""
        self.ingest_cup_reading(reading)

    def _on_picoammeter_error(self, msg: str) -> None:
        """Log error and emit Beamline.cup_error."""
        log.error("picoammeter_link: %s", msg)
        self.cup_error.emit(msg)

    def _on_picoammeter_connected_changed(self, connected: bool, ident: str) -> None:
        """Handle connection state transitions from worker."""
        if connected:
            log.info("picoammeter_link: connected (%s)", ident)
            self.cup_connected.emit(ident)
        else:
            log.info("picoammeter_link: disconnected")
            self._last_cup_state = CupState(connected=False)
            self.cup_changed.emit(self._last_cup_state)
            self.cup_disconnected_evt.emit()

    # ── Shutdown ──────────────────────────────────────────────────────────────

    def _shutdown_picoammeter(self) -> None:
        """Best-effort synchronous teardown for app exit — must not raise.

        Blocks until worker thread exits (up to 5 s). Leaves instrument state intact.
        """
        try:
            worker = self._picoammeter_worker
            if worker is None:
                return
            self._picoammeter_worker = None
            try:
                worker.reading_ready.disconnect()
                worker.error.disconnect()
                worker.connected_changed.disconnect()
            except Exception:
                pass
            worker.stop()
            if not worker.wait(5000):
                log.warning("picoammeter_link: worker did not stop within 5 s on shutdown")
        except Exception:
            log.exception("picoammeter_link: error shutting down worker")
