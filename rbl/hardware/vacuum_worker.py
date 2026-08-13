"""
vacuum_worker.py
QThread that polls both gauge controllers at 1 Hz and emits typed snapshots.

THREADING CONTRACT
------------------
This worker owns both serial ports for its lifetime.  No other object may
open those ports while the worker is running.  The Beamline (via
VacuumLinkMixin) owns the worker's lifecycle: it calls stop() and waits for
the thread before destroying it.

MISSING HARDWARE IS NORMAL
---------------------------
If a port cannot be found or fails to open, the worker continues running and
emits VacuumState snapshots with the corresponding `*_connected` flag False
and an empty readings list.  This is not an error.  Hardware going missing
MID-RUN is emitted on the `error` signal and triggers the backoff-retry
ladder.

BACKOFF-RETRY
-------------
On a serial exception from a connected instrument:
1. Emit `error(str)`.
2. Close and discard the driver instance.
3. Wait `RECONNECT_BACKOFF_S[n]` seconds (n starts at 0, increments on each
   failed attempt, then holds at the last value).
4. On the next poll tick, attempt to reopen that port and reconnect.
5. Log every attempt.
"""
import logging
import time

from PySide6.QtCore import QThread, Signal

from rbl.config.vacuum_config import (
    POLL_INTERVAL_S, VGC_ACTIVE_CHANNELS, RECONNECT_BACKOFF_S,
    XGS_BAUD_DEFAULT, VGC_BAUD_DEFAULT, VGC_BAUD_CANDIDATES,
    XGS_BAUD_CANDIDATES,
)
from rbl.hardware.serial_transport import SerialTransport
from rbl.hardware.xgs600_driver import (
    Xgs600, XgsTimeoutError, XgsProtocolError, XgsFieldError,
)
from rbl.hardware.vgc083_driver import Vgc083, VgcTimeoutError, VgcProtocolError
from rbl.state.snapshots import VacuumState

log = logging.getLogger(__name__)


