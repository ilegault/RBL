"""
calibration_tab.py
PySide6 widget for the "HV Calibration" outer tab.

WHAT THIS FEATURE IS AND IS NOT
--------------------------------
This sweeps each deflection channel through a bipolar DC ladder and records
what the EEL5000 VOLTAGE/CURRENT monitors report back via the LabJack T7,
against what was commanded. The voltage monitor is itself a link in the
chain being measured (command -> Rigol output -> amplifier gain -> monitor
divider -> T7 ADC), so this data CANNOT tell "the amplifier under-produces"
apart from "the monitor under-reads" — both look identical in the CSV.

This tab therefore only ever DISPLAYS gain/offset/residual. There is no
"Apply correction" control anywhere on it, and none should ever be added —
see rbl/config/calibration_config.py's module docstring and docs/calibration.md.

Follows amp_tab.py's "stage the selection, commit on Apply" idiom for the
one place it applies here (the load-condition + operator checklist gate
before Run), and its lj_panel embedding for the shared LabJackPanel.
"""
import logging

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QGroupBox, QLabel, QPushButton, QRadioButton, QButtonGroup,
    QLineEdit, QProgressBar, QMessageBox, QDialog, QDialogButtonBox,
    QCheckBox, QSizePolicy,
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from rbl.config import hardware_config as SC
from rbl.config.calibration_config import (
    CAL_MAX_KV, CAL_STEP_KV, CAL_PASSES, CAL_PROFILE, CAL_UNCERTAINTY_V,
    CAL_AC_FREQ_HZ,
    DRIFT_DEFAULT_KV, DRIFT_MAX_ATTENDED_H, DRIFT_MAX_UNATTENDED_H,
    LoadCondition, sweep_points, ac_sweep_points,
)
from rbl.hardware.funcgen_safety import CHANNEL_ROLE
from rbl.gui import theme
from rbl.gui.widgets.connection_bar import LabJackPanel
from rbl.gui.widgets.inputs import NoScrollComboBox, QuietDoubleSpinBox, unit_row
from rbl.services.calibration_runner import CalibrationRunner
from rbl.services.calibration_writer import CalibrationWriter

log = logging.getLogger(__name__)

# The EEL5000 manual's own words on load connections — put verbatim in the
# pre-run checklist so nobody swaps the load mid-session.
_MANUAL_LOAD_WARNING = (
    "The EEL5000 manual is explicit that load connections must be made "
    "with the unit off and unplugged."
)


def _build_funcgen_map(beamline) -> dict:
    """amp_label -> (live DG1022Z instance, channel number).

    Derived from the app's existing funcgen<->amp mapping
    (rbl/hardware/funcgen_safety.py CHANNEL_ROLE) combined with Beamline's
    own generator instances. Not a new mapping — the one that already
    decides which channel drives which plate everywhere else in the app.
    """
    mapping = {}
    for key, amp_label in CHANNEL_ROLE.items():   # "A1" -> "X+"
        gen_letter, channel = key[0], int(key[1])
        gen = beamline.dg_a if gen_letter == "A" else beamline.dg_b
        mapping[amp_label] = (gen, channel)
    return mapping


# ─── Pre-run checklist dialog ─────────────────────────────────────────────────

