"""
scope_worker.py
QThread worker that polls the TDS 2012 oscilloscope at SCOPE_POLL_INTERVAL_S.

Each poll:
1. Send a single-sequence acquisition command.
2. Read the waveform from the configured channel.
3. Convert raw bytes to voltages (samples_to_volts).
4. Extract the FWHM (compute_fwhm_seconds).
5. Downsample the voltage trace for the GUI plot.
6. Emit waveform_ready(ScopeState).

On any serial or protocol error:
- Close the transport.
- Emit error(str).
- Wait with exponential backoff then attempt reconnect.
- Emit waveform_ready with connected=False while disconnected so the GUI
  can show "disconnected" rather than showing stale data indefinitely.
"""
import logging
import math
import time

from PySide6.QtCore import QThread, Signal

from rbl.config.scope_config import (
    SCOPE_CHANNEL_DEFAULT,
    SCOPE_POLL_INTERVAL_S,
    SCOPE_RECONNECT_BACKOFF_S,
    SCOPE_WAVEFORM_DOWNSAMPLE,
    TDS_BAUD_DEFAULT,
    TDS_TIMEOUT_S,
)
from rbl.hardware.serial_transport import SerialTransport, SerialTimeout
from rbl.hardware.tds2012_driver import Tds2012, TdsProtocolError, TdsTimeoutError
from rbl.hardware.profile_fwhm import compute_fwhm_seconds, FwhmError
from rbl.state.snapshots import ScopeState

log = logging.getLogger(__name__)

_SLEEP_CHUNK_S = 0.05   # interrupt-checking granularity inside sleeps


class ScopeWorker(QThread):
    """Continuous oscilloscope acquisition worker.

    Parameters
    ----------
    port    : COM port string, e.g. "COM5"
    baud    : baud rate (default TDS_BAUD_DEFAULT)
    channel : oscilloscope channel to acquire (default SCOPE_CHANNEL_DEFAULT)
    """

    waveform_ready = Signal(object)   # ScopeState
    error          = Signal(str)

    def __init__(self, port: str, *,
                 baud: int    = TDS_BAUD_DEFAULT,
                 channel: str = SCOPE_CHANNEL_DEFAULT,
                 parent=None):
        super().__init__(parent)
        self._port    = port
        self._baud    = baud
        self._channel = channel
        self._running = False

    def stop(self):
        """Signal the run loop to exit on its next iteration."""
        self._running = False

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _backoff_delay(self, attempt: int) -> float:
        idx = min(attempt, len(SCOPE_RECONNECT_BACKOFF_S) - 1)
        return SCOPE_RECONNECT_BACKOFF_S[idx]

    def _sleep_interruptible(self, seconds: float) -> None:
        """Sleep for *seconds* in small chunks so stop() is responsive."""
        deadline = time.monotonic() + seconds
        while self._running and time.monotonic() < deadline:
            time.sleep(min(_SLEEP_CHUNK_S, deadline - time.monotonic()))

    def _connect(self):
        """Open transport + driver.  Returns (transport, driver) or (None, None)."""
        try:
            t = SerialTransport(
                self._port, self._baud,
                rtscts=True,
                timeout=TDS_TIMEOUT_S,
            )
            t.open()
            drv = Tds2012(t)
            ident = drv.identify()
            log.info("scope_worker: connected to %s — %s", self._port, ident)
            return t, drv
        except Exception as exc:
            log.warning("scope_worker: connect failed: %s", exc)
            return None, None

    def _disconnect(self, transport) -> None:
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass

    def _downsample(self, volts: list[float]) -> list[float]:
        """Return at most SCOPE_WAVEFORM_DOWNSAMPLE evenly-spaced points."""
        n = len(volts)
        if n <= SCOPE_WAVEFORM_DOWNSAMPLE:
            return volts
        step = n / SCOPE_WAVEFORM_DOWNSAMPLE
        return [volts[int(i * step)] for i in range(SCOPE_WAVEFORM_DOWNSAMPLE)]

    # -----------------------------------------------------------------------
    # Main thread body
    # -----------------------------------------------------------------------

    def run(self):
        self._running = True
        transport     = None
        driver        = None
        backoff_idx   = 0

        while self._running:
            # ---- Connect if needed ----------------------------------------
            if driver is None:
                transport, driver = self._connect()
                if driver is None:
                    delay = self._backoff_delay(backoff_idx)
                    log.info("scope_worker: retrying in %.1f s", delay)
                    self._sleep_interruptible(delay)
                    backoff_idx += 1
                    # Emit a disconnected state so the GUI doesn't show stale data
                    self.waveform_ready.emit(ScopeState(
                        timestamp=time.time(),
                        connected=False,
                        channel=self._channel,
                    ))
                    continue
                backoff_idx = 0

            # ---- Acquire waveform -----------------------------------------
            try:
                driver.reset_buffers()
                raw_bytes, preamble = driver.acquire_waveform(self._channel)

                from rbl.hardware.tds2012_driver import samples_to_volts
                volts = samples_to_volts(raw_bytes, preamble)

                xincr = float(preamble.get("XINCR", math.nan))
                xzero = float(preamble.get("XZERO", math.nan))

                # FWHM (best-effort — a bad beam still yields a snapshot)
                fwhm_samples = math.nan
                fwhm_seconds = math.nan
                fwhm_error   = ""
                try:
                    fwhm_seconds = compute_fwhm_seconds(volts, xincr)
                    if not math.isnan(xincr) and xincr > 0:
                        fwhm_samples = fwhm_seconds / xincr
                except FwhmError as exc:
                    fwhm_error = str(exc)
                    log.debug("scope_worker: FWHM extraction failed: %s", exc)

                state = ScopeState(
                    timestamp         = time.time(),
                    connected         = True,
                    channel           = self._channel,
                    fwhm_samples      = fwhm_samples,
                    fwhm_seconds      = fwhm_seconds,
                    xincr             = xincr,
                    xzero             = xzero,
                    volts_downsampled = self._downsample(volts),
                    preamble          = dict(preamble),
                    error             = fwhm_error,
                )
                self.waveform_ready.emit(state)

            except (TdsTimeoutError, TdsProtocolError, OSError) as exc:
                msg = f"scope_worker: acquisition error on {self._port}: {exc}"
                log.error(msg)
                self.error.emit(str(exc))
                self._disconnect(transport)
                transport = None
                driver    = None
                # Emit disconnected state immediately
                self.waveform_ready.emit(ScopeState(
                    timestamp=time.time(),
                    connected=False,
                    channel=self._channel,
                    error=str(exc),
                ))
                continue

            # ---- Rate-limit --------------------------------------------------
            self._sleep_interruptible(SCOPE_POLL_INTERVAL_S)

        # ---- Cleanup on stop -----------------------------------------------
        self._disconnect(transport)
        log.info("scope_worker: stopped")
