"""
scope_link.py
Beamline's oscilloscope half: lifecycle of the TDS 2012 worker and
publishing of ScopeState snapshots.

Modelled directly on vacuum_link.py — see that module's docstring for the
mixin rationale.  The signals (scope_changed, scope_error) are declared
on Beamline; the methods here emit self.<signal> and they resolve at
runtime because self is always a Beamline instance.
"""
import logging

from rbl.hardware.scope_worker import ScopeWorker
from rbl.hardware.serial_transport import discover, load_saved_ports, save_ports

log = logging.getLogger(__name__)


class ScopeLinkMixin:
    """TDS 2012 ownership, polling, and snapshot publishing.

    Mixed into Beamline; see the module docstring.  Requires the host class
    to declare signals: scope_changed, scope_error.
    """

    def _init_scope(self):
        """Called from Beamline.__init__ — not a cooperative __init__.

        Sets up instance state only; does NOT open any serial port.
        Connection is always explicit (connect_scope from the GUI).
        """
        self._scope_worker:   ScopeWorker = None
        self._scope_port:     str         = ""
        # Analysis settings the GUI has chosen.  Held here as well as on the
        # worker so a reconnect does not silently revert to defaults - the
        # worker is destroyed on disconnect, these are not.
        self._scope_analysis: dict        = {}
        self._scope_average:  object      = "unset"   # None is a real value
        self._scope_channel:  str         = ""
        self._scope_continuous: bool      = False
        self._scope_points:   object      = None
        self._scope_points_anchor: object = None
        self._scope_poll_interval: object = None

    # ---- Connection lifecycle ---------------------------------------------

    def connect_scope(self, port: str = None):
        """Open the scope port and start acquisition polling.

        Parameters
        ----------
        port : COM port string, e.g. "COM5".  None triggers auto-discovery
               (saved-port-first, then scan).
        """
        if self._scope_worker is not None:
            log.debug("scope_link: already connected, ignoring connect_scope()")
            return

        if port is None:
            log.info("scope_link: no port given — running auto-discovery")
            saved = load_saved_ports()
            found = discover(saved=saved)
            port  = found.get("tds2012")
            if port:
                save_ports({"tds2012": port})
                log.info("scope_link: discovered tds2012 on %s", port)
            else:
                log.info("scope_link: tds2012 not discovered — cannot connect")
                return

        self._scope_port = port
        worker = ScopeWorker(port)
        if self._scope_channel:
            worker.set_channel(self._scope_channel)
        if self._scope_analysis:
            worker.set_analysis(**self._scope_analysis)
        if self._scope_average != "unset":
            worker.set_average(self._scope_average)
        if self._scope_points is not None or self._scope_points_anchor is not None:
            worker.set_points(self._scope_points, self._scope_points_anchor)
        if self._scope_poll_interval is not None:
            worker.set_poll_interval(self._scope_poll_interval)
        worker.set_continuous(self._scope_continuous)
        worker.waveform_ready.connect(self._on_scope_waveform)
        worker.error.connect(self._on_scope_error)
        worker.start()
        self._scope_worker = worker
        log.info("scope_link: worker started on %s", port)

    def disconnect_scope(self):
        """Stop acquisition and close the scope port. Idempotent."""
        if self._scope_worker is None:
            return
        log.info("scope_link: stopping worker")
        self._scope_worker.waveform_ready.disconnect(self._on_scope_waveform)
        self._scope_worker.error.disconnect(self._on_scope_error)
        self._scope_worker.stop()
        if not self._scope_worker.wait(10_000):
            log.warning("scope_link: worker did not stop within 10 s")
        self._scope_worker = None
        log.info("scope_link: disconnected")

    # ---- Live settings ----------------------------------------------------

    def set_scope_analysis(self, **kwargs):
        """Change host-side profile analysis: smooth, peaks, threshold,
        polarity.  Remembered across reconnects."""
        self._scope_analysis.update({k: v for k, v in kwargs.items()
                                     if v is not None})
        if self._scope_worker is not None:
            self._scope_worker.set_analysis(**kwargs)

    def set_scope_average(self, sweeps):
        """Change the SCOPE's own acquisition averaging (4/16/64/128, or 1)."""
        self._scope_average = sweeps
        if self._scope_worker is not None:
            self._scope_worker.set_average(sweeps)

    def set_scope_points(self, points: int = None, anchor: str = None):
        """How much of the record to transfer, and where the slice sits."""
        if points is not None:
            self._scope_points = int(points)
        if anchor is not None:
            self._scope_points_anchor = anchor
        if self._scope_worker is not None:
            self._scope_worker.set_points(points, anchor)

    def set_scope_poll_interval(self, seconds: float):
        """Seconds between acquisitions."""
        self._scope_poll_interval = float(seconds)
        if self._scope_worker is not None:
            self._scope_worker.set_poll_interval(seconds)

    def set_scope_continuous(self, continuous: bool):
        """Free-run the scope, or leave it idle between requested shots."""
        self._scope_continuous = bool(continuous)
        if self._scope_worker is not None:
            self._scope_worker.set_continuous(continuous)

    def request_scope_shot(self):
        """Take one waveform now.  Ignored when nothing is connected."""
        if self._scope_worker is None:
            log.debug("scope_link: shot requested with no worker running")
            return False
        self._scope_worker.request_shot()
        return True

    @property
    def scope_continuous(self) -> bool:
        return bool(getattr(self, "_scope_continuous", False))

    def set_scope_channel(self, channel: str):
        """Switch the acquired channel ("CH1"/"CH2")."""
        self._scope_channel = channel
        if self._scope_worker is not None:
            self._scope_worker.set_channel(channel)

    @property
    def scope_connected(self) -> bool:
        """True if a worker is running (not necessarily instrument-level OK)."""
        return self._scope_worker is not None

    # ---- Signal handlers --------------------------------------------------

    def _on_scope_waveform(self, state):
        """Re-emit the worker's ScopeState as Beamline.scope_changed."""
        self.scope_changed.emit(state)

    def _on_scope_error(self, msg: str):
        """Re-emit a worker error as Beamline.scope_error."""
        log.error("scope_link: %s", msg)
        self.scope_error.emit(msg)

    # ---- Emergency shutdown -----------------------------------------------

    def _shutdown_scope(self):
        """Best-effort teardown — must not raise."""
        try:
            self.disconnect_scope()
        except Exception:
            log.exception("scope_link: error during shutdown")
