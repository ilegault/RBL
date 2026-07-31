"""
beamline.py
Beamline: the single owner of every instrument (LabJack T7, Galil DMC-4103,
two DG1022Z function generators) and the single place live values get
converted from raw volts/counts into physical units and published as typed
snapshots.

No QWidget holds a driver instance or is responsible for its lifecycle;
tabs reach the driver objects through a thin delegating property/proxy so
their existing call sites don't change, but construction and final teardown
happen here, once.
"""
import atexit
import math
import time

from PySide6.QtCore import QObject, Signal

from rbl.config import hardware_config as SC
from rbl.config.labjack_stream_config import (
    DEFAULT_PROFILE, STREAM_PROFILES, DEFAULT_SINGLE_CHANNEL,
    SINGLE_CHANNEL_CHOICES, GUI_REFRESH_HZ, is_single_channel,
)
from rbl.hardware.current_monitor import voltage_to_current
from rbl.hardware.amp_monitor import monitor_to_kv, monitor_to_ma
from rbl.hardware.waveform_ring import AlignedWaveHistory
from rbl.hardware.waveform_period import estimate_period_samples, cycle_slice
from rbl.hardware import beam_reconstruction as BR
from rbl.hardware.labjack_driver import LabJackT7
from rbl.hardware.labjack_stream_worker import LabJackStreamWorker
from rbl.hardware.galil_driver import GalilController
from rbl.hardware.funcgen_safety import channel_peak_volts, PEAK_MAX_VOLTS
from rbl.state.setpoints import FuncGenSetpoints
from rbl.state.snapshots import (
    AxisSnapshot, MotorState, ChannelSnapshot, ChannelParams, FuncGenState,
    LogAmpState, AmpChannelSnapshot, AmpState,
)


