"""
raster_planner_tab.py
PySide6 widget for the "Raster Planner" outer tab (Phase 3) — the research
deliverable. Computes the amplitude a raster needs for uniform dwell given a
measured beam FWHM, and shows the amplifier's operating envelope so the
operator can see whether the requested operating point is inside it.

Pure calculator: no hardware, no live run. Every number here comes from
rbl.hardware.raster_model / rbl.hardware.load_model, both pure math, so this
tab is just inputs -> those functions -> plots.

WHY THE ENVELOPE USES MEASURED CAPACITANCE WHEN AVAILABLE
------------------------------------------------------------
Section 4.3: "The envelope plot must use the per-channel measured
capacitance from Phase 1 when available, and label which capacitance it
used." An envelope drawn from the wrong C is worse than no envelope — a
1200 pF guess and a channel's real 1050 pF measurement move the current
wall by over 10%, enough to make a planned operating point look safely
inside the envelope when it is not.
"""
import math

import numpy as np
import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
    QSizePolicy,
)

from rbl.config.calibration_config import CAL_LOAD_CAP_PF, CAL_AC_TRIP_MA, CAL_MAX_KV
from rbl.config.hardware_config import AMP_LABELS
from rbl.config.load_calibration_store import capacitance_pf_for
from rbl.config.steerer_geometry import STEERER_GEOMETRIES, DEFAULT_STEERER
from rbl.gui import theme
from rbl.gui.widgets.inputs import NoScrollComboBox, QuietDoubleSpinBox
from rbl.hardware.load_model import envelope_walls
from rbl.hardware.raster_model import (
    required_differential_kv, dwell_uniformity, lissajous_metrics,
)
from rbl.config.calibration_config import ac_peak_current_ma

AMP_MAX_BANDWIDTH_HZ = 10_000.0   # EEL5000 large-signal BW, no load (manual p.1-3)


def _spin(minimum, maximum, value, decimals=3, step=0.1, suffix=""):
    sb = QuietDoubleSpinBox()
    sb.setRange(minimum, maximum)
    sb.setDecimals(decimals)
    sb.setSingleStep(step)
    sb.setValue(value)
    if suffix:
        sb.setSuffix(suffix)
    return sb