class _PreRunChecklistDialog(QDialog):
    """Modal, explicit-acknowledgement checklist. The app cannot verify any
    of these in software, so it makes a human assert them instead."""

    def __init__(self, load_condition: LoadCondition, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Calibration pre-run checklist")
        self.setModal(True)
        layout = QVBoxLayout(self)

        intro = QLabel(
            f"About to start a calibration run with load condition: "
            f"<b>{load_condition.value}</b>."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._checks = []
        items = [
            "HIGH VOLTAGE ENABLE shorting cap is installed on all four amplifiers.",
            f"Load condition matches what was selected in the GUI "
            f"({load_condition.value}).",
        ]
        if load_condition is LoadCondition.ON_PLATES:
            items.append("Nobody is at the beamline and the area is clear.")
        for text in items:
            cb = QCheckBox(text)
            cb.setWordWrap(True) if hasattr(cb, "setWordWrap") else None
            cb.toggled.connect(self._refresh_ok_enabled)
            self._checks.append(cb)
            layout.addWidget(cb)

        if load_condition is LoadCondition.DISCONNECTED:
            warn = QLabel(_MANUAL_LOAD_WARNING)
            warn.setWordWrap(True)
            warn.setStyleSheet(
                f"color: {theme.WARN}; font-weight: bold; padding: 6px; "
                "background: #fff6e6; border: 1px solid #e0c080; border-radius: 3px;"
            )
            layout.addWidget(warn)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Start Run")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._refresh_ok_enabled()

    def _refresh_ok_enabled(self, *_):
        ok = all(cb.isChecked() for cb in self._checks)
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)


# ─── Top-level tab widget ─────────────────────────────────────────────────────

class CalibrationTab(QWidget):
    """The 'HV Calibration' outer tab."""

    # Mirrors AmpTab.profile_change_requested exactly — MainWindow connects
    # this straight to beamline.set_stream_profile (see Phase 0 recon: there
    # is no MainWindow._set_stream_profile wrapper; amp_tab wires directly).
    profile_change_requested = Signal(str)

    # True while a run (sweep or drift) is active. MainWindow uses this to
    # grey out AmpTab's stream-profile selector for the duration.
    run_state_changed = Signal(bool)

    def __init__(self, beamline, parent=None):
        super().__init__(parent)
        self.beamline = beamline
        self._runner: CalibrationRunner = None
        self._writer: CalibrationWriter = None
        self._current_profile = None      # tracked via on_profile_changed
        self._prior_profile = None        # stashed at run start, restored after
        self._connected = False

        # Per-amp-label (commanded_kv, measured_kv) pairs for the live scatter
        # / fit display. Reset at the start of every run.
        self._points = {amp: [] for amp in SC.AMP_LABELS}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        left_col = QVBoxLayout()
        left_col.setSpacing(8)

        # ── Connection panel (shared T7, same as AmpTab / CurrentTab) ─────
        self.lj_panel = LabJackPanel()
        left_col.addWidget(self.lj_panel)

        # ── Run configuration ──────────────────────────────────────────────
        cfg_box = QGroupBox("Run Configuration")
        cfg_form = QFormLayout(cfg_box)

        mode_row = QHBoxLayout()
        self.rb_sweep = QRadioButton("DC Sweep")
        self.rb_ac = QRadioButton("AC Sweep")
        self.rb_drift = QRadioButton("Drift")
        self.rb_sweep.setChecked(True)
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self.rb_sweep)
        self._mode_group.addButton(self.rb_ac)
        self._mode_group.addButton(self.rb_drift)
        self.rb_sweep.toggled.connect(self._on_mode_toggled)
        self.rb_ac.toggled.connect(self._on_mode_toggled)
        mode_row.addWidget(self.rb_sweep)
        mode_row.addWidget(self.rb_ac)
        mode_row.addWidget(self.rb_drift)
        mode_row.addStretch()
        cfg_form.addRow("Mode:", mode_row)

        self.cbo_load = NoScrollComboBox()
        self.cbo_load.addItem("— select load condition —", userData=None)
        for cond in LoadCondition:
            self.cbo_load.addItem(cond.value, userData=cond)
        self.cbo_load.currentIndexChanged.connect(self._refresh_run_enabled)
        cfg_form.addRow("Load condition:", self.cbo_load)

        self.le_note = QLineEdit()
        self.le_note.setPlaceholderText("Operator note (recorded in the CSV sidecar)")
        cfg_form.addRow("Note:", self.le_note)

        # Sweep info — display-only, derived from calibration_config.
        n_pts = len(sweep_points("up"))
        n_total = n_pts * len(CAL_PASSES) * len(SC.AMP_LABELS)
        self.lbl_sweep_info = QLabel(
            f"{n_pts} points/pass (±{CAL_MAX_KV:.1f} kV, {CAL_STEP_KV:.2f} kV step, "
            f"ramps from 0) × {len(CAL_PASSES)} passes × {len(SC.AMP_LABELS)} "
            f"channels = {n_total} setpoints"
        )
        self.lbl_sweep_info.setWordWrap(True)
        self.lbl_sweep_info.setStyleSheet(f"color: {theme.NEUTRAL}; font-size: 10px;")
        cfg_form.addRow("DC Sweep:", self.lbl_sweep_info)

        n_ac_pts = len(ac_sweep_points())
        n_ac_total = n_ac_pts * len(SC.AMP_LABELS)
        self.lbl_ac_info = QLabel(
            f"{n_ac_pts} points (0 → {CAL_MAX_KV:.1f} kV peak → 0, "
            f"{CAL_STEP_KV:.2f} kV step) × {len(SC.AMP_LABELS)} channels "
            f"= {n_ac_total} setpoints at {CAL_AC_FREQ_HZ:.0f} Hz"
        )
        self.lbl_ac_info.setWordWrap(True)
        self.lbl_ac_info.setStyleSheet(f"color: {theme.NEUTRAL}; font-size: 10px;")
        cfg_form.addRow("AC Sweep:", self.lbl_ac_info)

        # Drift controls.
        self.spn_drift_kv = QuietDoubleSpinBox()
        self.spn_drift_kv.setRange(-CAL_MAX_KV, CAL_MAX_KV)
        self.spn_drift_kv.setValue(DRIFT_DEFAULT_KV)
        self.spn_drift_kv.setDecimals(3)
        cfg_form.addRow("Drift setpoint:", unit_row(self.spn_drift_kv, "kV"))

        self.spn_drift_h = QuietDoubleSpinBox()
        self.spn_drift_h.setRange(0.0, DRIFT_MAX_UNATTENDED_H)
        self.spn_drift_h.setValue(1.0)
        self.spn_drift_h.setDecimals(2)
        self.spn_drift_h.setToolTip(
            f"Above {DRIFT_MAX_ATTENDED_H:.1f} h is only permitted with the "
            f"amplifier DISCONNECTED from the steerer (unattended cap "
            f"{DRIFT_MAX_UNATTENDED_H:.1f} h)."
        )
        cfg_form.addRow("Drift duration:", unit_row(self.spn_drift_h, "h"))

        left_col.addWidget(cfg_box)

        # ── Uncertainty note ───────────────────────────────────────────────
        unc_lbl = QLabel(
            f"Combined amp + monitor + DAQ uncertainty budget: "
            f"±{CAL_UNCERTAINTY_V:.0f} V (shaded band on the plot). Deviations "
            f"inside this band are noise, not findings. This tool never applies "
            f"a correction — see docs/calibration.md."
        )
        unc_lbl.setWordWrap(True)
        unc_lbl.setStyleSheet(
            "color: #7a2000; font-size: 9pt; padding: 4px; "
            "background: #fff6e6; border: 1px solid #e0c080; border-radius: 3px;"
        )
        left_col.addWidget(unc_lbl)

        # ── Run / Abort ────────────────────────────────────────────────────
        run_row = QHBoxLayout()
        self.btn_run = QPushButton("Run")
        self.btn_run.setMinimumHeight(32)
        self.btn_run.setStyleSheet(
            "QPushButton { background:#1a7000; color:white; font-weight:bold; }"
            "QPushButton:hover { background:#228a00; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        self.btn_run.clicked.connect(self._on_run_clicked)
        self.btn_abort = QPushButton("Abort")
        self.btn_abort.setMinimumHeight(32)
        self.btn_abort.setEnabled(False)
        self.btn_abort.setStyleSheet(
            "QPushButton { background:#8c0000; color:white; font-weight:bold; }"
            "QPushButton:hover { background:#a00000; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        self.btn_abort.clicked.connect(self._on_abort_clicked)
        run_row.addWidget(self.btn_run)
        run_row.addWidget(self.btn_abort)
        left_col.addLayout(run_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        left_col.addWidget(self.progress)

        self.lbl_state = QLabel("Idle")
        self.lbl_state.setStyleSheet(f"color: {theme.NEUTRAL}; font-size: 10px;")
        left_col.addWidget(self.lbl_state)

        left_col.addStretch()
        top_row.addLayout(left_col, stretch=1)

        # ── Live fit readout (display only — never applied) ───────────────
        fit_box = QGroupBox("Live Fit (display only — no correction is ever applied)")
        fit_grid = QGridLayout(fit_box)
        fit_grid.addWidget(QLabel(""), 0, 0)
        for col, hdr in enumerate(("Gain", "Offset (kV)", "Residual RMS (V)"), start=1):
            lbl = QLabel(hdr)
            lbl.setStyleSheet(f"color: {theme.NEUTRAL}; font-size: 10px;")
            fit_grid.addWidget(lbl, 0, col)
        self._fit_labels = {}
        for row, amp in enumerate(SC.AMP_LABELS, start=1):
            hdr = QLabel(amp)
            hdr.setStyleSheet(f"color: {SC.AMP_COLORS[amp]}; font-weight: bold;")
            fit_grid.addWidget(hdr, row, 0)
            labels = []
            for col in range(1, 4):
                lbl = QLabel("—")
                fit_grid.addWidget(lbl, row, col)
                labels.append(lbl)
            self._fit_labels[amp] = labels

        right_col = QVBoxLayout()
        right_col.addWidget(fit_box)

        # ── Scatter plot: commanded vs measured ────────────────────────────
        self.fig = Figure(figsize=(6, 5))
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel("Commanded (kV)")
        self.ax.set_ylabel("Measured (kV)")
        self.ax.grid(True, alpha=0.3)
        self._draw_static_plot_elements()
        self._scatter = {}
        for amp in SC.AMP_LABELS:
            sc = self.ax.scatter([], [], s=10, label=amp, color=SC.AMP_COLORS[amp])
            self._scatter[amp] = sc
        self.ax.legend(loc="upper left", fontsize=8)
        self.fig.tight_layout()
        right_col.addWidget(self.canvas, stretch=1)

        top_row.addLayout(right_col, stretch=1)
        layout.addLayout(top_row, stretch=1)

        self._on_mode_toggled()
        self._refresh_run_enabled()
        self.lj_panel.set_enabled(True)

    # ------------------------------------------------------------------
    # Static plot chrome: ideal y=x line + shaded uncertainty band.
    # ------------------------------------------------------------------

    def _draw_static_plot_elements(self):
        lo, hi = -CAL_MAX_KV * 1.05, CAL_MAX_KV * 1.05
        self.ax.plot([lo, hi], [lo, hi], color="#999", lw=1.0, ls="--", label="_ideal")
        band_kv = CAL_UNCERTAINTY_V / 1000.0
        xs = np.linspace(lo, hi, 2)
        self.ax.fill_between(xs, xs - band_kv, xs + band_kv,
                              color="#1a7000", alpha=0.12, label="_band")
        self.ax.set_xlim(lo, hi)
        self.ax.set_ylim(lo, hi)

    # ------------------------------------------------------------------
    # Mode toggling
    # ------------------------------------------------------------------

    def _on_mode_toggled(self, *_):
        is_drift = self.rb_drift.isChecked()
        self.spn_drift_kv.setEnabled(is_drift)
        self.spn_drift_h.setEnabled(is_drift)

    def _refresh_run_enabled(self, *_):
        has_load = self.cbo_load.currentData() is not None
        self.btn_run.setEnabled(has_load and self._connected and self._runner is None)

    # ------------------------------------------------------------------
    # Run / Abort
    # ------------------------------------------------------------------

    def _on_run_clicked(self):
        load_condition = self.cbo_load.currentData()
        if load_condition is None:
            QMessageBox.warning(self, "Load condition required",
                                 "Select a load condition before running.")
            return
        if self.rb_drift.isChecked() and load_condition is LoadCondition.ON_PLATES \
                and self.spn_drift_h.value() > DRIFT_MAX_ATTENDED_H:
            QMessageBox.warning(
                self, "Drift duration refused",
                f"Runs longer than {DRIFT_MAX_ATTENDED_H:.1f} h are only "
                f"permitted with the amplifier DISCONNECTED from the steerer.",
            )
            return

        dialog = _PreRunChecklistDialog(load_condition, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._points = {amp: [] for amp in SC.AMP_LABELS}
        self._reset_fit_labels()

        funcgen_map = _build_funcgen_map(self.beamline)
        self._writer = CalibrationWriter(metadata={
            "load_condition": load_condition.value,
        })
        self._runner = CalibrationRunner(
            funcgen_map, load_condition, writer=self._writer,
        )
        self._runner.operator_note = self.le_note.text().strip()
        self._runner.progress.connect(self._on_progress)
        self._runner.row_recorded.connect(self._on_row_recorded)
        self._runner.finished.connect(self._on_finished)
        self._runner.error.connect(self._on_runner_error)

        # Force the calibration stream profile; restore whatever was live
        # once the run ends or aborts (see _on_finished).
        self._prior_profile = self._current_profile
        self.profile_change_requested.emit(CAL_PROFILE)

        self.btn_run.setEnabled(False)
        self.btn_abort.setEnabled(True)
        self.run_state_changed.emit(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        if self.rb_sweep.isChecked():
            mode_label = "DC sweep"
        elif self.rb_ac.isChecked():
            mode_label = "AC sweep"
        else:
            mode_label = "drift"
        self.lbl_state.setText(f"Running ({mode_label})…")

        if self.rb_sweep.isChecked():
            self._runner.start_sweep()
        elif self.rb_ac.isChecked():
            self._runner.start_ac_sweep()
        else:
            self._runner.start_drift(self.spn_drift_kv.value(), self.spn_drift_h.value())

    def _on_abort_clicked(self):
        if self._runner is not None:
            self._runner.abort()

    def _on_progress(self, done: int, total: int, label: str):
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        self.lbl_state.setText(label)

    def _on_row_recorded(self, row: dict):
        if row.get("kind") != "voltage":
            return
        amp = row["amp_label"]
        self._points[amp].append((row["commanded_kv"], row["converted_value"]))
        self._update_scatter(amp)
        self._update_fit(amp)

    def _on_runner_error(self, msg: str):
        log.error("calibration run error: %s", msg)

    def _on_finished(self, csv_path: str):
        self.btn_run.setEnabled(True)
        self.btn_abort.setEnabled(False)
        self.run_state_changed.emit(False)
        # Set progress to 100% on completion.
        maximum = max(self.progress.maximum(), 1)
        self.progress.setValue(maximum)
        self.lbl_state.setText(
            f"Finished — {csv_path}" if csv_path else "Stopped"
        )
        self._runner = None
        self._writer = None
        self._refresh_run_enabled()

        # Restore whatever stream profile was live before this run forced
        # CAL_PROFILE.
        if self._prior_profile:
            self.profile_change_requested.emit(self._prior_profile)
        self._prior_profile = None

    # ------------------------------------------------------------------
    # Plot / fit updates
    # ------------------------------------------------------------------

    def _update_scatter(self, amp: str):
        pts = self._points[amp]
        if not pts:
            return
        xs = np.array([p[0] for p in pts])
        ys = np.array([p[1] for p in pts])
        finite = np.isfinite(ys)
        self._scatter[amp].set_offsets(np.column_stack([xs[finite], ys[finite]]))
        self.canvas.draw_idle()

    def _update_fit(self, amp: str):
        pts = [(x, y) for x, y in self._points[amp] if np.isfinite(y)]
        labels = self._fit_labels[amp]
        if len(pts) < 2:
            for lbl in labels:
                lbl.setText("—")
            return
        xs = np.array([p[0] for p in pts])
        ys = np.array([p[1] for p in pts])
        if np.ptp(xs) <= 1e-9:
            return
        gain, offset = np.polyfit(xs, ys, 1)
        residual_v = float(np.sqrt(np.mean((ys - (gain * xs + offset)) ** 2))) * 1000.0
        labels[0].setText(f"{gain:.4f}")
        labels[1].setText(f"{offset:+.4f}")
        labels[2].setText(f"{residual_v:.2f}")

    def _reset_fit_labels(self):
        for labels in self._fit_labels.values():
            for lbl in labels:
                lbl.setText("—")
        for amp in SC.AMP_LABELS:
            self._scatter[amp].set_offsets(np.empty((0, 2)))
        self.canvas.draw_idle()

    # ------------------------------------------------------------------
    # _lj_tabs contract (see rbl/gui/app.py) — same shape as AmpTab/CurrentTab.
    # ------------------------------------------------------------------

    def on_labjack_connected(self, serial: str):
        self._connected = True
        self.lj_panel.set_connected(True, serial)
        self._refresh_run_enabled()

    def on_labjack_disconnected(self):
        self._connected = False
        self.lj_panel.set_connected(False)
        self._refresh_run_enabled()
        if self._runner is not None:
            self._runner.abort()

    def _on_error(self, msg: str):
        QMessageBox.warning(self, "LabJack poll error", msg)

    def on_profile_changed(self, profile_name: str):
        self._current_profile = profile_name

    def on_window(self, payload: dict):
        """Connected to Beamline.raw_window_ready (see rbl/gui/app.py)."""
        if self._runner is not None:
            self._runner.on_window(payload)

    # ------------------------------------------------------------------

    def shutdown(self):
        if self._runner is not None:
            self._runner.abort()
