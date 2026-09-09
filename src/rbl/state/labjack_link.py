"""
labjack_link.py
Beamline's LabJack T7 half: the one handle, the one stream worker, the
stop/reconfigure/start cycle a profile change needs, and the conversion of
one raw stream window into a LogAmpState + an AmpState.

Split out of beamline.py, which had grown to hold three instruments'
lifecycles and command surfaces in one file. Each half is now its own module
and the class is assembled from them.

WHY A MIXIN AND NOT A SEPARATE OBJECT
-------------------------------------
`Beamline` is the published surface: every tab, every test, and every signal
connection in the app names it directly (`beamline.connect_labjack(...)`,
`beamline.amps_changed.connect(...)`). Splitting into collaborating objects
would rename all of that to `beamline.labjack.connect_labjack(...)` — dozens
of call sites edited to move code that did not otherwise change, or else a
wall of delegating properties, which is more code than it saves.

Mixing in keeps ONE class with ONE public API and moves only where the source
lives. The signals stay declared on `Beamline` because Qt requires them in a
QObject subclass body, and because they are the class's interface rather than
this half's — the methods here emit `self.logamps_changed` and friends, which
resolve at runtime for the same reason `self.lj` does: `self` is a Beamline.
The one rule is that this file must not be instantiated on its own.
"""
import time

import numpy as np

from rbl.config import hardware_config as SC
from rbl.config.labjack_stream_config import (
    DEFAULT_AMP_PAIR,
    DEFAULT_PROFILE,
    DEFAULT_SINGLE_CHANNEL,
    SINGLE_CHANNEL_CHOICES,
    STREAM_PROFILES,
    is_pair_channel,
    is_single_channel,
    pair_choices,
)
from rbl.hardware.amp_monitor import monitor_to_kv, monitor_to_ma
from rbl.hardware.amp_trace import AmpTraceBuilder
from rbl.hardware.current_monitor import voltage_to_current
from rbl.hardware.labjack_driver import LabJackT7
from rbl.hardware.labjack_stream_worker import LabJackStreamWorker
from rbl.state.snapshots import AmpChannelSnapshot, AmpState, LogAmpState


