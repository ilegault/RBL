"""
vacuum_link.py
Beamline's vacuum gauge half: lifecycle of both gauge controllers and
publishing of VacuumState snapshots.

Modelled directly on labjack_link.py — see that module's docstring for the
mixin rationale.  The signals (vacuum_changed, vacuum_error) are declared
on Beamline; the methods here emit self.<signal> and they resolve at runtime
because self is always a Beamline instance.  This file must not be
instantiated on its own.

DESIGN NOTE — TWO INDEPENDENT WORKERS
--------------------------------------
Each gauge controller (XGS-600, VGC083) runs in its own VacuumWorker
QThread.  This lets them be connected / disconnected independently and
avoids the need to stop-restart a shared worker when the second instrument
is added.  The link layer merges the two half-snapshots into a single
VacuumState before emitting vacuum_changed.
"""
import logging
import time

from rbl.hardware.vacuum_worker import VacuumWorker
from rbl.hardware.serial_transport import discover, load_saved_ports, save_ports
from rbl.state.snapshots import VacuumState

log = logging.getLogger(__name__)


class VacuumLinkMixin:
    """XGS-600 + VGC083 ownership, polling, and snapshot publishing.

    Mixed into Beamline; see the module docstring.  Requires the host class
    to declare signals: vacuum_changed, vacuum_error.
    """

    def _init_vacuum(self):
        """Called from Beamline.__init__ — not a cooperative __init__.

        Sets up instance state only; does NOT open any serial port.
        Connection is always explicit (connect_vacuum / auto-discover on
        first connect request from the GUI).
        """
        self._xgs_worker: VacuumWorker | None = None
        self._vgc_worker: VacuumWorker | None = None
        self._xgs_port: str = ""
        self._vgc_port: str = ""
        # Cached half-snapshots from each worker, merged on every emission.
        self._last_xgs_state: VacuumState | None = None
        self._last_vgc_state: VacuumState | None = None

    # ---- Connection lifecycle ---------------------------------------------

    @staticmethod
    def discover_vacuum_ports() -> dict:
        """Run serial-port auto-discovery and return the result.

        Returns a dict like ``{"xgs600": "COM4", "vgc083": "COM7"}``.
        Missing instruments are simply absent from the dict.  The result
        is also persisted so the next launch tries saved ports first.
        """
        saved = load_saved_ports()
        ports = discover(saved=saved)
        if ports:
            save_ports(ports)
        return ports

    def connect_vacuum(self, ports: dict = None):
        """Open ports and start polling.

        Parameters
        ----------
        ports : {"xgs600": "COM4", "vgc083": "COM7"} or None.
                None triggers auto-discovery (saved-port-first, then scan).

        Each instrument is started independently.  Calling this a second
        time with the other instrument's port simply adds it — the already-
        running worker is left untouched.
        """
        if ports is None:
            log.info("vacuum_link: no ports given — running auto-discovery")
            saved = load_saved_ports()
            ports = discover(saved=saved)
            if ports:
                save_ports(ports)
                log.info("vacuum_link: discovered %s", ports)
            else:
                log.info("vacuum_link: no instruments discovered")
                ports = {}

        xgs_port = ports.get("xgs600")
        vgc_port = ports.get("vgc083")

        if xgs_port and self._xgs_worker is None:
            self._xgs_port = xgs_port
            worker = VacuumWorker({"xgs600": xgs_port})
            worker.readings_ready.connect(self._on_xgs_readings)
            worker.error.connect(self._on_vacuum_error)
            worker.start()
            self._xgs_worker = worker
            log.info("vacuum_link: XGS-600 worker started on %s", xgs_port)

        if vgc_port and self._vgc_worker is None:
            self._vgc_port = vgc_port
            worker = VacuumWorker({"vgc083": vgc_port})
            worker.readings_ready.connect(self._on_vgc_readings)
            worker.error.connect(self._on_vacuum_error)
            worker.start()
            self._vgc_worker = worker
            log.info("vacuum_link: VGC083 worker started on %s", vgc_port)

    def disconnect_vacuum(self):
        """Stop polling and close all gauge ports. Non-blocking."""
        self.disconnect_xgs600()
        self.disconnect_vgc083()
        log.info("vacuum_link: disconnect all requested")

    def disconnect_xgs600(self):
        """Stop the XGS-600 worker only. Non-blocking, idempotent."""
        worker = self._xgs_worker
        if worker is None:
            return
        self._xgs_worker = None
        self._xgs_port = ""
        self._last_xgs_state = None
        worker.readings_ready.disconnect(self._on_xgs_readings)
        worker.error.disconnect(self._on_vacuum_error)
        worker.finished.connect(worker.deleteLater)
        worker.stop()
        log.info("vacuum_link: XGS-600 disconnect requested")

    def disconnect_vgc083(self):
        """Stop the VGC083 worker only. Non-blocking, idempotent."""
        worker = self._vgc_worker
        if worker is None:
            return
        self._vgc_worker = None
        self._vgc_port = ""
        self._last_vgc_state = None
        worker.readings_ready.disconnect(self._on_vgc_readings)
        worker.error.disconnect(self._on_vacuum_error)
        worker.finished.connect(worker.deleteLater)
        worker.stop()
        log.info("vacuum_link: VGC083 disconnect requested")

    @property
    def vacuum_connected(self) -> dict:
        """{"xgs600": bool, "vgc083": bool} — last known connection state."""
        return {
            "xgs600": self._last_xgs_state.xgs_connected
                      if self._last_xgs_state else False,
            "vgc083": self._last_vgc_state.vgc_connected
                      if self._last_vgc_state else False,
        }

    # ---- Signal handlers --------------------------------------------------

    def _on_xgs_readings(self, state):
        """Cache XGS half and emit merged snapshot."""
        self._last_xgs_state = state
        self._emit_merged_state()

    def _on_vgc_readings(self, state):
        """Cache VGC half and emit merged snapshot."""
        self._last_vgc_state = state
        self._emit_merged_state()

    def _emit_merged_state(self):
        """Combine cached XGS and VGC halves into one VacuumState."""
        xgs = self._last_xgs_state
        vgc = self._last_vgc_state
        merged = VacuumState(
            timestamp=time.time(),
            xgs_readings=xgs.xgs_readings if xgs else [],
            vgc_readings=vgc.vgc_readings if vgc else [],
            xgs_connected=xgs.xgs_connected if xgs else False,
            vgc_connected=vgc.vgc_connected if vgc else False,
            units_xgs=xgs.units_xgs if xgs else "",
            units_vgc=vgc.units_vgc if vgc else "Torr",
        )
        self.vacuum_changed.emit(merged)

    def _on_vacuum_error(self, msg: str):
        """Re-emit a worker error as Beamline.vacuum_error."""
        log.error("vacuum_link: %s", msg)
        self.vacuum_error.emit(msg)

    # ---- Emergency shutdown (called from Beamline.shutdown) ---------------

    def _shutdown_vacuum(self):
        """Best-effort synchronous teardown for app exit — must not raise.

        Blocks until both workers exit (or 5 s each) because the process is
        about to terminate and we need serial ports cleanly closed.
        """
        for label, worker_attr in [("XGS", "_xgs_worker"),
                                   ("VGC", "_vgc_worker")]:
            try:
                worker = getattr(self, worker_attr)
                if worker is None:
                    continue
                setattr(self, worker_attr, None)
                try:
                    worker.readings_ready.disconnect()
                    worker.error.disconnect()
                except Exception:
                    pass
                worker.stop()
                if not worker.wait(5000):
                    log.warning("vacuum_link: %s worker did not stop "
                                "within 5 s on shutdown", label)
            except Exception:
                log.exception("vacuum_link: error shutting down %s worker",
                              label)