class Beamline(QObject):
    motors_changed   = Signal(object)   # MotorState
    logamps_changed  = Signal(object)   # LogAmpState
    amps_changed     = Signal(object)   # AmpState
    funcgens_changed = Signal(object)   # FuncGenState
    timebase_changed = Signal(dict)     # {"A": "INT"/"EXT"/"?"/"—", "B": ...}
    command_failed   = Signal(str, str)  # subsystem, message

    # Motion commanded from ANY screen, published so every screen sees it.
    #
    # There is one Command Console (Stepper Motors tab) and there are two
    # places a slit can be moved from (that tab and the Overview). Before
    # these signals, a move commanded on the Overview reached the Galil
    # without ever appearing in the console and without moving the Stepper
    # Motors tab's Target box — the operator's own action was invisible on the
    # screen that exists to show it. Both are emitted by move_slit(), which is
    # the single path to the controller, so neither screen can miss a command
    # the other made.
    motor_logged     = Signal(str)        # one console line, already formatted
    slit_target_changed = Signal(str, float)   # slit label, absolute mm

    # LabJack connection lifecycle. Re-emitted here (rather than reaching into
    # widgets directly) so this class stays Qt-signal-only, no GUI knowledge.
    labjack_connected    = Signal(str)    # serial
    labjack_disconnected_evt = Signal()
    window_ready         = Signal(dict)   # re-emitted stream payload, for tabs
                                           # still rendering it directly
    stream_error         = Signal(str)
    profile_changed      = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Last-seen slit edges (SIGNED mm) and log-amp currents (Amps), cached
        # so reconstruct_beam() can be called on demand with whatever spot
        # size the caller currently has selected, without re-deriving them.
        self._slit_edges_mm: dict[str, float] = {}
        self._log_amp_currents: dict[str, float] = {}

        # ── LabJack T7 + stream worker ──────────────────────────────────────
        #
        # ONE physical T7 -> ONE LabJackT7 instance -> ONE stream worker
        # reading all channels at high rate. Both the Beam Current tab
        # (AIN0-3, log amps) and the HV Amplifier tab (AIN6-13, EEL5000
        # monitors) subscribe to window_ready and filter for their own
        # channels. Never let a tab open its own handle.
        self.lj                = LabJackT7()
        self._lj_worker         = None
        self.active_profile     = DEFAULT_PROFILE
        # Target channel for single-channel profiles (SINGLE_FAST/SINGLE_HIRES).
        # Ignored while a multi-channel profile is active.
        self.active_channel     = DEFAULT_SINGLE_CHANNEL
        self._profile_updating  = False   # re-entrancy guard for set_profile / set_channel
        # Shared monotonic epoch for every stream worker this connection spawns.
        # Set on connect so payload timestamps stay continuous across the
        # stop/reconfigure/start cycles that profile and channel switches need.
        self._stream_t0         = None
        # Recent raw HV VOLTAGE monitor windows, kept so the Overview's pair
        # trace can still show a whole cycle when the drive is slower than one
        # stream window. Cleared whenever the stream restarts: a new profile
        # means a new sample rate, and stitching across that seam would put two
        # different time bases in one trace.
        self._amp_waves = AlignedWaveHistory(self._WAVE_HISTORY_SAMPLES)

        # ── Galil DMC-4103 ───────────────────────────────────────────────────
        self.galil = GalilController()

        # ── DG1022Z function generators ──────────────────────────────────────
        # None until FuncGenTab connects them (each needs a VISA resource
        # string at construction time, unlike Galil/LabJack).
        self.dg_a = None
        self.dg_b = None

        # The four channels' commanded parameters, shared by every screen that
        # edits them (Function Generators per channel, Overview per axis). One
        # model, so the two can never show different setpoints for the same
        # channel and an Apply from either cannot silently overwrite the other.
        self.funcgen_setpoints = FuncGenSetpoints(self)

        # Last-read clock source per unit, refreshed by read_timebase() and
        # republished on every FuncGenState — see that method's docstring.
        self._timebase = {"A": "—", "B": "—"}

        # Last-resort safety net: if the process is torn down without a clean
        # closeEvent (e.g. an unhandled exit), still stop the LabJack stream
        # and close the handle so the T7 is never left in stream mode.
        atexit.register(self._emergency_labjack_shutdown)

    # ---- Motors ----------------------------------------------------------------

    def ingest_motor_poll(self, snapshot: dict, zeroed: bool):
        """snapshot: axis letter -> {pos, moving, switches, enabled}, exactly
        what GalilPollWorker.state emits."""
        axes = {}
        edges = {}
        for axis_letter, st in snapshot.items():
            slit = SC.AXIS_NAMES[axis_letter]
            pos_mm = SC.counts_to_mm(axis_letter, st["pos"])
            axes[slit] = AxisSnapshot(
                pos_counts=st["pos"],
                pos_mm=pos_mm,
                moving=st["moving"],
                enabled=st.get("enabled", True),
                switches=st["switches"],
            )
            # The Galil reports each slit as a distance from centre with no
            # sign; the '-' slits live on the negative side of the axis.
            edges[slit] = abs(pos_mm) if slit.endswith("+") else -abs(pos_mm)
        self._slit_edges_mm = edges
        self.motors_changed.emit(MotorState(connected=True, zeroed=zeroed, axes=axes))

    def motors_disconnected(self):
        self._slit_edges_mm = {}
        self.motors_changed.emit(MotorState(connected=False, zeroed=False, axes={}))

    # ---- Log amps + HV amplifiers (one LabJack window feeds both) -------------

    def ingest_labjack_window(self, payload: dict, active_profile: str = ""):
        """One stream window from LabJackStreamWorker -> LogAmpState + AmpState.

        Both live on the same physical LabJack and arrive in the same
        payload, so one ingestion call keeps them from disagreeing about
        "now" the way two independently-timed poll paths could.
        """
        channels = payload["channels"]

        currents = {}
        for ain, slit in SC.LABJACK_CHANNEL_MAP.items():
            ch = channels.get(ain)
            if ch is None:
                # WAVEFORM profile: log amps not sampled this window.
                currents[slit] = float("nan")
                continue
            currents[slit] = voltage_to_current(
                ch["mean"], SC.LOG_AMP_V_AT_1NA, SC.LOG_AMP_V_AT_1MA
            )
        self._log_amp_currents = currents
        self.logamps_changed.emit(LogAmpState(connected=True, currents=dict(currents)))

        self._store_amp_waves(channels)
        traces = self._cycle_traces(payload)

        amp_channels = {}
        for amp in SC.AMP_LABELS:
            v_ain = SC.AMP_CHANNEL_MAP[amp]["voltage"]
            i_ain = SC.AMP_CHANNEL_MAP[amp]["current"]
            v_ch = channels.get(v_ain)
            i_ch = channels.get(i_ain)

            peak_kv = pkpk_kv = rms_kv = raw_v = float("nan")
            rms_ma = raw_i = float("nan")
            wave_kv, span_s, freq_hz = traces.get(
                amp, ((), float("nan"), float("nan")))
            if v_ch is not None:
                peak_kv = monitor_to_kv(v_ch["peak"])
                pkpk_kv = v_ch["pk_pk"] * SC.VOLTAGE_MONITOR_KV_PER_VOLT
                rms_kv  = monitor_to_kv(v_ch["rms"])
                wave = v_ch.get("waveform")
                raw_v = float(sum(wave) / len(wave)) if wave is not None and len(wave) else v_ch["rms"]
            if i_ch is not None:
                rms_ma = monitor_to_ma(i_ch["rms"])
                wave = i_ch.get("waveform")
                raw_i = float(sum(wave) / len(wave)) if wave is not None and len(wave) else i_ch["rms"]

            amp_channels[amp] = AmpChannelSnapshot(
                peak_kv=peak_kv, pkpk_kv=pkpk_kv, rms_kv=rms_kv,
                rms_ma=rms_ma, raw_v=raw_v, raw_i=raw_i, wave_kv=wave_kv,
                wave_span_s=span_s, wave_freq_hz=freq_hz,
            )
        self.amps_changed.emit(
            AmpState(connected=True, channels=amp_channels, active_profile=active_profile)
        )

    # Points kept per channel per window for the Overview's overlaid pair
    # trace. Enough to read a triangle's shape at a glance; small enough that
    # four of them crossing a Qt signal at 10 Hz costs nothing.
    _WAVE_POINTS = 120

    # Cycles of the measured drive to put in that trace. Two, not one: one
    # cycle drawn edge to edge gives nothing to compare its start against, and
    # a pair whose members repeat at slightly different rates only separates
    # visibly over more than a single period.
    _WAVE_TARGET_CYCLES = 2

    # Cycles the period is measured over, when that many are on hand. Well
    # above the two the estimator needs, because accuracy near its floor is
    # poor (a 20 Hz drive measured from a single 0.1 s window — two cycles —
    # came out 5% high) and the caption quotes this number as a frequency.
    _WAVE_RATE_CYCLES = 8

    # Raw samples kept per voltage channel. The bound is samples rather than
    # seconds because that is what costs memory and FFT time; what it buys in
    # seconds depends on the profile (~4 s on FULL, ~0.33 s on SINGLE_FAST),
    # and that in turn sets the slowest drive whose period can be measured —
    # roughly 0.5 Hz on FULL. Slower than that, the trace falls back to one
    # raw window and says so rather than pretending to have found a cycle.
    _WAVE_HISTORY_SAMPLES = 32768

    def _store_amp_waves(self, channels: dict):
        """File this window's raw HV voltage monitors into the history."""
        window = {}
        for amp in SC.AMP_LABELS:
            ch = channels.get(SC.AMP_CHANNEL_MAP[amp]["voltage"])
            wave = ch.get("waveform") if ch is not None else None
            if wave is not None and len(wave):
                window[SC.AMP_CHANNEL_MAP[amp]["voltage"]] = wave
        self._amp_waves.push(window)

    def _measure_group(self, ains: list, latest: int, available: int) -> tuple:
        """(samples, reference channel, period in samples) for one trace group.

        Starts from the newest window alone — at any raster rate above a few Hz
        the cycles are already in there, and keeping the transform small is
        what lets this run on every frame — and reaches back into the history
        only when that record is too short to measure a period well from.
        """
        seg = self._amp_waves.aligned_tail(ains, min(latest, available))
        if not seg:
            return {}, None, float("nan")
        # The harder-driven plate is the cleaner trigger: a plate sitting near
        # zero is mostly monitor noise, and triggering on noise moves BOTH
        # traces, since the pair shares the window by design.
        ref = max(ains, key=lambda a: float(seg[a].max() - seg[a].min()))

        period = estimate_period_samples(seg[ref])
        # A period measured from a record barely longer than itself is a poor
        # one — the autocorrelation has only a few samples of overlap left at
        # that lag — and a 5% error on the frequency is the difference between
        # a caption you can trust and one you cannot. Widen the record until it
        # holds a comfortable number of cycles; when nothing repeated inside
        # one window at all, widen it to everything and try again.
        wanted = (available if math.isnan(period)
                  else min(available, int(period * self._WAVE_RATE_CYCLES) + 2))
        if wanted > len(seg[ref]):
            longer = self._amp_waves.aligned_tail(ains, wanted)
            refined = estimate_period_samples(longer[ref])
            if not math.isnan(refined):
                return longer, ref, refined
            if not math.isnan(period):
                # The longer record disagrees (the drive changed inside it, or
                # it spans a settling transient). Keep the estimate that worked
                # and the longer record with it — the extra samples are the
                # room the trigger needs to hold the trace still.
                return longer, ref, period
        return seg, ref, period

    def _trace_groups(self, axis: str) -> list:
        """The amplifiers that share one trace window on *axis*.

        Normally the pair, together: the window is chosen once, from whichever
        plate is driven harder, and applied to both. Choosing it per channel
        would align each plate to its own zero crossing and delete the phase
        relationship the pair trace exists to show.

        A window can only be shared by channels sampled in the SAME stream
        windows, though, and a single-channel profile samples one plate of the
        pair. The survivor then gets a window of its own — half a picture, but
        the trace is what tells you that profile is running.
        """
        pair = [f"{axis}+", f"{axis}-"]
        ains = [SC.AMP_CHANNEL_MAP[a]["voltage"] for a in pair]
        if self._amp_waves.aligned_length(ains) >= 2:
            return [pair]
        return [[amp] for amp, ain in zip(pair, ains)
                if self._amp_waves.aligned_length([ain]) >= 2]

    def _cycle_traces(self, payload: dict) -> dict:
        """Per amplifier: (wave_kv, span_s, freq_hz) for the Overview trace."""
        dt = payload.get("sample_period")
        if not dt:
            # Pure-math callers (tests, the self-test) omit it; the nominal
            # window duration over its sample count is the same number to
            # within the device's rate rounding.
            n = payload.get("window_samples") or 0
            dt = (1.0 / GUI_REFRESH_HZ) / n if n else float("nan")

        traces = {}
        for axis in ("X", "Y"):
            for amps in self._trace_groups(axis):
                ains = [SC.AMP_CHANNEL_MAP[a]["voltage"] for a in amps]
                available = self._amp_waves.aligned_length(ains)
                latest = payload.get("window_samples") or available
                seg, ref, period = self._measure_group(ains, latest, available)
                if not seg:
                    continue

                bounds = cycle_slice(seg[ref], period, self._WAVE_TARGET_CYCLES)
                if bounds is None:
                    # No cycle to lock to (flat, noise, or slower than the
                    # history): show the newest raw window, which is what this
                    # trace always showed before it could measure anything.
                    start = max(0, len(seg[ref]) - latest)
                    stop = len(seg[ref])
                    freq_hz = float("nan")
                else:
                    start, stop = bounds
                    freq_hz = 1.0 / (period * dt) if dt else float("nan")

                span_s = (stop - start) * dt
                for amp, ain in zip(amps, ains):
                    traces[amp] = (self._decimate_wave(seg[ain][start:stop]),
                                   span_s, freq_hz)
        return traces

    @staticmethod
    def _decimate_wave(wave) -> tuple:
        """Thin a slice of raw samples down to _WAVE_POINTS on a UNIFORM grid.

        Uniform striding, deliberately, not the envelope-preserving min/max
        binning the amplifier tab's plot uses: min/max emits each point at its
        own sample position, so two channels decimated that way no longer
        share an x grid — and a pair drawn on two different grids shows a
        phase difference that is pure resampling artefact. Peaks matter on a
        plot you read values off; a shared time base matters here.

        Striding is only safe because the caller hands over a couple of cycles
        rather than a whole window: at ~60 points per cycle the shape survives.
        Striding 200 cycles down to 120 points, which is what this used to be
        given, is aliasing — it drew a beat pattern of the decimation.
        """
        if wave is None or len(wave) == 0:
            return ()
        step = max(1, len(wave) // Beamline._WAVE_POINTS)
        return tuple(
            float(v) * SC.VOLTAGE_MONITOR_KV_PER_VOLT
            for v in wave[::step][:Beamline._WAVE_POINTS]
        )

    def _mark_labjack_disconnected(self):
        self._log_amp_currents = {}
        self._amp_waves.clear()
        self.logamps_changed.emit(LogAmpState(connected=False))
        self.amps_changed.emit(AmpState(connected=False))

    # ---- LabJack connection lifecycle -------------------------------------------

    def connect_labjack(self, conn_type: str, identifier: str):
        """Raises on failure; caller (the GUI) shows the error."""
        if self.lj.connected:
            return
        self.lj.connect(conn_type, identifier)
        serial = self.lj.serial_number()
        self._stream_t0 = time.monotonic()   # anchor the shared timeline
        self._start_stream_worker(self.active_profile)
        self.labjack_connected.emit(serial)

    def _start_stream_worker(self, profile_name: str):
        """Create and start a stream worker for *profile_name*.

        For single-channel profiles the current channel target is passed as
        the override. Caller is responsible for stopping any existing worker
        first.
        """
        override = self.active_channel if is_single_channel(profile_name) else None
        worker = LabJackStreamWorker(
            self.lj.handle, profile_name, override, t0=self._stream_t0
        )
        worker.window_ready.connect(self._on_stream_window)
        worker.error.connect(self._on_stream_error)
        worker.start()
        self._lj_worker = worker

    def _on_stream_window(self, payload: dict):
        self.ingest_labjack_window(payload, active_profile=self.active_profile)
        self.window_ready.emit(payload)

    def _on_stream_error(self, msg: str):
        # Tabs each show their own warning box (via stream_error); we tear
        # the connection down the same way a GUI-initiated disconnect would.
        self.disconnect_labjack()
        self.stream_error.emit(msg)

    def set_stream_profile(self, profile_name: str):
        """Stop the running stream, reconfigure, and restart with a new profile.

        Hardware constraint: the T7 scan list cannot be changed mid-stream.
        A full eStreamStop -> reconfigure -> eStreamStart cycle is required.
        This is user-driven and takes ~tens of ms — never call mid-capture.
        """
        if self._profile_updating:
            return   # ignore re-entrant call while a switch is in progress
        if profile_name not in STREAM_PROFILES:
            return

        # Remember the request even while disconnected so it takes effect on
        # the next connect (the stream worker is started from active_profile).
        changed = (profile_name != self.active_profile)
        self.active_profile = profile_name
        if not self.lj.connected or self._lj_worker is None:
            return
        if not changed:
            return

        self._profile_updating = True
        try:
            self._restart_stream_worker(profile_name)
            self.profile_changed.emit(profile_name)
        finally:
            self._profile_updating = False

    def set_stream_channel(self, ain_name: str):
        """Change which channel a single-channel profile streams.

        No-op unless a single-channel profile is active. Like a profile
        switch, changing the scan list requires a full stop -> reconfigure ->
        start cycle (the T7 cannot change its scan list mid-stream).
        """
        if self._profile_updating:
            return
        if ain_name not in SINGLE_CHANNEL_CHOICES:
            return   # not a valid single-channel target

        # Remember the target regardless of the active profile so a later
        # switch to a single-channel profile starts on the channel picked.
        changed = (ain_name != self.active_channel)
        self.active_channel = ain_name
        if not changed:
            return
        if not self.lj.connected or self._lj_worker is None:
            return   # remembered; applied when a single-channel profile starts
        if not is_single_channel(self.active_profile):
            return   # remembered; the live scan list is fixed in multi-channel mode

        self._profile_updating = True
        try:
            self._restart_stream_worker(self.active_profile)
        finally:
            self._profile_updating = False

    def _restart_stream_worker(self, profile_name: str):
        """Stop the running worker (if any) and start a fresh one.

        Hardware constraint: the T7 scan list cannot be changed mid-stream, so
        both profile switches and single-channel target changes go through
        this stop -> reconfigure -> start cycle. Callers hold _profile_updating.
        """
        if self._lj_worker is not None:
            self._lj_worker.stop()
            self._lj_worker.wait(5000)
            self._lj_worker = None
        # Samples either side of this restart were taken at different rates
        # (and, for a single-channel switch, on different plates). Stitching
        # across the seam would measure a period that never existed.
        self._amp_waves.clear()
        self._start_stream_worker(profile_name)

    def disconnect_labjack(self):
        # Stop the stream before closing the handle (hardware order matters).
        if self._lj_worker is not None:
            self._lj_worker.stop()
            if not self._lj_worker.wait(3000):
                # The drain thread did not exit in time (e.g. blocked on a
                # slow eStreamRead). Fall through anyway: LabJackT7.disconnect()
                # force-stops the stream on the handle before closing it, so
                # the device is never left streaming even in this degraded case.
                pass
            self._lj_worker = None
        self._stream_t0 = None
        self.lj.disconnect()   # force-stops the stream, then closes the handle
        self._mark_labjack_disconnected()
        self.labjack_disconnected_evt.emit()

    def _emergency_labjack_shutdown(self):
        """atexit safety net — never leave the T7 in stream mode.

        Runs at interpreter exit for any path that skipped shutdown(). It must
        not raise; a best-effort stream stop + handle close is all that matters.
        """
        try:
            if self._lj_worker is not None:
                self._lj_worker.stop()
                self._lj_worker.wait(2000)
                self._lj_worker = None
        except Exception:
            pass
        try:
            self.lj.stop_stream()   # explicit, in case the worker never ran finally
            self.lj.disconnect()
        except Exception:
            pass

    def reconstruct_beam(self, sigma_mm: float, span_x_mm: float = 0.0,
                          span_y_mm: float = 0.0):
        """Beam position from the last-seen currents + slit edges.

        The one call site every consumer (the log-amp tab's beam indicator,
        and later the Overview tab) uses, so they read the same currents and
        edges and can never disagree about where the beam is — only the
        assumed spot size stays a per-caller / operator setting.

        Returns None if fewer than all four slit edges are known yet.
        """
        if len(self._slit_edges_mm) < 4:
            return None
        return BR.reconstruct(
            self._log_amp_currents, self._slit_edges_mm, sigma_mm, span_x_mm, span_y_mm
        )

    # ---- Function generators ---------------------------------------------------

    def ingest_funcgen_readback(self, connected: dict, timebase: dict, readback: dict):
        """connected: {"A": bool, "B": bool}. timebase: {"A": "INT"/"EXT", ...}.
        readback: {"A1": {shape, freq, amp, offset, phase, output}, ...} —
        exactly DG1022Z.get_state()'s shape, keyed by channel key.
        """
        channels = {}
        for key, state in readback.items():
            if "error" in state:
                continue
            channels[key] = ChannelSnapshot(
                shape=state["shape"],
                freq_hz=state["freq"],
                amp_vpp=state["amp"],
                offset_v=state["offset"],
                phase_deg=state["phase"],
                output_on=state["output"],
            )
        # An empty `timebase` means "no fresh reading" — carry the cached one
        # rather than publishing blanks that would flicker a second screen's
        # lock indicator off and on between clock reads.
        self.funcgens_changed.emit(FuncGenState(
            connected=dict(connected),
            timebase=dict(timebase) if timebase else dict(self._timebase),
            channels=channels,
        ))

    def funcgens_disconnected(self):
        self._timebase = {"A": "—", "B": "—"}
        self.funcgens_changed.emit(FuncGenState(
            connected={"A": False, "B": False}, timebase=dict(self._timebase),
            channels={},
        ))

    # ---- Command surface ---------------------------------------------------------
    #
    # The single path every caller — the funcgen tab, the motor tab, and any
    # future Overview control — must go through to reach the driver. In
    # particular the ±5 V combined-peak interlock lives here, not in a widget:
    # a control that reached the driver by another route would bypass it
    # entirely. Failures are reported via command_failed rather than raised,
    # so a bad Overview-tab command can't take down the event loop.

    def _gen_for(self, gen_letter: str):
        return self.dg_a if gen_letter == "A" else self.dg_b

    def set_channel(self, key: str, params: ChannelParams) -> bool:
        """Push one channel's parameters to its generator.

        `key` is e.g. "A1" (generator letter + channel number). Returns True
        on success. The combined-peak interlock (|offset| + amp/2) is
        enforced unconditionally: a peak above PEAK_MAX_VOLTS is rejected
        here regardless of what any caller already checked.
        """
        gen_letter, channel = key[0], int(key[1])
        gen = self._gen_for(gen_letter)
        if gen is None:
            self.command_failed.emit("funcgen", f"{key}: generator not connected")
            return False

        peak = channel_peak_volts(params.shape, params.amp_vpp, params.offset_v)
        if peak > PEAK_MAX_VOLTS + 1e-9:
            self.command_failed.emit(
                "funcgen",
                f"{key}: combined peak {peak:.4g} V exceeds the "
                f"{PEAK_MAX_VOLTS:.0f} V amplifier input limit",
            )
            return False

        try:
            warn = gen.set_waveform(channel, params.shape, params.freq_hz,
                                     params.amp_vpp, params.offset_v, params.phase_deg)
            gen.set_output_load(channel, params.load)
            gen.set_start_phase(channel, params.start_phase_deg)
            if params.output_on:
                gen.output_on(channel)
            else:
                gen.output_off(channel)
            if warn:
                self.command_failed.emit("funcgen", f"{key}: {warn}")
            return True
        except Exception as e:
            self.command_failed.emit("funcgen", f"{key}: {e}")
            return False

    def apply_all_channels(self, params_by_key: dict) -> bool:
        """Configure + enable every given channel together.

        `params_by_key`: {"A1": ChannelParams, ...} for whichever channels
        the caller wants applied — channels whose generator isn't connected
        are silently skipped.

        Preserves the three-phase ordering exactly (load-bearing for raster
        alignment): configure every channel first (outputs untouched), THEN
        fire every output-enable back-to-back, THEN run :PHASe:SYNChronize
        last on each connected unit, once the relays have settled.
        :OUTPut ON only closes a relay — it does not reset the waveform's DDS
        phase accumulator, so aligning before the relays are closed would
        lock in the wrong start point.

        The interlock is checked for EVERY channel before anything is sent —
        applying a partial raster is worse than applying none, so if any
        channel is over the hard ceiling, nothing goes out at all.
        """
        active = []   # (key, gen_letter, channel, gen, params)
        for key, params in params_by_key.items():
            gen = self._gen_for(key[0])
            if gen is not None:
                active.append((key, key[0], int(key[1]), gen, params))
        if not active:
            return False

        blocked = []
        for key, _, _, _, params in active:
            peak = channel_peak_volts(params.shape, params.amp_vpp, params.offset_v)
            if peak > PEAK_MAX_VOLTS + 1e-9:
                blocked.append(key)
        if blocked:
            self.command_failed.emit(
                "funcgen",
                "Apply All blocked — over the "
                f"{PEAK_MAX_VOLTS:.0f} V limit: " + ", ".join(blocked),
            )
            return False

        # Phase 1: configure every channel (outputs untouched).
        try:
            for key, gen_letter, channel, gen, params in active:
                warn = gen.set_waveform(channel, params.shape, params.freq_hz,
                                         params.amp_vpp, params.offset_v, params.phase_deg)
                gen.set_output_load(channel, params.load)
                gen.set_start_phase(channel, params.start_phase_deg)
                if warn:
                    self.command_failed.emit("funcgen", f"{key}: {warn}")
        except Exception as e:
            self.command_failed.emit("funcgen", f"Apply All failed during configure: {e}")
            return False

        # Phase 2: enable outputs — OFF ones first, then all ON back-to-back.
        try:
            for key, gen_letter, channel, gen, params in active:
                if not params.output_on:
                    gen.output_off(channel)
            for key, gen_letter, channel, gen, params in active:
                if params.output_on:
                    gen.output_on(channel)
        except Exception as e:
            self.command_failed.emit("funcgen", f"Apply All failed during output enable: {e}")
            return False

        # Phase 3: align each connected unit's two channels, last.
        time.sleep(0.05)   # let the output relays physically settle first
        for gen_letter in ("A", "B"):
            gen = self._gen_for(gen_letter)
            if gen is not None:
                try:
                    gen.align_phase(1)
                except Exception as e:
                    self.command_failed.emit("funcgen", f"Gen {gen_letter}: align phase failed: {e}")

        return True

    # ---- Cross-unit timebase (10 MHz reference) --------------------------------
    #
    # Lives here, not in a tab, for the same reason the peak interlock does:
    # more than one screen offers the control now, and the both-EXT guard below
    # protects the instruments, so it must sit on the single path to the
    # drivers rather than in whichever widget happens to be on top.

    SETTLE_S = 3.0   # PLL settling time before the lock readback is trusted

    def read_timebase(self) -> dict:
        """Each unit's active clock source: "INT", "EXT", "?" or "—".

        Queries the instruments and caches the answer. The cache is what gets
        published on every FuncGenState, so a second screen can show the lock
        state without either polling the clock source at readback rate (two
        extra VISA round trips every 500 ms for a value that only changes when
        somebody changes it) or reaching for a driver of its own.
        """
        clocks = {}
        for letter in ("A", "B"):
            gen = self._gen_for(letter)
            if gen is None:
                clocks[letter] = "—"
                continue
            try:
                clocks[letter] = gen.get_reference_clock()
            except Exception:
                clocks[letter] = "?"
        self._timebase = clocks
        return dict(clocks)

    @property
    def timebase_locked(self) -> bool:
        """True only for the one configuration that actually shares a clock."""
        return self._timebase.get("A") == "INT" and self._timebase.get("B") == "EXT"

    def set_shared_timebase(self, enabled: bool):
        """Lock (or unlock) Gen B to Gen A's 10 MHz reference.

        Returns (ok, message): ok is True when the REQUESTED state was
        actually reached — a failed lock returns False so the caller can put
        its checkbox back rather than showing a lock that isn't there.

        Only Gen B is ever set to EXT. The rear-panel [10MHz In/Out] connector
        is BIDIRECTIONAL and its direction follows the clock-source setting,
        so two units both driving it is not a misconfiguration to warn about
        afterwards — it damages the instruments. Hence the guard below runs
        before anything is written.
        """
        gen_a, gen_b = self._gen_for("A"), self._gen_for("B")
        if gen_a is None or gen_b is None:
            return False, "Both generators must be connected to share a timebase."

        try:
            if not enabled:
                gen_a.set_reference_clock("INTernal")
                gen_b.set_reference_clock("INTernal")
                self.timebase_changed.emit(self.read_timebase())  # refreshes cache
                return True, ("Independent internal clocks — X/Y phase will "
                              "drift across the two units.")

            if gen_a.get_reference_clock() == "EXT":
                msg = (
                    "Gen A is currently set to EXTernal reference.\n\n"
                    "One unit must drive the 10 MHz reference (INT) and the "
                    "other must follow it (EXT). Setting both to EXT causes "
                    "both instruments to drive the rear-panel [10MHz In/Out] "
                    "connector simultaneously — this will damage the "
                    "instruments.\n\n"
                    "Return Gen A to its internal clock first (send "
                    ":SYSTem:ROSCillator:SOURce INTernal to Gen A via the SCPI "
                    "console), then enable sharing."
                )
                self.command_failed.emit(
                    "funcgen",
                    "Timebase refused — Gen A is already EXT; both EXT would collide")
                return False, msg

            gen_a.set_reference_clock("INTernal")
            locked, actual = gen_b.verify_external_lock(settle_s=self.SETTLE_S)
            self.timebase_changed.emit(self.read_timebase())
            if locked:
                return True, "Locked: Gen B follows Gen A's 10 MHz reference."
            return False, (
                f"Gen B was set to external 10 MHz reference but its readback "
                f"is {actual!r} — the DG1022Z silently falls back to INT when "
                f"no valid signal is present.\n\n"
                "Checklist:\n"
                "  • BNC cable from Gen A [10MHz Out] → Gen B [10MHz In]\n"
                "  • Reference level must be 250 mVpp – 5 Vpp\n"
                "  • The [10MHz In/Out] connector is BIDIRECTIONAL — its "
                "direction is set by the clock source selection. Both units "
                "set to INT will each try to drive the connector "
                "simultaneously, which can damage the instruments."
            )
        except Exception as e:
            self.command_failed.emit("funcgen", f"timebase: {e}")
            return False, str(e)

    def all_outputs_off(self):
        """Turn off every function-generator channel's output.

        Deliberately narrow: this only opens the output relays, the same as
        a manual "Output OFF" click on each channel. It does not disconnect,
        zero any setpoint, or otherwise touch the generators.
        """
        for gen_letter in ("A", "B"):
            gen = self._gen_for(gen_letter)
            if gen is None:
                continue
            for channel in (1, 2):
                try:
                    gen.output_off(channel)
                except Exception as e:
                    self.command_failed.emit("funcgen", f"{gen_letter}{channel}: {e}")

    def axis_letter_for(self, slit: str):
        """Galil axis letter for a slit label ("X+" -> "A"), or None."""
        return next((a for a, j in SC.AXIS_NAMES.items() if j == slit), None)

    def log_motor(self, line: str):
        """Put one line on the Stepper Motors tab's Command Console.

        Anything that reaches the Galil should say so here, whichever screen
        it came from — that console is the app's record of what was sent, and
        a command missing from it reads as a command that never happened.
        """
        self.motor_logged.emit(line)

    def note_slit_target(self, slit: str, mm: float):
        """Publish a commanded slit target so every screen shows the same one.

        Separate from move_slit() because the Stepper Motors tab issues its
        own move through the driver (it has the soft-limit dialog and the
        counts/mm unit toggle that the Overview deliberately does not), and
        the Overview still has to learn the target that move set.
        """
        self.slit_target_changed.emit(slit, float(mm))

    def move_slit(self, slit: str, mm: float) -> bool:
        """Move one slit to an absolute position in mm.

        `slit` is a slit label ("X+", "X-", "Y+", "Y-"), not a Galil axis
        letter — callers shouldn't need to know the axis mapping.

        Logs to the Command Console and publishes the new target BEFORE
        touching the driver, and does so even when the move is refused: an
        attempt that failed is exactly the thing an operator needs to find in
        the console afterwards.
        """
        axis_letter = self.axis_letter_for(slit)
        if axis_letter is None:
            self.command_failed.emit("motors", f"{slit}: not a valid slit label")
            return False
        if not self.galil.connected:
            self.log_motor(f"! {slit}: move to {mm:+.3f} mm ignored — not connected")
            self.command_failed.emit("motors", f"{slit}: Galil not connected")
            return False

        # Publish the ACHIEVABLE target, not the typed one. A stepper lands on
        # whole counts, so 6.000 mm is commanded as 3653 counts and the slit
        # stops at 6.001 mm. Marking the typed number on the bars would leave
        # every caret sitting a fraction of a step off the position that
        # eventually arrives under it, and would disagree with the Stepper
        # Motors tab, which has always worked in counts.
        counts = SC.mm_to_counts(axis_letter, mm)
        reachable_mm = SC.counts_to_mm(axis_letter, counts)
        self.log_motor(f"> PA {axis_letter}={counts} ({reachable_mm:+.3f} mm) ; "
                       f"BG {axis_letter}   [{slit}]")
        self.note_slit_target(slit, reachable_mm)
        try:
            self.galil.move_absolute(axis_letter, counts)
            return True
        except Exception as e:
            self.log_motor(f"! {slit}: {e}")
            self.command_failed.emit("motors", f"{slit}: {e}")
            return False

    def emergency_stop(self):
        """Abort all motion immediately (Galil AB command)."""
        if not self.galil.connected:
            return
        try:
            self.galil.abort()
        except Exception as e:
            self.command_failed.emit("motors", f"emergency stop: {e}")

    # ---- Full shutdown (MainWindow.closeEvent) ----------------------------------

    def shutdown(self):
        """Full hardware teardown, in the required order, for app close.

        Order matters: stop the LabJack stream before closing its handle
        (disconnect_labjack already guarantees this), abort Galil motion
        before disconnecting it, and NEVER disable the function generators'
        outputs here — they are meant to retain state after the app exits
        (see FuncGenTab.close_session's docstring; this mirrors it).
        """
        try:
            self.disconnect_labjack()
        except Exception:
            pass
        try:
            if self.galil.connected:
                self.galil.abort()
        except Exception:
            pass
        try:
            self.galil.disconnect()
        except Exception:
            pass
        for gen in (self.dg_a, self.dg_b):
            if gen is not None:
                try:
                    gen.close()
                except Exception:
                    pass
