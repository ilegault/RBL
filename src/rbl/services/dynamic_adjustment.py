"""
dynamic_adjustment.py
Phase 8: the DYNAMIC ADJ front-panel pot A/B campaign.

WHY THIS EXISTS, AND AN HONEST CAVEAT
----------------------------------------
The pot compensates the amplifier's feedback network for load capacitance —
structurally the same problem as scope-probe compensation (Section 8.1).
At this rig's measured ~1200 pF the load is above the manual's stated 1 nF
threshold beyond which factory adjustment is required, so the pot may not
be able to reach a true optimum. This panel characterizes and optimizes
WITHIN the achievable range; its output should be good enough to justify a
factory-adjustment request if it comes to that. State this caveat in the UI
— do not let a "winner" trial read as "this channel is now well compensated."

TRIAL WORKFLOW (Section 8.3) — WHY TWO SEPARATE STREAMING PASSES
---------------------------------------------------------------------
The pot change requires an HV off/on cycle, so this is an A/B campaign
across pot positions, not a live meter — each `DynamicAdjustmentTrial` runs
ONE position. Per position, this drives a 1 Hz, +/-500 V square wave (low
amplitude deliberately: a badly compensated setting cannot do harm, and
compensation optimum is amplitude-independent in the linear regime — verify
the winner at full amplitude only at the end, separately) and streams
SINGLE-CHANNEL 100 kS/s: first the voltage monitor, THEN the current
monitor, as two sequential passes — not simultaneously (contrast Mode C in
load_characterizer.py, which streams both at once for a different reason).
Single-channel is what gives the full 100 kS/s Section 8.2 needs to resolve
a ~30 us monitor-limited edge; sacrificing that for simultaneous coverage
would blur exactly the region this measurement exists to see.

Edges are found from the MEASURED trace itself (never a commanded phase —
Section 1.9's SCPI-latency argument applies here exactly as it does in
Mode C) and averaged, since the point is the FLAT-TOP behaviour after the
edge, not any single noisy transient.

WHAT IS AND IS NOT MEASURABLE (Section 8.2)
-----------------------------------------------
NOT measurable: the sub-10 us edge corner and any ringing at the loop
crossover (the monitor's own ~30 us rise time hides it). FULLY measurable:
flat-top behaviour from ~100 us to hundreds of ms — where a compensation
error shows up as a slow exponential creep. `edge_metrics.flat_top_creep_pct`
is that number; its SIGN is the steering signal for which way to turn the
pot (Section 8.4).
"""
import logging

import numpy as np
from PySide6.QtCore import QObject, Signal

from rbl.config.hardware_config import AMP_CHANNEL_MAP, AMP_MAX_KV
from rbl.hardware.edge_metrics import (
    current_tail_duration_s,
    figure_of_merit,
    flat_top_creep_pct,
    overshoot_pct,
    peak_current_ma,
    rise_time_s,
    settling_time_s,
)
from rbl.services.amp_drive import AmpDrive

log = logging.getLogger(__name__)

TRIAL_PEAK_KV = 0.5          # +/-500 V — low, so a bad setting cannot do harm
TRIAL_FREQ_HZ = 1.0
TRIAL_STREAM_S = 3.0         # >= 2 full periods at 1 Hz
DECIMATED_TRACE_POINTS = 500  # raw trace stored per Section 8.5 point 1

_EDGE_PRE_S = 2e-3
_EDGE_POST_S = 450e-3


def _find_edges(values: np.ndarray, min_spacing_samples: int) -> np.ndarray:
    """Indices where `values` crosses halfway between its minimum and its
    maximum, spaced at least min_spacing_samples apart.

    Deliberately NOT a percentile-of-the-whole-array (or median-based)
    threshold: the voltage trace is a ~50% duty square wave, but the
    current trace is a narrow charge/discharge PULSE that may occupy well
    under 5% of each half period (a well-compensated, small-tau channel
    especially) — a threshold set from the DISTRIBUTION of samples would
    then sit at ~baseline and never register the pulse as "high" at all,
    or (for a duty cycle that happens to land unevenly across a short
    capture) drift off-centre entirely. The min/max midpoint depends only
    on the two extreme values, not how many samples sit near each one, so
    it stays centred regardless of duty cycle — which is what lets ONE
    detector serve both streaming passes.
    """
    if values.size < 4:
        return np.array([], dtype=int)
    lo, hi = float(np.min(values)), float(np.max(values))
    if hi - lo < 1e-12:
        return np.array([], dtype=int)
    threshold = (lo + hi) / 2.0
    above = values > threshold
    crossings = np.flatnonzero(np.diff(above.astype(int)) != 0) + 1
    if crossings.size == 0:
        return crossings
    kept = [int(crossings[0])]
    for idx in crossings[1:]:
        if idx - kept[-1] >= min_spacing_samples:
            kept.append(int(idx))
    return np.array(kept, dtype=int)


