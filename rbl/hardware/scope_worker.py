"""
scope_worker.py
QThread worker that polls the TDS 2012 oscilloscope at SCOPE_POLL_INTERVAL_S.

Each acquisition - one shot, or one pass of continuous mode:
1. Apply any pending settings change (channel, scope-side averaging).
2. Read the waveform from the configured channel.
3. Convert raw bytes to voltages (samples_to_volts).
4. Measure EVERY beam peak (analyse_profile).  One channel normally
   carries two peaks, and they are the X profile and the Y profile - two
   directions, not one beam measured twice.  When the beam is rastered the
   width lives in the ENVELOPE of the raster teeth, which is what
   envelope_ms switches the analysis onto.
5. Fit a sum of Gaussians, one per peak, and pick whichever number is
   trustworthy (best_fwhm).
6. Ask the scope for its own PWIDTH measurement as an independent check.
7. Downsample the trace for the GUI plot.
8. Emit waveform_ready(ScopeState).

ACQUISITION MODE
----------------
The worker IDLES by default.  Connecting opens the link and stops there; a
waveform is transferred when someone asks for one with request_shot(), or
continuously once set_continuous(True) is on.

That default is the point.  A 2500-point transfer at 19200 baud takes well
over a second, so a free-running worker keeps the link busy essentially all
the time, with the envelope, the fit and the redraw riding on top - and it
used to start the moment you connected, whether or not the trace meant
anything yet.  Idling costs nothing: the port stays open, a cheap identity
query every few seconds still notices a pulled cable, and the last
measurement stays on screen.

On any serial or protocol error:
- Close the transport.
- Emit error(str).
- Wait with exponential backoff then attempt reconnect.
- Emit waveform_ready with connected=False while disconnected so the GUI
  can show "disconnected" rather than showing stale data indefinitely.

WHY THE SETTINGS ARE MUTABLE AT RUNTIME
---------------------------------------
Smoothing window, expected peak count and scope-side averaging all have to
be tuned against a live beam - the right values depend on the signal, and
waiting for a restart to try one costs beam time.  They are guarded by a
mutex and read once per acquisition, so a change never lands halfway
through one.
"""
import logging
import math
import time

from PySide6.QtCore import QThread, QMutex, QMutexLocker, Signal

from rbl.config.scope_config import (
    SCOPE_AVERAGE_SWEEPS,
    SCOPE_CONTINUOUS_DEFAULT,
    SCOPE_AXIS_LABELS,
    SCOPE_CHANNEL_DEFAULT,
    SCOPE_ENVELOPE_MS,
    SCOPE_EXPECTED_PEAKS,
    SCOPE_FIT_MIN_R2,
    SCOPE_PEAK_MIN_SEP_FRAC,
    SCOPE_PEAK_THRESHOLD,
    SCOPE_POINTS,
    SCOPE_POINTS_ANCHOR,
    SCOPE_POLARITY,
    SCOPE_POLL_INTERVAL_S,
    SCOPE_RECORD_POINTS,
    SCOPE_RECONNECT_BACKOFF_S,
    SCOPE_SMOOTH_WINDOW,
    SCOPE_WAVEFORM_DOWNSAMPLE,
    TDS_BAUD_DEFAULT,
    TDS_TIMEOUT_S,
)
from rbl.hardware.serial_transport import SerialTransport, SerialTimeout
from rbl.hardware.tds2012_driver import (
    Tds2012, TdsProtocolError, TdsTimeoutError, samples_to_volts,
)
from rbl.hardware.profile_fwhm import (
    FwhmError, analyse_profile, best_fwhm, fit_gaussians,
)
from rbl.state.snapshots import ScopeState

log = logging.getLogger(__name__)