class LabJackLinkMixin:
    """LabJack T7 ownership, streaming, and volts -> physical-units ingestion.

    Mixed into Beamline; see the module docstring. Requires the host class to
    declare: logamps_changed, amps_changed, labjack_connected,
    labjack_disconnected_evt, stream_error, profile_changed.
    """

    def _init_labjack(self):
        """Called from Beamline.__init__ — not a cooperative __init__.

        Each half initialises its own state through an explicit call so the
        order is readable in one place and no mixin has to participate in a
        super().__init__ chain alongside QObject.
        """
        # ONE physical T7 -> ONE LabJackT7 instance -> ONE stream worker
        # reading all channels at high rate. Both the Beam Current tab
        # (AIN0-3, log amps) and the HV Amplifier tab (AIN6-13, EEL5000
        # monitors) render from the snapshots this produces. Never let a tab
        # open its own handle.
        self.lj                = LabJackT7()
        self._lj_worker        = None
        self.active_profile    = DEFAULT_PROFILE
        # Target channel for single-channel profiles (SINGLE_FAST/SINGLE_HIRES).
        # Ignored while a multi-channel profile is active.
        self.active_channel    = DEFAULT_SINGLE_CHANNEL
        # Target AMP LABEL for pair profiles (AMP_PAIR).  Kept separate from
        # active_channel rather than overloading it: they are different kinds
        # of thing ("AIN7" vs "Y+"), and a sweep switching pairs must not
        # disturb whatever single-channel target the amp tab last selected.
        self.active_pair       = DEFAULT_AMP_PAIR
        self._profile_updating = False   # re-entrancy guard for set_profile / set_channel
        # Shared monotonic epoch for every stream worker this connection spawns.
        # Set on connect so payload timestamps stay continuous across the
        # stop/reconfigure/start cycles that profile and channel switches need.
        self._stream_t0        = None
        # Recent raw HV VOLTAGE monitor windows, kept so the Overview's pair
        # trace can still show a whole cycle when the drive is slower than one
        # stream window.
        self.amp_traces        = AmpTraceBuilder()
        # Last-seen log-amp currents (Amps), cached so reconstruct_beam() can
        # be called on demand with whatever spot size the caller currently has
        # selected, without re-deriving them.
        self._log_amp_currents: dict[str, float] = {}

    # ---- Ingestion: one window -> LogAmpState + AmpState ----------------------

    def ingest_labjack_window(self, payload: dict, active_profile: str = ""):
        """One stream window from LabJackStreamWorker -> LogAmpState + AmpState.

        This is the ONLY place a LabJack volt becomes an amp, a kilovolt or a
        milliamp. Both subsystems live on the same physical LabJack and arrive
        in the same payload, so one ingestion call keeps them from disagreeing
        about "now" the way two independently-timed paths could — and keeps
        every screen showing a number that was converted once, the same way.
        """
        channels = payload["channels"]
        t = payload.get("t", float("nan"))

        currents = {}
        volts = {}
        for ain, slit in SC.LABJACK_CHANNEL_MAP.items():
            ch = channels.get(ain)
            if ch is None:
                # WAVEFORM profile: log amps not sampled this window. Absent
                # from `volts` is how a consumer tells this apart from a
                # channel that WAS sampled and read out of range — both give a
                # NaN current, only one is a fault.
                currents[slit] = float("nan")
                continue
            volts[slit] = ch["mean"]
            currents[slit] = voltage_to_current(
                ch["mean"], SC.LOG_AMP_V_AT_1NA, SC.LOG_AMP_V_AT_1MA
            )
        self._log_amp_currents = currents
        self.logamps_changed.emit(LogAmpState(
            connected=True, currents=dict(currents), volts=dict(volts), t=t,
        ))

        self.amp_traces.push(channels)
        traces = self.amp_traces.traces(payload)

        amp_channels = {}
        for amp in SC.AMP_LABELS:
            v_ain = SC.AMP_CHANNEL_MAP[amp]["voltage"]
            i_ain = SC.AMP_CHANNEL_MAP[amp]["current"]
            v_ch = channels.get(v_ain)
            i_ch = channels.get(i_ain)

            peak_kv = pkpk_kv = rms_kv = raw_v = dc_kv = float("nan")
            rms_ma = raw_i = dc_ma = float("nan")
            window_kv = window_ma = None
            wave_kv, span_s, freq_hz = traces.get(
                amp, ((), float("nan"), float("nan")))
            if v_ch is not None:
                peak_kv = monitor_to_kv(v_ch["peak"])
                pkpk_kv = v_ch["pk_pk"] * SC.VOLTAGE_MONITOR_KV_PER_VOLT
                rms_kv  = monitor_to_kv(v_ch["rms"])
                wave = v_ch.get("waveform")
                if wave is not None and len(wave):
                    # The window average is the DC level the input sits at —
                    # what a meter on the BNC would read — and is scaled once
                    # here so no consumer has to know the monitor ratio.
                    raw_v = float(np.mean(wave))
                    window_kv = np.asarray(wave) * SC.VOLTAGE_MONITOR_KV_PER_VOLT
                else:
                    raw_v = v_ch["rms"]
                dc_kv = monitor_to_kv(raw_v)
            if i_ch is not None:
                rms_ma = monitor_to_ma(i_ch["rms"])
                wave = i_ch.get("waveform")
                if wave is not None and len(wave):
                    raw_i = float(np.mean(wave))
                    window_ma = np.asarray(wave) * SC.CURRENT_MONITOR_MA_PER_VOLT
                else:
                    raw_i = i_ch["rms"]
                dc_ma = monitor_to_ma(raw_i)

            amp_channels[amp] = AmpChannelSnapshot(
                peak_kv=peak_kv, pkpk_kv=pkpk_kv, rms_kv=rms_kv,
                rms_ma=rms_ma, raw_v=raw_v, raw_i=raw_i, wave_kv=wave_kv,
                wave_span_s=span_s, wave_freq_hz=freq_hz,
                v_live=v_ch is not None, i_live=i_ch is not None,
                window_kv=window_kv, window_ma=window_ma,
                dc_kv=dc_kv, dc_ma=dc_ma,
            )
        self.amps_changed.emit(AmpState(
            connected=True, channels=amp_channels, active_profile=active_profile,
            t=t, sample_period=payload.get("sample_period"),
        ))

    def _mark_labjack_disconnected(self):
        self._log_amp_currents = {}
        self.amp_traces.clear()
        self.logamps_changed.emit(LogAmpState(connected=False))
        self.amps_changed.emit(AmpState(connected=False))

    # ---- Connection lifecycle -------------------------------------------------

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

        The override argument is profile-kind dependent: an AIN name for
        single-channel profiles, an amp label for pair profiles, None for
        multi-channel profiles whose scan list is fixed. Caller is
        responsible for stopping any existing worker first.
        """
        if is_single_channel(profile_name):
            override = self.active_channel
        elif is_pair_channel(profile_name):
            override = self.active_pair
        else:
            override = None
        worker = LabJackStreamWorker(
            self.lj.handle, profile_name, override, t0=self._stream_t0
        )
        worker.window_ready.connect(self._on_stream_window)
        worker.error.connect(self._on_stream_error)
        worker.start()
        self._lj_worker = worker

    def _on_stream_window(self, payload: dict):
        self.raw_window_ready.emit(payload)
        self.ingest_labjack_window(payload, active_profile=self.active_profile)

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

    def set_stream_pair(self, amp_label: str):
        """Change which amplifier's (current, voltage) pair AMP_PAIR streams.

        The pair-profile analogue of set_stream_channel. Same hardware
        constraint applies: the T7 cannot change its scan list mid-stream, so
        this is a full stop -> reconfigure -> start cycle costing tens of ms.

        A per-channel sweep calls this once per driven amplifier, between
        setpoints -- never mid-capture. The runner is responsible for waiting
        out the restart before it starts collecting again, exactly as it
        already does for a profile switch.
        """
        if self._profile_updating:
            return
        if amp_label not in pair_choices("AMP_PAIR"):
            return   # not a valid pair target

        # Remember the target regardless of the active profile so a later
        # switch to a pair profile starts on the amplifier picked.
        changed = (amp_label != self.active_pair)
        self.active_pair = amp_label
        if not changed:
            return
        if not self.lj.connected or self._lj_worker is None:
            return   # remembered; applied when a pair profile starts
        if not is_pair_channel(self.active_profile):
            return   # remembered; the live scan list is fixed in other modes

        self._profile_updating = True
        try:
            self._restart_stream_worker(self.active_profile)
        finally:
            self._profile_updating = False

    def set_stream_pair_profile(self, profile_name: str, amp_label: str):
        """Switch to a pair profile AND its target amplifier in ONE restart.

        Exists because doing it in two steps was unreliable in exactly the
        situations a calibration run meets in practice:

          * set_stream_profile returns early when the requested profile is
            already active, so starting a run while the user had already
            selected AMP_PAIR by hand did nothing at all — and, because the
            early return also skips the profile_changed emit, no tab was told
            to resync its UI.
          * When the profile DID change, the restart used whatever active_pair
            happened to hold, and the pair retarget that followed caused a
            SECOND stop/reconfigure/start. Two restarts back to back means two
            settling transients and two STREAM_SETTLE_DISCARD_S gaps
            immediately before the first setpoint.
          * Neither step re-armed anything when the profile was already
            AMP_PAIR on a different pair, so which one you ended up on
            depended on the order the two early-return guards fired in.

        This sets both fields first, then restarts UNCONDITIONALLY — no
        "changed" short-circuit. A caller asking for a specific stream
        configuration gets exactly that configuration, from any prior state,
        including from the same profile on a different pair. Restarting when
        it was already correct costs one stream cycle and is the cheaper
        mistake by far.

        profile_changed is emitted at the end whether or not the profile name
        actually changed, so every tab resyncs its controls to the truth.
        """
        if profile_name not in STREAM_PROFILES:
            return
        if amp_label and amp_label in pair_choices(profile_name):
            self.active_pair = amp_label
        self.active_profile = profile_name

        if not self.lj.connected or self._lj_worker is None:
            # Remembered; applied when the stream next starts. Still emit, so
            # the UI reflects the request rather than the stale value.
            self.profile_changed.emit(profile_name)
            return
        if self._profile_updating:
            return   # a switch is already in flight; it will use the new fields

        self._profile_updating = True
        try:
            self._restart_stream_worker(profile_name)
        finally:
            self._profile_updating = False
        self.profile_changed.emit(profile_name)

    def _restart_stream_worker(self, profile_name: str):
        """Stop the running worker (if any) and start a fresh one.

        Hardware constraint: the T7 scan list cannot be changed mid-stream, so
        profile switches, single-channel target changes and pair target
        changes all go through this stop -> reconfigure -> start cycle.
        Callers hold _profile_updating.
        """
        if self._lj_worker is not None:
            self._lj_worker.stop()
            self._lj_worker.wait(5000)
            self._lj_worker = None
        # Samples either side of this restart were taken at different rates
        # (and, for a single-channel switch, on different plates). Stitching
        # across the seam would measure a period that never existed.
        self.amp_traces.clear()
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
