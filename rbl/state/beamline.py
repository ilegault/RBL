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
import time

from PySide6.QtCore import QObject, Signal

from rbl.config import hardware_config as SC
from rbl.config.labjack_stream_config import (
    DEFAULT_PROFILE, STREAM_PROFILES, DEFAULT_SINGLE_CHANNEL,
    SINGLE_CHANNEL_CHOICES, is_single_channel,
)
from rbl.hardware.current_monitor import voltage_to_current
from rbl.hardware.amp_monitor import monitor_to_kv, monitor_to_ma
from rbl.hardware import beam_reconstruction as BR
from rbl.hardware.labjack_driver import LabJackT7
from rbl.hardware.labjack_stream_worker import LabJackStreamWorker
from rbl.hardware.galil_driver import GalilController
from rbl.hardware.funcgen_safety import channel_peak_volts, PEAK_MAX_VOLTS
from rbl.state.snapshots import (
    AxisSnapshot, MotorState, ChannelSnapshot, ChannelParams, FuncGenState,
    LogAmpState, AmpChannelSnapshot, AmpState,
)


class Beamline(QObject):
    motors_changed   = Signal(object)   # MotorState
    logamps_changed  = Signal(object)   # LogAmpState
    amps_changed     = Signal(object)   # AmpState
    funcgens_changed = Signal(object)   # FuncGenState
    command_failed   = Signal(str, str)  # subsystem, message

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

        # ── Galil DMC-4103 ───────────────────────────────────────────────────
        self.galil = GalilController()

        # ── DG1022Z function generators ──────────────────────────────────────
        # None until FuncGenTab connects them (each needs a VISA resource
        # string at construction time, unlike Galil/LabJack).
        self.dg_a = None
        self.dg_b = None

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

        amp_channels = {}
        for amp in SC.AMP_LABELS:
            v_ain = SC.AMP_CHANNEL_MAP[amp]["voltage"]
            i_ain = SC.AMP_CHANNEL_MAP[amp]["current"]
            v_ch = channels.get(v_ain)
            i_ch = channels.get(i_ain)

            peak_kv = pkpk_kv = rms_kv = raw_v = float("nan")
            rms_ma = raw_i = float("nan")
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
                rms_ma=rms_ma, raw_v=raw_v, raw_i=raw_i,
            )
        self.amps_changed.emit(
            AmpState(connected=True, channels=amp_channels, active_profile=active_profile)
        )

    def _mark_labjack_disconnected(self):
        self._log_amp_currents = {}
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
        self.funcgens_changed.emit(FuncGenState(
            connected=dict(connected), timebase=dict(timebase), channels=channels,
        ))

    def funcgens_disconnected(self):
        self.funcgens_changed.emit(FuncGenState(
            connected={"A": False, "B": False}, timebase={}, channels={},
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

    def move_slit(self, slit: str, mm: float) -> bool:
        """Move one slit to an absolute position in mm.

        `slit` is a slit label ("X+", "X-", "Y+", "Y-"), not a Galil axis
        letter — callers shouldn't need to know the axis mapping.
        """
        axis_letter = next((a for a, j in SC.AXIS_NAMES.items() if j == slit), None)
        if axis_letter is None:
            self.command_failed.emit("motors", f"{slit}: not a valid slit label")
            return False
        if not self.galil.connected:
            self.command_failed.emit("motors", f"{slit}: Galil not connected")
            return False
        try:
            self.galil.move_absolute(axis_letter, SC.mm_to_counts(axis_letter, mm))
            return True
        except Exception as e:
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
