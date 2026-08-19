"""
hv_interlock_link.py
Beamline's live half of the vacuum <-> HV interlock (rbl/hardware/hv_interlock.py):
tracks the latest known chamber pressure and its staleness, recomputes the
permitted-voltage ceiling whenever pressure or a commanded voltage changes,
and republishes it as `hv_interlock_changed` so the chokepoint
(funcgen_control.py) and the Overview tab agree on the same number.

WHY THIS EXISTS
---------------
`hv_interlock.py` is pure policy: given a pressure and a commanded voltage,
say ok/warn/block. Something has to own turning "the last vacuum reading was
12 seconds ago" into "pressure is unknown" (Section 3.3's stale-reading
guard), and that needs a clock and a live cache — state, not math. This
mixin is that state, following the vacuum_link.py/labjack_link.py pattern:
the signal is declared on Beamline, methods here resolve `self` at runtime
because `self` is always a Beamline instance. Do not instantiate this file
on its own.

WHY A TIMER, NOT ONLY A vacuum_changed HANDLER
------------------------------------------------
A gauge going silent is itself the fault condition Section 3.3 asks for
("if the gauge has not reported within N seconds ... treat pressure as
unknown"). If staleness were only checked when a NEW reading arrives, a
gauge that stops reporting entirely would never be caught — there would be
no new event to trigger the check. A periodic timer is what actually
detects "nothing happened."
"""
import logging
import time

from PySide6.QtCore import QTimer

from rbl.config.hv_safety_config import GAUGE_STALE_TIMEOUT_S
from rbl.hardware.funcgen_safety import channel_peak_volts
from rbl.hardware.hv_interlock import interlock_status

log = logging.getLogger(__name__)

_STALE_CHECK_INTERVAL_MS = 1000


def chamber_pressure_torr(vacuum_state) -> float:
    """The single pressure number the interlock checks against: the WORST
    (highest, i.e. least-vacuum) numeric reading currently available across
    both connected gauge controllers.

    Worst-case rather than an average or "the primary gauge": a discharge
    developing anywhere the gauges can see must be able to block HV, and an
    errored or non-numeric channel on one gauge must never silently win by
    omission just because the other gauge still reads a healthy vacuum.

    Returns NaN if no connected gauge currently has a numeric reading.
    """
    readings = []
    if vacuum_state.xgs_connected:
        readings += [r.pressure for r in vacuum_state.xgs_readings
                     if r.pressure is not None]
    if vacuum_state.vgc_connected:
        readings += [r.pressure for r in vacuum_state.vgc_readings
                     if r.pressure is not None]
    return max(readings) if readings else float("nan")


class HvInterlockLinkMixin:
    """Requires the host class to declare: hv_interlock_changed."""

    def _init_hv_interlock(self):
        self._hv_pressure_torr = float("nan")
        self._hv_pressure_at = None   # time.monotonic() of the last numeric reading
        self._hv_interlock_state = "block"
        self._hv_interlock_reason = "no vacuum reading yet"
        self._hv_stale_timer = QTimer(self)
        self._hv_stale_timer.timeout.connect(self._recompute_hv_interlock)
        self._hv_stale_timer.start(_STALE_CHECK_INTERVAL_MS)
        self.vacuum_changed.connect(self.on_vacuum_changed_for_interlock)

    # ---- Reading pressure ------------------------------------------------

    def on_vacuum_changed_for_interlock(self, vacuum_state) -> None:
        pressure = chamber_pressure_torr(vacuum_state)
        if pressure == pressure:   # not NaN
            self._hv_pressure_torr = pressure
            self._hv_pressure_at = time.monotonic()
        self._recompute_hv_interlock()

    def hv_pressure_is_stale(self) -> bool:
        if self._hv_pressure_at is None:
            return True
        return (time.monotonic() - self._hv_pressure_at) > GAUGE_STALE_TIMEOUT_S

    # ---- The chokepoint's per-command check -------------------------------

    def hv_interlock_status_for(self, commanded_kv: float) -> tuple:
        """(status, reason) for one SPECIFIC commanded kV — what
        set_channel()/apply_all_channels() call before sending anything."""
        stale = self.hv_pressure_is_stale()
        return interlock_status(self._hv_pressure_torr, commanded_kv,
                                 pressure_known=not stale)

    # ---- Published, cached state (for the Overview tab) -------------------

    def _recompute_hv_interlock(self) -> None:
        live_kv = self._max_live_commanded_kv()
        stale = self.hv_pressure_is_stale()
        status, reason = interlock_status(self._hv_pressure_torr, live_kv,
                                           pressure_known=not stale)
        transitioned_to_block = status == "block" and self._hv_interlock_state != "block"
        self._hv_interlock_state, self._hv_interlock_reason = status, reason
        self.hv_interlock_changed.emit({
            "state": status, "reason": reason,
            "pressure_torr": self._hv_pressure_torr,
            "pressure_stale": stale,
            "commanded_kv": live_kv,
        })
        if transitioned_to_block and live_kv > 1e-9:
            log.warning(
                "hv_interlock: blocking transition at %.3g kV commanded, "
                "%.3g torr (stale=%s) — ramping every live channel to zero",
                live_kv, self._hv_pressure_torr, stale)
            self._ramp_all_channels_to_zero_on_interlock()

    def _max_live_commanded_kv(self) -> float:
        """Highest |commanded plate kV| across every channel currently
        outputting. `channel_peak_volts()` is numerically the plate kV at
        this rig's 1000x gain (peak volts in equals peak kV at the plate —
        see funcgen_control.py's module docstring)."""
        best = 0.0
        for gen_letter in ("A", "B"):
            gen = self._gen_for(gen_letter)
            if gen is None:
                continue
            for channel in (1, 2):
                try:
                    state = gen.get_state(channel)
                except Exception:
                    continue
                if "error" in state or not state.get("output"):
                    continue
                peak = channel_peak_volts(state.get("shape", "DC"),
                                           state.get("amp", 0.0), state.get("offset", 0.0))
                best = max(best, peak)
        return best

    def _ramp_all_channels_to_zero_on_interlock(self) -> None:
        """Ramp (never hard-cut) every live channel's amplitude AND offset to
        zero. Uses distinct ":amp"/":off" suffixed keys per channel — rather
        than the bare "A1" key set_channel(ramped=True) uses — because a
        channel can have BOTH a nonzero AC amplitude and a nonzero DC offset
        at once, and a single RampEngine label can only run one ramp at a
        time (see ramp_engine.py); this safety path must not silently ramp
        only one of the two."""
        for gen_letter in ("A", "B"):
            gen = self._gen_for(gen_letter)
            if gen is None:
                continue
            for channel in (1, 2):
                key = f"{gen_letter}{channel}"
                try:
                    state = gen.get_state(channel)
                except Exception:
                    continue
                if "error" in state or not state.get("output"):
                    continue
                if abs(state.get("amp", 0.0)) > 1e-9:
                    self._funcgen_ramp_map[f"{key}:amp"] = (gen, channel)
                    self.funcgen_ramp.retarget(f"{key}:amp", 0.0, mode="amplitude")
                if abs(state.get("offset", 0.0)) > 1e-9:
                    self._funcgen_ramp_map[f"{key}:off"] = (gen, channel)
                    self.funcgen_ramp.retarget(f"{key}:off", 0.0, mode="offset")