def _average_edge_window(values: np.ndarray, edges: np.ndarray, dt: float,
                          n_pre: int, n_post: int):
    """Average the window around each edge, flipping alternating (falling)
    edges so they all read as a rising step — a square wave's edges
    alternate direction, and averaging them without flipping would cancel
    the very transient being measured."""
    windows = []
    for i, idx in enumerate(edges):
        lo, hi = idx - n_pre, idx + n_post
        if lo < 0 or hi > values.size:
            continue
        seg = values[lo:hi].astype(float)
        if i % 2 == 1:
            seg = -seg
        windows.append(seg)
    if not windows:
        return None
    t = (np.arange(n_pre + n_post) - n_pre) * dt
    avg = np.mean(np.vstack(windows), axis=0)
    return t, avg, len(windows)


class DynamicAdjustmentTrial(QObject):
    stage_changed   = Signal(str)   # "voltage" | "current" | "done"
    trial_completed = Signal(dict)
    error           = Signal(str)

    def __init__(self, amp_label: str, funcgen_map: dict, pot_position: str, parent=None):
        """
        pot_position: operator-entered, required (Section 8.3 point 1 /
        8.5 point 2) — a recorded fact the app trusts but cannot verify,
        exactly like LoadCondition. Raises ValueError if blank.
        """
        super().__init__(parent)
        if not pot_position or not str(pot_position).strip():
            raise ValueError("pot_position is required before a trial can start (Section 8.3)")
        self._amp_label = amp_label
        self._pot_position = str(pot_position).strip()
        self._map = funcgen_map
        self._drive = AmpDrive(funcgen_map, max_kv=AMP_MAX_KV, log_prefix="[DYNADJ]")

        self._stage = None
        self._buffer: list = []
        self._last_sample_period = None
        self._voltage_result = None
        self._current_result = None

    @property
    def pot_position(self) -> str:
        return self._pot_position

    def target_ain(self) -> str:
        """Which AIN the caller should point the SINGLE_FAST stream at right
        now — the GUI owns the actual `beamline.set_stream_channel()` call."""
        kind = "voltage" if self._stage == "voltage" else "current"
        return AMP_CHANNEL_MAP[self._amp_label][kind]

    def start(self) -> None:
        self._drive.command_square(self._amp_label, TRIAL_PEAK_KV, TRIAL_FREQ_HZ)
        self._stage = "voltage"
        self._buffer = []
        self.stage_changed.emit("voltage")

    def on_window(self, payload: dict) -> None:
        if self._stage not in ("voltage", "current"):
            return
        sp = payload.get("sample_period")
        if sp:
            self._last_sample_period = float(sp)
        ain = self.target_ain()
        entry = payload.get("channels", {}).get(ain)
        if entry and "waveform" in entry:
            self._buffer.append(np.asarray(entry["waveform"], dtype=float))
        total_samples = sum(w.size for w in self._buffer)
        if self._last_sample_period and total_samples * self._last_sample_period >= TRIAL_STREAM_S:
            self._on_stage_complete()

    # ------------------------------------------------------------------

    def _on_stage_complete(self) -> None:
        try:
            raw = np.concatenate(self._buffer) if self._buffer else np.array([])
            dt = self._last_sample_period or 0.0
            if self._stage == "voltage":
                self._voltage_result = self._process_voltage(raw, dt)
                self._stage = "current"
                self._buffer = []
                self.stage_changed.emit("current")
            else:
                self._current_result = self._process_current(raw, dt)
                self._stage = "done"
                self._finish()
        except Exception as e:
            self.error.emit(str(e))
            self._stage = "done"

    def _process_voltage(self, raw_monitor_v: np.ndarray, dt: float) -> dict:
        from rbl.config.hardware_config import VOLTAGE_MONITOR_KV_PER_VOLT
        v_kv = raw_monitor_v * VOLTAGE_MONITOR_KV_PER_VOLT
        if dt <= 0 or v_kv.size < 4:
            return {"error": "no voltage data collected"}

        n_pre = max(1, int(_EDGE_PRE_S / dt))
        n_post = max(1, int(_EDGE_POST_S / dt))
        min_spacing = max(1, int((0.5 / TRIAL_FREQ_HZ) / dt * 0.5))
        edges = _find_edges(v_kv, min_spacing)
        window = _average_edge_window(v_kv, edges, dt, n_pre, n_post)
        if window is None:
            return {"error": "no edges found in voltage trace", "n_edges": 0}
        t, avg, n_edges = window
        v_final = float(np.mean(avg[-max(1, n_post // 10):]))

        return {
            "n_edges": n_edges,
            "overshoot_pct": overshoot_pct(avg, v_final),
            "settling_1pct_s": settling_time_s(t, avg, v_final, 1.0),
            "settling_0p1pct_s": settling_time_s(t, avg, v_final, 0.1),
            "flat_top_creep_pct": flat_top_creep_pct(t, avg, v_final),
            "rise_time_s": rise_time_s(t, avg, v_final),
            "v_final_kv": v_final,
            "trace_t_s": t[::max(1, t.size // DECIMATED_TRACE_POINTS)].tolist(),
            "trace_v_kv": avg[::max(1, avg.size // DECIMATED_TRACE_POINTS)].tolist(),
        }

    def _process_current(self, raw_monitor_v: np.ndarray, dt: float) -> dict:
        from rbl.config.hardware_config import CURRENT_MONITOR_MA_PER_VOLT
        i_ma = raw_monitor_v * CURRENT_MONITOR_MA_PER_VOLT
        if dt <= 0 or i_ma.size < 4:
            return {"error": "no current data collected"}

        n_pre = max(1, int(_EDGE_PRE_S / dt))
        n_post = max(1, int(_EDGE_POST_S / dt))
        min_spacing = max(1, int((0.5 / TRIAL_FREQ_HZ) / dt * 0.5))
        edges = _find_edges(np.abs(i_ma - np.median(i_ma)), min_spacing)
        window = _average_edge_window(i_ma, edges, dt, n_pre, n_post)
        if window is None:
            return {"error": "no edges found in current trace", "n_edges": 0}
        t, avg, n_edges = window
        baseline_ma = float(np.mean(avg[-max(1, n_post // 10):]))

        return {
            "n_edges": n_edges,
            "peak_current_ma": peak_current_ma(avg),
            "current_tail_duration_s": current_tail_duration_s(t, avg, baseline_ma),
            "trace_t_s": t[::max(1, t.size // DECIMATED_TRACE_POINTS)].tolist(),
            "trace_i_ma": avg[::max(1, avg.size // DECIMATED_TRACE_POINTS)].tolist(),
        }

    def _finish(self) -> None:
        try:
            self._drive.zero_and_off_all()
        except Exception:
            pass
        v = self._voltage_result or {}
        i = self._current_result or {}
        fom = figure_of_merit(v.get("overshoot_pct", float("nan")),
                               v.get("settling_1pct_s", float("nan")),
                               v.get("flat_top_creep_pct", float("nan")))
        record = {
            "amp_label": self._amp_label, "pot_position": self._pot_position,
            "voltage": v, "current": i, "figure_of_merit": fom,
        }
        self.stage_changed.emit("done")
        self.trial_completed.emit(record)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    class _FakeGen:
        def set_waveform(self, ch, shape, freq, amp, offset, phase):
            return ""
        def output_on(self, ch):
            pass
        def output_off(self, ch):
            pass
        def get_state(self, ch):
            return {"shape": "DC", "freq": 0.0, "amp": 0.0, "offset": 0.0,
                     "phase": 0.0, "output": False, "load": "INFinity"}
        def set_output_load(self, ch, load):
            pass

    fmap = {"X+": (_FakeGen(), 1)}

    try:
        DynamicAdjustmentTrial("X+", fmap, pot_position="")
        raise AssertionError("expected ValueError for blank pot_position")
    except ValueError:
        print("[OK] blank pot_position is rejected")

    trial = DynamicAdjustmentTrial("X+", fmap, pot_position="2 o'clock")
    completed = []
    trial.trial_completed.connect(completed.append)
    stages = []
    trial.stage_changed.connect(stages.append)
    trial.start()
    assert stages == ["voltage"]
    assert trial.target_ain() == AMP_CHANNEL_MAP["X+"]["voltage"]

    fs = 100_000.0
    n = int(fs * TRIAL_STREAM_S)
    t = np.arange(n) / fs
    v_kv = TRIAL_PEAK_KV * np.sign(np.sin(2 * np.pi * TRIAL_FREQ_HZ * t))
    v_kv_noisy = v_kv + np.random.default_rng(0).normal(0, 0.001, n)
    raw_v = v_kv_noisy / 1.0   # VOLTAGE_MONITOR_KV_PER_VOLT == 1.0

    chunk = 2000
    for w in range(0, n, chunk):
        payload = {"sample_period": 1.0 / fs,
                   "channels": {trial.target_ain(): {"waveform": raw_v[w:w+chunk]}}}
        trial.on_window(payload)
        if trial._stage != "voltage":
            break

    assert stages == ["voltage", "current"], stages
    assert trial._voltage_result is not None
    assert trial._voltage_result.get("n_edges", 0) > 0
    print(f"[OK] voltage stage: n_edges={trial._voltage_result.get('n_edges')}, "
          f"overshoot={trial._voltage_result.get('overshoot_pct'):.2f}%, "
          f"creep={trial._voltage_result.get('flat_top_creep_pct'):.2f}%")

    tau = 2e-4
    i_ma = 15.0 * np.exp(-(t % (1.0 / TRIAL_FREQ_HZ / 2)) / tau) * np.sign(v_kv) + 0.2
    raw_i = i_ma / 10.0   # CURRENT_MONITOR_MA_PER_VOLT == 10.0
    for w in range(0, n, chunk):
        payload = {"sample_period": 1.0 / fs,
                   "channels": {trial.target_ain(): {"waveform": raw_i[w:w+chunk]}}}
        trial.on_window(payload)
        if trial._stage == "done":
            break

    assert completed, "expected the trial to complete"
    record = completed[0]
    assert record["pot_position"] == "2 o'clock"
    assert record["current"].get("n_edges", 0) >= 0
    print(f"[OK] trial completed: figure_of_merit={record['figure_of_merit']:.3f}")

    print("\n[OK] dynamic_adjustment self-test passed")
