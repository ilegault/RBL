"""
beamline.py
Beamline: the single place live values get converted from raw volts/counts
into physical units and published as typed snapshots.

Today this is fed by the same raw data each tab already receives (LabJack
stream windows, Galil poll snapshots) — device ownership itself moves here in
a later phase. The point of this phase is that the conversion happens ONCE,
here, tested without Qt, instead of once per consuming tab.
"""
from PySide6.QtCore import QObject, Signal

from rbl.config import hardware_config as SC
from rbl.hardware.current_monitor import voltage_to_current
from rbl.hardware.amp_monitor import monitor_to_kv, monitor_to_ma
from rbl.hardware import beam_reconstruction as BR
from rbl.state.snapshots import (
    AxisSnapshot, MotorState, ChannelSnapshot, FuncGenState,
    LogAmpState, AmpChannelSnapshot, AmpState,
)


class Beamline(QObject):
    motors_changed   = Signal(object)   # MotorState
    logamps_changed  = Signal(object)   # LogAmpState
    amps_changed     = Signal(object)   # AmpState
    funcgens_changed = Signal(object)   # FuncGenState
    command_failed   = Signal(str, str)  # subsystem, message

    def __init__(self, parent=None):
        super().__init__(parent)
        # Last-seen jaw edges (SIGNED mm) and log-amp currents (Amps), cached
        # so reconstruct_beam() can be called on demand with whatever spot
        # size the caller currently has selected, without re-deriving them.
        self._jaw_edges_mm: dict[str, float] = {}
        self._log_amp_currents: dict[str, float] = {}

    # ---- Motors ----------------------------------------------------------------

    def ingest_motor_poll(self, snapshot: dict, zeroed: bool):
        """snapshot: axis letter -> {pos, moving, switches, enabled}, exactly
        what GalilPollWorker.state emits."""
        axes = {}
        edges = {}
        for axis_letter, st in snapshot.items():
            jaw = SC.AXIS_NAMES[axis_letter]
            pos_mm = SC.counts_to_mm(axis_letter, st["pos"])
            axes[jaw] = AxisSnapshot(
                pos_counts=st["pos"],
                pos_mm=pos_mm,
                moving=st["moving"],
                enabled=st.get("enabled", True),
                switches=st["switches"],
            )
            # The Galil reports each jaw as a distance from centre with no
            # sign; the '-' jaws live on the negative side of the axis.
            edges[jaw] = abs(pos_mm) if jaw.endswith("+") else -abs(pos_mm)
        self._jaw_edges_mm = edges
        self.motors_changed.emit(MotorState(connected=True, zeroed=zeroed, axes=axes))

    def motors_disconnected(self):
        self._jaw_edges_mm = {}
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
        for ain, jaw in SC.LABJACK_CHANNEL_MAP.items():
            ch = channels.get(ain)
            if ch is None:
                # WAVEFORM profile: log amps not sampled this window.
                currents[jaw] = float("nan")
                continue
            currents[jaw] = voltage_to_current(
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

    def labjack_disconnected(self):
        self._log_amp_currents = {}
        self.logamps_changed.emit(LogAmpState(connected=False))
        self.amps_changed.emit(AmpState(connected=False))

    def reconstruct_beam(self, sigma_mm: float, span_x_mm: float = 0.0,
                          span_y_mm: float = 0.0):
        """Beam position from the last-seen currents + jaw edges.

        The one call site every consumer (the log-amp tab's beam indicator,
        and later the Overview tab) uses, so they read the same currents and
        edges and can never disagree about where the beam is — only the
        assumed spot size stays a per-caller / operator setting.

        Returns None if fewer than all four jaw edges are known yet.
        """
        if len(self._jaw_edges_mm) < 4:
            return None
        return BR.reconstruct(
            self._log_amp_currents, self._jaw_edges_mm, sigma_mm, span_x_mm, span_y_mm
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