_SLEEP_CHUNK_S = 0.05   # interrupt-checking granularity inside sleeps
_IDLE_TICK_S   = 0.1    # how often an idle worker looks for a shot request
# Seconds between identity queries while idle.  This is a cable-pull
# detector for a link that is deliberately doing nothing, not a heartbeat
# anyone is waiting on: the next shot reports a dead link immediately
# anyway.  Fifteen seconds' notice costs nothing and cuts the idle serial
# traffic by two thirds.
_KEEPALIVE_S   = 15.0


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
        self._running = False

        self._mutex = QMutex()
        self._channel = channel
        self._smooth = SCOPE_SMOOTH_WINDOW
        self._peaks = SCOPE_EXPECTED_PEAKS
        self._threshold = SCOPE_PEAK_THRESHOLD
        self._polarity = SCOPE_POLARITY
        self._envelope_ms = SCOPE_ENVELOPE_MS
        self._axis_labels = tuple(SCOPE_AXIS_LABELS)
        self._points = SCOPE_POINTS
        self._points_anchor = SCOPE_POINTS_ANCHOR
        self._poll_interval = SCOPE_POLL_INTERVAL_S
        self._average = SCOPE_AVERAGE_SWEEPS
        self._average_dirty = True      # push averaging on first connect
        self._channel_dirty = True
        self._continuous = SCOPE_CONTINUOUS_DEFAULT
        self._shot_requested = False
        # The identity string the link answered with at connect.  The idle
        # keepalive compares against it so that a SILENT ping stays silent
        # and only a CHANGED answer - a different instrument on this port -
        # gets a log line.
        self._ident = ""

    def stop(self):
        """Signal the run loop to exit on its next iteration."""
        self._running = False

    # -----------------------------------------------------------------------
    # Live settings (called from the GUI thread)
    # -----------------------------------------------------------------------

    def set_analysis(self, *, smooth: int = None, peaks: int = None,
                     threshold: float = None, polarity: str = None,
                     envelope_ms: float = None, axis_labels=None):
        """Change host-side analysis parameters between acquisitions."""
        with QMutexLocker(self._mutex):
            if smooth is not None:
                self._smooth = max(1, int(smooth))
            if peaks is not None:
                self._peaks = max(1, int(peaks))
            if threshold is not None:
                self._threshold = float(threshold)
            if polarity is not None:
                self._polarity = polarity
            if envelope_ms is not None:
                self._envelope_ms = max(0.0, float(envelope_ms))
            if axis_labels is not None:
                self._axis_labels = tuple(axis_labels)

    def set_average(self, sweeps: int | None):
        """Change the SCOPE's acquisition averaging (4/16/64/128, or 1)."""
        with QMutexLocker(self._mutex):
            self._average = sweeps
            self._average_dirty = True

    def set_points(self, points: int = None, anchor: str = None):
        """Choose how much of the 2500-point record to transfer.

        This CROPS the record; it does not decimate it.  Fewer points means
        a shorter time window at the same sample interval - if a peak lives
        outside the slice it is not in the data at all.  The payoff is
        transfer time, which is the dominant cost of an acquisition at
        19200 baud.
        """
        with QMutexLocker(self._mutex):
            if points is not None:
                self._points = max(1, min(int(points), SCOPE_RECORD_POINTS))
            if anchor is not None:
                self._points_anchor = anchor

    def set_poll_interval(self, seconds: float):
        """Seconds between acquisitions.  Costs nothing but update rate."""
        with QMutexLocker(self._mutex):
            self._poll_interval = max(0.0, float(seconds))

    def record_slice(self) -> tuple:
        """(start, stop), 1-based inclusive, for the configured transfer."""
        with QMutexLocker(self._mutex):
            n, anchor = self._points, self._points_anchor
        return self._slice_for(n, anchor)

    @staticmethod
    def _slice_for(points: int, anchor: str) -> tuple:
        points = max(1, min(int(points), SCOPE_RECORD_POINTS))
        if points >= SCOPE_RECORD_POINTS:
            return 1, SCOPE_RECORD_POINTS
        if anchor == "start":
            start = 1
        else:                                  # centre the slice
            start = (SCOPE_RECORD_POINTS - points) // 2 + 1
        return start, start + points - 1

    def set_continuous(self, continuous: bool):
        """Free-run, or idle until asked for a shot.

        Switching to idle is not a disconnect: the transport stays up, so
        the next shot does not pay for a reconnect and the scope keeps the
        acquisition settings already pushed to it.
        """
        with QMutexLocker(self._mutex):
            self._continuous = bool(continuous)

    def is_continuous(self) -> bool:
        with QMutexLocker(self._mutex):
            return self._continuous

    def request_shot(self):
        """Take ONE waveform, as soon as the loop comes round.

        Latched rather than executed here, because this is called from the
        GUI thread and the transfer belongs to the worker's.  In continuous
        mode it simply cuts the current wait short.
        """
        with QMutexLocker(self._mutex):
            self._shot_requested = True

    def shot_pending(self) -> bool:
        with QMutexLocker(self._mutex):
            return self._shot_requested

    def set_channel(self, channel: str):
        """Switch acquisition channel ("CH1"/"CH2")."""
        with QMutexLocker(self._mutex):
            self._channel = channel
            self._channel_dirty = True

    def _snapshot_settings(self) -> dict:
        """Read every mutable setting once, so one acquisition is consistent."""
        with QMutexLocker(self._mutex):
            cfg = {
                "channel": self._channel,
                "smooth": self._smooth,
                "peaks": self._peaks,
                "threshold": self._threshold,
                "polarity": self._polarity,
                "envelope_ms": self._envelope_ms,
                "axis_labels": self._axis_labels,
                "average": self._average,
                "average_dirty": self._average_dirty,
                "channel_dirty": self._channel_dirty,
                "continuous": self._continuous,
                "shot": self._shot_requested,
                "points": self._points,
                "points_anchor": self._points_anchor,
                "poll_interval": self._poll_interval,
            }
            self._average_dirty = False
            self._channel_dirty = False
            self._shot_requested = False     # consumed by this pass
        return cfg

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
            self._ident = ident
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

    def _downsample(self, volts: list) -> tuple[list, int]:
        """Return at most SCOPE_WAVEFORM_DOWNSAMPLE points, plus the stride.

        The stride is returned because a plot drawn against xincr instead of
        stride*xincr silently compresses the time axis - the trace looks
        right and every time on it is wrong.
        """
        n = len(volts)
        if n <= SCOPE_WAVEFORM_DOWNSAMPLE:
            return list(volts), 1
        step = n / SCOPE_WAVEFORM_DOWNSAMPLE
        out = [volts[int(i * step)] for i in range(SCOPE_WAVEFORM_DOWNSAMPLE)]
        return out, step

    @staticmethod
    def _peaks_to_seconds(result: dict, fit: dict | None,
                          xincr: float, xzero: float) -> list:
        """Convert peak sample indices into seconds for the GUI."""
        def t_of(idx):
            return xzero + idx * xincr if idx is not None else float("nan")

        out = []
        fit_peaks = (fit or {}).get("peaks", [])
        for k, p in enumerate(result["peaks"]):
            out.append({
                "axis":             p.get("axis", f"#{k + 1}"),
                "index":            p["index"],
                "peak_volts":       p["volts"],
                "half_volts":       p["half_volts"],
                "left_seconds":     t_of(p["left_index"]),
                "right_seconds":    t_of(p["right_index"]),
                "centre_seconds":   t_of(p["centre_index"]),
                "fwhm_seconds":     p["fwhm_seconds"],
                "fit_fwhm_seconds": (fit_peaks[k]["fwhm_seconds"]
                                     if k < len(fit_peaks) else float("nan")),
                "resolved":         p["resolved"],
                "note":             p["note"],
            })
        return out

    # -----------------------------------------------------------------------
    # Main thread body
    # -----------------------------------------------------------------------

    def run(self):
        """Never-dying wrapper around the acquisition loop.

        WHY THIS EXISTS
        ---------------
        `_run_loop` used to BE `run`, and it only caught transport errors
        (TdsTimeoutError / TdsProtocolError / OSError) around the acquire +
        measure block.  Anything else raised there - a numpy error deep in
        the profile analysis, a bad preamble field, a bug - propagated out
        of `run`, which ends a QThread SILENTLY: no error signal, no log
        line the operator sees, and `scope_link` still holds the worker
        object, so `scope_connected` stays True and the scope itself is
        still happily answering on the bench.  What the operator sees is
        "Take shot" stuck on "Acquiring..." forever with a healthy-looking
        instrument.  That is exactly the stall this wrapper ends: a crash
        now reports itself as an error and the thread stays up.
        """
        try:
            self._run_loop()
        except BaseException as exc:                      # noqa: BLE001
            log.exception("scope_worker: acquisition loop died: %s", exc)
            try:
                self.error.emit(f"scope worker stopped unexpectedly: {exc}")
                self.waveform_ready.emit(ScopeState(
                    timestamp=time.time(),
                    connected=False,
                    channel=self._channel,
                    error=f"worker stopped: {exc}",
                ))
            except Exception:
                pass
            # Deliberately NOT re-raised: an unhandled exception escaping a
            # QThread's run() can take the whole process down depending on
            # the excepthook in force, and losing the app is a worse failure
            # than losing the scope link.  The traceback is in the log.
        finally:
            self._running = False

    def _run_loop(self):
        self._running = True
        transport     = None
        driver        = None
        backoff_idx   = 0
        was_idle      = False
        keepalive_due = 0.0

        while self._running:
            cfg = self._snapshot_settings()

            # ---- Connect if needed ----------------------------------------
            if driver is None:
                transport, driver = self._connect()
                if driver is None:
                    # The settings snapshot already consumed any shot
                    # request. Put it back: a press that lands during a
                    # reconnect should be honoured when the link returns,
                    # not silently dropped - a button that sometimes does
                    # nothing is worse than one that is slow.
                    if cfg["shot"]:
                        self.request_shot()
                    delay = self._backoff_delay(backoff_idx)
                    log.info("scope_worker: retrying in %.1f s", delay)
                    self._sleep_interruptible(delay)
                    backoff_idx += 1
                    self.waveform_ready.emit(ScopeState(
                        timestamp=time.time(),
                        connected=False,
                        channel=cfg["channel"],
                    ))
                    continue
                backoff_idx = 0
                cfg["average_dirty"] = True     # a fresh link knows nothing

            # ---- Idle: hold the link open, transfer nothing ---------------
            # Nothing is fetched unless this is continuous mode or someone
            # pressed for a shot. This is the default, and it is why
            # connecting no longer makes the app heavy.
            if not (cfg["continuous"] or cfg["shot"]):
                if not was_idle:
                    log.info("scope_worker: idle (link held open)")
                    self.waveform_ready.emit(ScopeState(
                        timestamp=time.time(),
                        connected=True,
                        idle=True,
                        continuous=False,
                        channel=cfg["channel"],
                    ))
                    was_idle = True
                    keepalive_due = time.monotonic() + _KEEPALIVE_S

                # A cheap query now and then, so a cable pulled while idle
                # is noticed rather than discovered on the next shot. ID? is
                # a few bytes; it costs nothing next to a full transfer.
                if time.monotonic() >= keepalive_due:
                    keepalive_due = time.monotonic() + _KEEPALIVE_S
                    try:
                        driver.reset_buffers()
                        ident = driver.identify()
                        if ident != self._ident:
                            # A different instrument is answering on this
                            # port.  THAT is worth a line; the same answer
                            # for the hundredth time is not.
                            log.info("scope_worker: instrument on %s changed "
                                     "— %s (was %s)",
                                     self._port, ident, self._ident)
                            self._ident = ident
                    except (TdsTimeoutError, TdsProtocolError, OSError) as exc:
                        log.warning("scope_worker: link lost while idle: %s",
                                    exc)
                        self.error.emit(str(exc))
                        self._disconnect(transport)
                        transport = None
                        driver = None
                        self.waveform_ready.emit(ScopeState(
                            timestamp=time.time(),
                            connected=False,
                            idle=True,
                            channel=cfg["channel"],
                            error=str(exc),
                        ))
                self._sleep_interruptible(_IDLE_TICK_S)
                continue

            if was_idle:
                log.info("scope_worker: acquiring (%s)",
                         "continuous" if cfg["continuous"] else "single shot")
                was_idle = False

            # ---- Acquire and measure --------------------------------------
            try:
                driver.reset_buffers()

                if cfg["average_dirty"]:
                    driver.set_average(cfg["average"])

                start, stop = self._slice_for(cfg["points"],
                                              cfg["points_anchor"])
                t_transfer = time.monotonic()
                raw_bytes, preamble = driver.acquire_waveform(
                    cfg["channel"], start=start, stop=stop)
                transfer_s = time.monotonic() - t_transfer
                volts = samples_to_volts(raw_bytes, preamble)

                xincr = float(preamble.get("XINCR", math.nan))
                xzero = float(preamble.get("XZERO", math.nan))

                state = self._measure(volts, preamble, xincr, xzero, cfg,
                                      driver, transfer_s)
                self.waveform_ready.emit(state)

            except (TdsTimeoutError, TdsProtocolError, OSError) as exc:
                msg = f"scope_worker: acquisition error on {self._port}: {exc}"
                log.error(msg)
                self.error.emit(str(exc))
                self._disconnect(transport)
                transport = None
                driver    = None
                self.waveform_ready.emit(ScopeState(
                    timestamp=time.time(),
                    connected=False,
                    continuous=cfg["continuous"],
                    channel=cfg["channel"],
                    error=str(exc),
                ))
                continue

            except Exception as exc:                      # noqa: BLE001
                # NOT a transport error: the link is fine, the ANALYSIS (or
                # something else host-side) blew up.  Dropping the port here
                # would be a lie about what failed, and letting it propagate
                # would kill the thread - which is what used to leave "Take
                # shot" stuck on "Acquiring..." with no error anywhere.  So:
                # log the traceback, tell the GUI, and stay connected so the
                # next press has a chance to work.
                log.exception("scope_worker: measurement failed on %s: %s",
                              self._port, exc)
                self.error.emit(f"measurement failed: {exc}")
                self.waveform_ready.emit(ScopeState(
                    timestamp=time.time(),
                    connected=True,
                    idle=False,
                    continuous=cfg["continuous"],
                    channel=cfg["channel"],
                    error=f"measurement failed: {exc}",
                ))
                if not cfg["continuous"]:
                    continue

            # ---- Rate-limit ------------------------------------------------
            # Only continuous mode waits. A single shot is finished the
            # moment it is measured, and the loop should be back to idle -
            # and responsive to the next press - immediately.
            if cfg["continuous"]:
                self._sleep_interruptible(cfg["poll_interval"])
                was_idle = False

        # ---- Cleanup on stop -----------------------------------------------
        self._disconnect(transport)
        log.info("scope_worker: stopped")

    def _measure(self, volts, preamble, xincr, xzero, cfg, driver,
                 transfer_s: float = float("nan")) -> ScopeState:
        """Run the profile analysis and build the snapshot.

        A failed measurement is NOT a failed acquisition: a beam that has
        gone away should show as "no measurement" with the trace still
        plotted, not as a dead instrument.  So FwhmError is caught here and
        recorded in the snapshot, while transport errors propagate.
        """
        ds_volts, stride = self._downsample(volts)
        xincr_ds = xincr * stride if xincr == xincr else math.nan

        common = dict(
            timestamp         = time.time(),
            connected         = True,
            idle              = False,
            continuous        = cfg["continuous"],
            channel           = cfg["channel"],
            xincr             = xincr,
            xzero             = xzero,
            volts_downsampled = ds_volts,
            xincr_downsampled = xincr_ds,
            preamble          = dict(preamble),
            smooth_window     = cfg["smooth"],
            points            = len(volts),
            transfer_seconds  = transfer_s,
        )

        # The raster period is configured in milliseconds because that is
        # what an operator can read off the trace; it becomes samples here,
        # where xincr is known.
        envelope_samples = 0
        if cfg["envelope_ms"] > 0 and xincr == xincr and xincr > 0:
            envelope_samples = int(round(cfg["envelope_ms"] * 1e-3 / xincr))

        try:
            result = analyse_profile(
                volts, xincr,
                polarity         = cfg["polarity"],
                smooth           = cfg["smooth"],
                max_peaks        = cfg["peaks"],
                peak_threshold   = cfg["threshold"],
                min_sep_frac     = SCOPE_PEAK_MIN_SEP_FRAC,
                envelope_samples = envelope_samples,
                quantum          = float(preamble.get("YMULT", 0.0) or 0.0),
                axis_labels      = cfg["axis_labels"],
            )
        except FwhmError as exc:
            log.debug("scope_worker: no measurement: %s", exc)
            return ScopeState(error=str(exc), **common)

        fit = fit_gaussians(result, xincr)
        fwhm_s, source = best_fwhm(result, fit, min_r2=SCOPE_FIT_MIN_R2)

        # The scope's own PWIDTH: an independent check, best effort only.
        pwidth = math.nan
        try:
            pwidth = driver.measure_immediate("PWIDTH", cfg["channel"])
        except (TdsProtocolError, TdsTimeoutError) as exc:
            log.debug("scope_worker: PWIDTH unavailable: %s", exc)

        corrected_ds, _ = self._downsample(result["corrected"])
        fit_curve_ds, _ = (self._downsample(fit["curve"]) if fit else ([], 1))

        unresolved = [p["note"] for p in result["peaks"] if not p["resolved"]]
        clip = result["clipping"]
        if clip["low"] or clip["high"]:
            where = "bottom" if clip["low"] else "top"
            pct = 100 * max(clip["low_fraction"], clip["high_fraction"])
            unresolved.insert(0, f"trace is clipped at the {where} of the "
                                 f"screen ({pct:.0f} % of samples) - widths "
                                 f"from a flat-topped peak are not real")

        return ScopeState(
            fwhm_seconds          = fwhm_s,
            fwhm_samples          = (fwhm_s / xincr
                                     if (xincr == xincr and xincr > 0
                                         and fwhm_s == fwhm_s) else math.nan),
            fwhm_source           = source,
            peaks                 = self._peaks_to_seconds(result, fit,
                                                           xincr, xzero),
            n_peaks               = result["n_peaks"],
            n_resolved            = result["n_resolved"],
            mean_fwhm_seconds     = result["mean_fwhm_seconds"],
            fwhm_spread           = result["fwhm_spread"],
            fwhm_x_seconds        = result["fwhm_x_seconds"],
            fwhm_y_seconds        = result["fwhm_y_seconds"],
            xy_ratio              = result["xy_ratio"],
            separations_seconds   = result["separations_seconds"],
            fit_fwhm_seconds      = (fit["mean_fwhm_seconds"] if fit else math.nan),
            fit_r_squared         = (fit["r_squared"] if fit else math.nan),
            scope_pwidth_seconds  = pwidth,
            baseline_volts        = result["baseline"],
            signal_to_noise       = result["signal_to_noise"],
            flipped               = result["flipped"],
            envelope_samples      = result["envelope_samples"],
            clipped_low           = result["clipping"]["low"],
            clipped_high          = result["clipping"]["high"],
            clipped_fraction      = max(result["clipping"]["low_fraction"],
                                        result["clipping"]["high_fraction"]),
            corrected_downsampled = corrected_ds,
            fit_curve_downsampled = fit_curve_ds,
            error                 = (unresolved[0] if unresolved else ""),
            **common,
        )