class RasterPlannerTab(QWidget):
    """Calculator-only tab: no beamline hardware access, no live stream."""

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        # ── Inputs ────────────────────────────────────────────────────────
        in_box = QGroupBox("Raster Parameters")
        form = QFormLayout(in_box)

        self.cb_steerer = NoScrollComboBox()
        self.cb_steerer.addItems(list(STEERER_GEOMETRIES.keys()))
        self.cb_steerer.setCurrentText(DEFAULT_STEERER)
        form.addRow("Steerer model:", self.cb_steerer)

        self.cb_channel = NoScrollComboBox()
        self.cb_channel.addItems(["(none — use CAL_LOAD_CAP_PF)"] + AMP_LABELS)
        form.addRow("Measured-C channel:", self.cb_channel)

        self.sb_drift_cm = _spin(0.1, 1000.0, 100.0, decimals=1, step=1.0, suffix=" cm")
        form.addRow("Drift distance (steerer exit -> sample):", self.sb_drift_cm)

        self.sb_charge_state = _spin(1, 20, 1, decimals=0, step=1.0)
        form.addRow("Ion charge state (q):", self.sb_charge_state)

        self.sb_beam_energy_kev = _spin(0.1, 10_000.0, 30.0, decimals=2, step=1.0, suffix=" keV")
        form.addRow("Beam energy:", self.sb_beam_energy_kev)

        self.sb_fwhm_mm = _spin(0.001, 100.0, 1.0, decimals=3, step=0.1, suffix=" mm")
        form.addRow("Beam FWHM:", self.sb_fwhm_mm)

        self.sb_sample_half_width_mm = _spin(0.01, 500.0, 5.0, decimals=2, step=0.5, suffix=" mm")
        form.addRow("Sample half-width:", self.sb_sample_half_width_mm)

        self.sb_turnaround_k = _spin(0.0, 10.0, 1.5, decimals=2, step=0.1)
        form.addRow("Turnaround margin (k x FWHM):", self.sb_turnaround_k)

        self.sb_freq_fast_hz = _spin(0.1, 10_000.0, 517.0, decimals=1, step=1.0, suffix=" Hz")
        form.addRow("Fast-axis frequency:", self.sb_freq_fast_hz)

        self.sb_freq_slow_hz = _spin(0.1, 10_000.0, 64.0, decimals=1, step=1.0, suffix=" Hz")
        form.addRow("Slow-axis frequency:", self.sb_freq_slow_hz)

        for sb in (self.sb_drift_cm, self.sb_charge_state, self.sb_beam_energy_kev,
                   self.sb_fwhm_mm, self.sb_sample_half_width_mm, self.sb_turnaround_k,
                   self.sb_freq_fast_hz, self.sb_freq_slow_hz):
            sb.valueChanged.connect(self._recompute)
        self.cb_steerer.currentTextChanged.connect(self._recompute)
        self.cb_channel.currentTextChanged.connect(self._recompute)

        top_row.addWidget(in_box, stretch=0)

        # ── Results ───────────────────────────────────────────────────────
        out_box = QGroupBox("Required Drive")
        out_form = QFormLayout(out_box)

        self.lbl_c_source = QLabel("—")
        out_form.addRow("Capacitance used:", self.lbl_c_source)

        self.lbl_differential_kv = QLabel("—")
        self.lbl_differential_kv.setStyleSheet(f"font-weight: bold; font-size: {theme.FS_BIG}px;")
        out_form.addRow("Required plate-to-plate (differential) kV:", self.lbl_differential_kv)

        self.lbl_plate_kv = QLabel("—")
        out_form.addRow("Required per-plate kV (push-pull, /2):", self.lbl_plate_kv)

        self.lbl_fast_current = QLabel("—")
        out_form.addRow("Predicted peak current, fast axis:", self.lbl_fast_current)

        self.lbl_slow_current = QLabel("—")
        out_form.addRow("Predicted peak current, slow axis:", self.lbl_slow_current)

        self.lbl_envelope = QLabel("—")
        self.lbl_envelope.setWordWrap(True)
        out_form.addRow("Envelope check:", self.lbl_envelope)

        self.lbl_uniformity = QLabel("—")
        out_form.addRow("Dwell uniformity (on sample):", self.lbl_uniformity)

        self.lbl_lissajous = QLabel("—")
        self.lbl_lissajous.setWordWrap(True)
        out_form.addRow("Lissajous:", self.lbl_lissajous)

        top_row.addWidget(out_box, stretch=1)
        layout.addLayout(top_row, stretch=0)

        # ── Plots ─────────────────────────────────────────────────────────
        plot_row = QHBoxLayout()
        plot_row.setSpacing(8)

        env_box = QGroupBox("Operating Envelope")
        env_lay = QVBoxLayout(env_box)
        self._fig_env = Figure(figsize=(5, 4))
        self._canvas_env = FigureCanvasQTAgg(self._fig_env)
        self._canvas_env.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._ax_env = self._fig_env.add_subplot(111)
        env_lay.addWidget(self._canvas_env)
        plot_row.addWidget(env_box, stretch=1)

        dwell_box = QGroupBox("Dwell Uniformity")
        dwell_lay = QVBoxLayout(dwell_box)
        self._fig_dwell = Figure(figsize=(5, 4))
        self._canvas_dwell = FigureCanvasQTAgg(self._fig_dwell)
        self._canvas_dwell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._ax_dwell = self._fig_dwell.add_subplot(111)
        dwell_lay.addWidget(self._canvas_dwell)
        plot_row.addWidget(dwell_box, stretch=1)

        layout.addLayout(plot_row, stretch=1)

        self._recompute()

    # ------------------------------------------------------------------

    def _load_pf(self) -> tuple:
        """(load_pf, source_label) — the capacitance the envelope uses."""
        channel = self.cb_channel.currentText()
        if channel in AMP_LABELS:
            measured = capacitance_pf_for(channel)
            if measured is not None:
                return measured, f"{measured:.1f} pF (measured, {channel})"
        return CAL_LOAD_CAP_PF, f"{CAL_LOAD_CAP_PF:.1f} pF (CAL_LOAD_CAP_PF fallback)"

    def _recompute(self):
        try:
            self._do_recompute()
        except Exception as e:
            self.lbl_envelope.setText(f"Error: {e}")

    def _do_recompute(self):
        model = self.cb_steerer.currentText()
        plate_length_cm, plate_gap_cm, _plate_width, _rating_kv = STEERER_GEOMETRIES[model]
        drift_cm = self.sb_drift_cm.value()
        charge_state = int(self.sb_charge_state.value())
        beam_energy_ev = self.sb_beam_energy_kev.value() * 1000.0
        fwhm_mm = self.sb_fwhm_mm.value()
        sample_half_width_mm = self.sb_sample_half_width_mm.value()
        turnaround_k = self.sb_turnaround_k.value()
        f_fast = self.sb_freq_fast_hz.value()
        f_slow = self.sb_freq_slow_hz.value()

        load_pf, c_source = self._load_pf()
        self.lbl_c_source.setText(c_source)

        # --- Required drive amplitude --------------------------------------
        differential_kv = required_differential_kv(
            target_half_width_mm=sample_half_width_mm, fwhm_mm=fwhm_mm,
            plate_length_cm=plate_length_cm, plate_gap_cm=plate_gap_cm,
            charge_state=charge_state, beam_energy_ev=beam_energy_ev,
            drift_cm=drift_cm, turnaround_k=turnaround_k,
        )
        plate_kv = differential_kv / 2.0   # push-pull: each plate swings +/-V

        self.lbl_differential_kv.setText(
            "—" if math.isnan(differential_kv) else f"{differential_kv:.4f} kV differential")
        self.lbl_plate_kv.setText(
            "—" if math.isnan(plate_kv) else f"{plate_kv:.4f} kV per plate")

        # --- Predicted peak current per axis ---------------------------------
        i_fast_ma = ac_peak_current_ma(f_fast, plate_kv, load_pf=load_pf)
        i_slow_ma = ac_peak_current_ma(f_slow, plate_kv, load_pf=load_pf)
        self.lbl_fast_current.setText(f"{i_fast_ma:.3f} mA at {f_fast:.1f} Hz")
        self.lbl_slow_current.setText(f"{i_slow_ma:.3f} mA at {f_slow:.1f} Hz")

        # --- Envelope --------------------------------------------------------
        walls = envelope_walls(load_pf=load_pf, trip_ma=CAL_AC_TRIP_MA, shape="triangle",
                                max_kv=CAL_MAX_KV, max_f_hz=AMP_MAX_BANDWIDTH_HZ)
        worst_f = max(f_fast, f_slow)
        worst_kv = plate_kv
        if worst_f <= AMP_MAX_BANDWIDTH_HZ:
            envelope_at_f = float(np.interp(worst_f, walls["freq_hz"], walls["envelope_kv"]))
        else:
            envelope_at_f = float("nan")
        in_envelope = (not math.isnan(envelope_at_f)) and worst_kv <= envelope_at_f
        if math.isnan(envelope_at_f):
            self.lbl_envelope.setText(
                f"{worst_f:.1f} Hz exceeds the {AMP_MAX_BANDWIDTH_HZ:.0f} Hz bandwidth wall — out of envelope")
        else:
            verdict = "INSIDE" if in_envelope else "OUTSIDE"
            self.lbl_envelope.setText(
                f"{worst_kv:.3f} kV requested at {worst_f:.1f} Hz vs. {envelope_at_f:.3f} kV "
                f"available — {verdict} the envelope")
        self.lbl_envelope.setStyleSheet(
            theme.status_label(theme.OK if in_envelope else theme.FAULT))

        self._draw_envelope(walls, worst_f, worst_kv)

        # --- Dwell uniformity --------------------------------------------------
        scan_half_width_mm = sample_half_width_mm + turnaround_k * fwhm_mm
        du = dwell_uniformity(fwhm_mm=fwhm_mm, scan_half_width_mm=scan_half_width_mm,
                               sample_half_width_mm=sample_half_width_mm)
        u_pct = du["uniformity_pct"]
        self.lbl_uniformity.setText(
            "—" if math.isnan(u_pct) else f"{u_pct:.3f}% peak-to-peak variation")
        self._draw_dwell(du, sample_half_width_mm)

        # --- Lissajous -----------------------------------------------------
        span_mm = 2 * scan_half_width_mm
        liss = lissajous_metrics(f_fast_hz=f_fast, f_slow_hz=f_slow,
                                  span_fast_mm=span_mm, span_slow_mm=span_mm, fwhm_mm=fwhm_mm)
        fills = "fills uniformly" if liss["fills_uniformly"] else "LEAVES STRIPES — raster under-samples the beam"
        self.lbl_lissajous.setText(
            f"repeat period {liss['repeat_period_s']:.4g} s, "
            f"line spacing {liss['line_spacing_mm']:.4g} mm, "
            f"{liss['lines_per_fwhm']:.2f} lines/FWHM — {fills}")
        self.lbl_lissajous.setStyleSheet(
            theme.status_label(theme.OK if liss["fills_uniformly"] else theme.WARN))

    def _draw_envelope(self, walls, op_f, op_kv):
        ax = self._ax_env
        ax.clear()
        ax.plot(walls["freq_hz"], walls["current_wall_kv"], label="Current wall")
        ax.plot(walls["freq_hz"], walls["voltage_wall_kv"], "--", label="Voltage wall")
        ax.plot(walls["freq_hz"], walls["slew_wall_kv"], ":", label="Slew wall")
        ax.axvline(walls["bandwidth_wall_hz"], color="gray", linestyle="-.", label="Bandwidth wall")
        ax.plot([op_f], [op_kv], "o", color="red", markersize=10, label="Requested point")
        ax.set_xscale("log")
        ax.set_ylim(0, walls["voltage_wall_kv"][0] * 1.2)
        ax.set_xlabel("Frequency (Hz)")
        ax.set_ylabel("Peak plate voltage (kV)")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize="small")
        self._fig_env.tight_layout()
        self._canvas_env.draw_idle()

    def _draw_dwell(self, du, sample_half_width_mm):
        ax = self._ax_dwell
        ax.clear()
        if du["profile_mm"].size:
            ax.plot(du["profile_mm"], du["profile_dose"])
            ax.axvspan(-sample_half_width_mm, sample_half_width_mm, color="tab:green", alpha=0.15,
                       label="Sample")
        ax.set_xlabel("Position (mm)")
        ax.set_ylabel("Relative dose")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize="small")
        self._fig_dwell.tight_layout()
        self._canvas_dwell.draw_idle()