class VacuumWorker(QThread):
    """Polls XGS-600 and VGC083 at 1 Hz; emits VacuumState snapshots.

    Parameters
    ----------
    ports : dict with optional keys "xgs600" and "vgc083" mapping to COM
            port strings.  Missing or None values mean that instrument is
            not configured and will not be polled.
    xgs_baud / vgc_baud : baud rates (fall back to config defaults).
    vgc_channels : list of VGC083 channel names to read each poll.
    """

    readings_ready = Signal(object)   # VacuumState
    error          = Signal(str)

    def __init__(self, ports: dict = None, *,
                 xgs_baud: int = XGS_BAUD_DEFAULT,
                 vgc_baud: int = VGC_BAUD_DEFAULT,
                 vgc_channels: list = None,
                 parent=None):
        super().__init__(parent)
        self._ports        = dict(ports or {})
        self._xgs_baud     = xgs_baud
        self._vgc_baud     = vgc_baud
        self._vgc_channels = list(vgc_channels or VGC_ACTIVE_CHANNELS)
        self._running      = False

    def stop(self):
        """Signal the poll loop to exit.  Call wait(ms) afterwards."""
        self._running = False

    # -----------------------------------------------------------------------
    # QThread entry point — runs on the worker thread
    # -----------------------------------------------------------------------

    def run(self):
        self._running = True
        log.info("vacuum_worker: starting (ports=%s)", self._ports)

        # ---- Initial connection attempt -----------------------------------
        xgs_transport, xgs = self._connect_xgs()
        vgc_transport, vgc = self._connect_vgc()
        xgs_units = ""
        if xgs is not None:
            try:
                xgs_units = xgs.read_units()
            except Exception:
                xgs_units = "?"

        # Backoff state per instrument.
        xgs_backoff_idx = 0
        vgc_backoff_idx = 0
        xgs_retry_at    = 0.0   # monotonic time; 0 means "try now"
        vgc_retry_at    = 0.0

        while self._running:
            tick_start = time.monotonic()

            # ---- Reconnect attempts (if instrument was lost) ---------------
            now = time.monotonic()
            if xgs is None and self._ports.get("xgs600") and now >= xgs_retry_at:
                log.info("vacuum_worker: attempting XGS-600 reconnect on %s",
                         self._ports["xgs600"])
                xgs_transport, xgs = self._connect_xgs()
                if xgs is not None:
                    xgs_backoff_idx = 0
                    try:
                        xgs_units = xgs.read_units()
                    except Exception:
                        xgs_units = "?"
                else:
                    delay = _backoff_delay(xgs_backoff_idx)
                    xgs_backoff_idx += 1
                    xgs_retry_at = time.monotonic() + delay
                    log.info("vacuum_worker: XGS-600 reconnect failed; "
                             "retry in %.1f s", delay)

            if vgc is None and self._ports.get("vgc083") and now >= vgc_retry_at:
                log.info("vacuum_worker: attempting VGC083 reconnect on %s",
                         self._ports["vgc083"])
                vgc_transport, vgc = self._connect_vgc()
                if vgc is not None:
                    vgc_backoff_idx = 0
                else:
                    delay = _backoff_delay(vgc_backoff_idx)
                    vgc_backoff_idx += 1
                    vgc_retry_at = time.monotonic() + delay
                    log.info("vacuum_worker: VGC083 reconnect failed; "
                             "retry in %.1f s", delay)

            # ---- XGS-600 poll ------------------------------------------------
            xgs_readings = []
            xgs_ok = False
            if xgs is not None:
                try:
                    xgs_readings = xgs.read_all()
                    xgs_ok = True
                    xgs_backoff_idx = 0
                # XgsFieldError subclasses ValueError, NOT OSError — without
                # it listed explicitly a board-count mismatch escapes run()
                # and kills the whole worker thread instead of triggering the
                # reconnect ladder.
                except (XgsTimeoutError, XgsProtocolError, XgsFieldError,
                        OSError) as exc:
                    msg = f"XGS-600 poll error: {exc}"
                    log.warning("vacuum_worker: %s", msg)
                    self.error.emit(msg)
                    _close_transport(xgs_transport)
                    xgs_transport = None
                    xgs = None
                    delay = _backoff_delay(xgs_backoff_idx)
                    xgs_backoff_idx += 1
                    xgs_retry_at = time.monotonic() + delay
                    log.info("vacuum_worker: XGS-600 retry in %.1f s", delay)

            # ---- VGC083 poll -------------------------------------------------
            vgc_readings = []
            vgc_ok = False
            if vgc is not None:
                try:
                    vgc_readings = vgc.read_all(self._vgc_channels)
                    vgc_ok = True
                    vgc_backoff_idx = 0
                except (VgcTimeoutError, VgcProtocolError, OSError) as exc:
                    msg = f"VGC083 poll error: {exc}"
                    log.warning("vacuum_worker: %s", msg)
                    self.error.emit(msg)
                    _close_transport(vgc_transport)
                    vgc_transport = None
                    vgc = None
                    delay = _backoff_delay(vgc_backoff_idx)
                    vgc_backoff_idx += 1
                    vgc_retry_at = time.monotonic() + delay
                    log.info("vacuum_worker: VGC083 retry in %.1f s", delay)

            # ---- Emit snapshot -----------------------------------------------
            state = VacuumState(
                timestamp     = time.time(),
                xgs_readings  = xgs_readings,
                vgc_readings  = vgc_readings,
                xgs_connected = xgs_ok,
                vgc_connected = vgc_ok,
                units_xgs     = xgs_units,
                units_vgc     = "Torr",
            )
            self.readings_ready.emit(state)

            # ---- Wait until next poll tick -----------------------------------
            elapsed  = time.monotonic() - tick_start
            sleep_s  = max(0.0, POLL_INTERVAL_S - elapsed)
            deadline = time.monotonic() + sleep_s
            while self._running and time.monotonic() < deadline:
                time.sleep(0.05)   # 50 ms granularity for responsive stop()

        # ---- Teardown -------------------------------------------------------
        log.info("vacuum_worker: stopping")
        _close_transport(xgs_transport)
        _close_transport(vgc_transport)
        log.info("vacuum_worker: stopped")

    # -----------------------------------------------------------------------
    # Connection helpers
    # -----------------------------------------------------------------------

    def _connect_xgs(self):
        """Try to open the XGS-600 port. Returns (transport, driver) or (None, None).

        CRITICAL: if open() succeeds but the identity handshake fails, the
        transport MUST be closed before returning.  Otherwise the port stays
        registered in serial_transport._OPEN_PORTS and every subsequent
        reconnect attempt fails with "already open by another SerialTransport
        instance" — a self-inflicted permanent failure.
        """
        port = self._ports.get("xgs600")
        if not port:
            return None, None

        bauds = [self._xgs_baud] + [b for b in XGS_BAUD_CANDIDATES
                                    if b != self._xgs_baud]

        for baud in bauds:
            t = None
            try:
                t = SerialTransport(port, baud, timeout=2.0)
                t.open()
                t.reset_buffers()
                drv = Xgs600(t)
                drv.discover_channels()
                log.info("vacuum_worker: XGS-600 connected on %s @ %d baud, "
                         "%d channel(s)", port, baud, len(drv._channels))
                if baud != self._xgs_baud:
                    log.warning(
                        "vacuum_worker: XGS-600 answered at %d baud, not the "
                        "configured %d.  Update XGS_BAUD_DEFAULT.",
                        baud, self._xgs_baud)
                    self._xgs_baud = baud
                return t, drv
            except Exception as exc:
                log.warning("vacuum_worker: XGS-600 open failed on %s @ %d "
                            "baud: %s", port, baud, exc)
                _close_transport(t)      # <- releases the port registry entry

        return None, None

    def _connect_vgc(self):
        """Try to open the VGC083 port. Returns (transport, driver) or (None, None).

        Tries the configured baud first, then the alternate rate.  A VGC083
        set to 9600 on its front panel answers nothing at 19200, which is
        indistinguishable from "not plugged in" without this sweep.

        Same close-on-failure contract as _connect_xgs — see its docstring.
        """
        port = self._ports.get("vgc083")
        if not port:
            return None, None

        # Configured baud first, then the other supported rate.
        bauds = [self._vgc_baud] + [b for b in VGC_BAUD_CANDIDATES
                                    if b != self._vgc_baud]

        for baud in bauds:
            t = None
            try:
                t = SerialTransport(port, baud, rtscts=False, timeout=2.0)
                t.open()
                t.reset_buffers()
                drv = Vgc083(t)
                ident = drv.identify()
                log.info("vacuum_worker: VGC083 connected on %s @ %d baud, fw=%s",
                         port, baud, ident)
                if baud != self._vgc_baud:
                    log.warning(
                        "vacuum_worker: VGC083 answered at %d baud, not the "
                        "configured %d.  Update VGC_BAUD_DEFAULT or the "
                        "instrument's front-panel setting.", baud, self._vgc_baud)
                    self._vgc_baud = baud
                return t, drv
            except Exception as exc:
                log.warning("vacuum_worker: VGC083 open failed on %s @ %d baud: %s",
                            port, baud, exc)
                _close_transport(t)      # <- releases the port registry entry

        return None, None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _backoff_delay(idx: int) -> float:
    """Return the backoff delay at position *idx* in the ladder."""
    return RECONNECT_BACKOFF_S[min(idx, len(RECONNECT_BACKOFF_S) - 1)]


def _close_transport(t):
    """Close a SerialTransport; ignore errors (already disconnected is fine)."""
    if t is not None:
        try:
            t.close()
        except Exception:
            pass
