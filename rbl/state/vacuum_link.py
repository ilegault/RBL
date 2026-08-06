"""
vacuum_link.py
Beamline's vacuum gauge half: lifecycle of both gauge controllers and
publishing of VacuumState snapshots.

Modelled directly on labjack_link.py — see that module's docstring for the
mixin rationale.  The signals (vacuum_changed, vacuum_error) are declared
on Beamline; the methods here emit self.<signal> and they resolve at runtime
because self is always a Beamline instance.  This file must not be
instantiated on its own.
"""
import logging

from rbl.hardware.vacuum_worker import VacuumWorker
from rbl.hardware.serial_transport import discover, load_saved_ports, save_ports

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
        self._vacuum_worker:  VacuumWorker  = None
        self._vacuum_ports:   dict          = {}   # last-used port mapping

    # ---- Connection lifecycle ---------------------------------------------

    def connect_vacuum(self, ports: dict = None):
        """Open ports and start polling.

        Parameters
        ----------
        ports : {"xgs600": "COM4", "vgc083": "COM7"} or None.
                None triggers auto-discovery (saved-port-first, then scan).

        Missing or unresponsive instruments are not errors — the worker
        starts with whatever is present and reports the rest as disconnected.
        """
        if self._vacuum_worker is not None:
            log.debug("vacuum_link: already connected, ignoring connect_vacuum()")
            return

        if ports is None:
            log.info("vacuum_link: no ports given — running auto-discovery")
            saved = load_saved_ports()
            ports = discover(saved=saved)
            if ports:
                save_ports(ports)
                log.info("vacuum_link: discovered %s", ports)
            else:
                log.info("vacuum_link: no instruments discovered")

        self._vacuum_ports = dict(ports)
        worker = VacuumWorker(ports)
        worker.readings_ready.connect(self._on_vacuum_readings)
        worker.error.connect(self._on_vacuum_error)
        worker.start()
        self._vacuum_worker = worker
        log.info("vacuum_link: worker started")

    def disconnect_vacuum(self):
        """Stop polling and close all gauge ports. Idempotent."""
        if self._vacuum_worker is None:
            return
        log.info("vacuum_link: stopping worker")
        self._vacuum_worker.readings_ready.disconnect(self._on_vacuum_readings)
        self._vacuum_worker.error.disconnect(self._on_vacuum_error)
        self._vacuum_worker.stop()
        if not self._vacuum_worker.wait(5000):
            log.warning("vacuum_link: worker did not stop within 5 s")
        self._vacuum_worker = None
        log.info("vacuum_link: disconnected")

    @property
    def vacuum_connected(self) -> dict:
        """{"xgs600": bool, "vgc083": bool} — last known connection state."""
        # These are updated from the most recent VacuumState snapshot.
        return dict(getattr(self, "_vacuum_conn_state",
                            {"xgs600": False, "vgc083": False}))

    # ---- Signal handlers --------------------------------------------------

    def _on_vacuum_readings(self, state):
        """Re-emit the worker's VacuumState as Beamline.vacuum_changed."""
        # Cache connection state for the vacuum_connected property.
        self._vacuum_conn_state = {
            "xgs600": state.xgs_connected,
            "vgc083": state.vgc_connected,
        }
        self.vacuum_changed.emit(state)

    def _on_vacuum_error(self, msg: str):
        """Re-emit a worker error as Beamline.vacuum_error."""
        log.error("vacuum_link: %s", msg)
        self.vacuum_error.emit(msg)

    # ---- Emergency shutdown (called from Beamline.shutdown) ---------------

    def _shutdown_vacuum(self):
        """Best-effort teardown — must not raise."""
        try:
            self.disconnect_vacuum()
        except Exception:
            log.exception("vacuum_link: error during shutdown")
