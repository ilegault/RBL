"""
Raster Scan Analysis Tool — Native Desktop GUI
Run: python app.py   (from inside the raster_tool/ directory)

PyQt5 + embedded matplotlib figures. No browser, no server.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter,
    QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QDoubleSpinBox, QSpinBox,
    QComboBox, QTabWidget, QScrollArea,
    QTableWidget, QTableWidgetItem, QGroupBox,
    QProgressBar, QSizePolicy, QMessageBox,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QColor

import matplotlib
matplotlib.use("Qt5Agg")
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg, NavigationToolbar2QT
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

from defaults import DEFAULTS
from patterns import get_pattern
from dose import compute_dose
from metrics import compute_all_metrics, _aperture_mask_from_edges
from viz import plot_heatmap, plot_dose_3d, plot_velocity_profile, plot_dwell_hist


# ─── Worker threads ───────────────────────────────────────────────────────────

class ComputeWorker(QThread):
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)

    def __init__(self, params):
        super().__init__()
        self.params = params

    def run(self):
        try:
            p = self.params
            t, x, y = get_pattern(p["pattern"], p)
            dose, rho, xe, ye = compute_dose(p, t, x, y)
            dt = t[1] - t[0] if len(t) > 1 else 1.0
            m  = compute_all_metrics(dose, rho, x, y, dt, xe, ye, p)
            self.finished.emit((dose, rho, xe, ye, t, x, y, m))
        except Exception as e:
            self.error.emit(str(e))


class OptimizerWorker(QThread):
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)

    def __init__(self, mode, params, n_grid=10):
        super().__init__()
        self.mode   = mode    # "grid" | "optimize"
        self.params = params
        self.n_grid = n_grid

    def run(self):
        try:
            if self.mode == "grid":
                from optimizer import grid_search
                fx_vals, fy_vals, J_grid = grid_search(
                    self.params, self.n_grid, self.n_grid
                )
                self.finished.emit(("grid", fx_vals, fy_vals, J_grid))
            else:
                from optimizer import run_optimizer
                bounds = [(500, 10000), (1, 500), (1.0, 1.5), (1.0, 1.5)]
                result = run_optimizer(bounds, self.params)
                self.finished.emit(("optimize", result))
        except Exception as e:
            self.error.emit(str(e))


# ─── Reusable matplotlib tab ──────────────────────────────────────────────────

class PlotTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self.canvas  = None
        self.toolbar = None

    def set_figure(self, fig):
        import matplotlib.pyplot as plt
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.canvas  = FigureCanvasQTAgg(fig)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self._layout.addWidget(self.toolbar)
        self._layout.addWidget(self.canvas)
        self.canvas.draw()
        plt.close(fig)


# ─── Spin-box helpers ─────────────────────────────────────────────────────────

def _dbl(min_val, max_val, val, step=0.1, decimals=2):
    sb = QDoubleSpinBox()
    sb.setRange(min_val, max_val)
    sb.setValue(val)
    sb.setSingleStep(step)
    sb.setDecimals(decimals)
    sb.setMinimumWidth(90)
    return sb

def _int(min_val, max_val, val, step=1):
    sb = QSpinBox()
    sb.setRange(min_val, max_val)
    sb.setValue(val)
    sb.setSingleStep(step)
    sb.setMinimumWidth(90)
    return sb


# ─── Parameter panel ──────────────────────────────────────────────────────────

class ParamPanel(QScrollArea):
    """Left-side parameter controls. Caller reads values via get_params()."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setMinimumWidth(280)
        self.setMaximumWidth(340)

        container = QWidget()
        self.setWidget(container)
        root = QVBoxLayout(container)
        root.setSpacing(8)

        def group(title):
            gb = QGroupBox(title)
            fl = QFormLayout(gb)
            fl.setSpacing(4)
            root.addWidget(gb)
            return fl

        # ── Beam ──────────────────────────────────────────────────────────────
        f = group("Beam")
        self.fwhm_x = _dbl(0.1, 20.0, DEFAULTS["fwhm_x_mm"], 0.1)
        self.fwhm_y = _dbl(0.1, 20.0, DEFAULTS["fwhm_y_mm"], 0.1)
        f.addRow("FWHM X (mm)", self.fwhm_x)
        f.addRow("FWHM Y (mm)", self.fwhm_y)

        # ── Aperture ──────────────────────────────────────────────────────────
        f = group("Aperture (mm)")
        self.xL = _dbl(-50.0, 0.0,  DEFAULTS["aperture_xL_mm"], 0.5)
        self.xR = _dbl(0.0,  50.0,  DEFAULTS["aperture_xR_mm"], 0.5)
        self.yB = _dbl(-50.0, 0.0,  DEFAULTS["aperture_yB_mm"], 0.5)
        self.yT = _dbl(0.0,  50.0,  DEFAULTS["aperture_yT_mm"], 0.5)
        f.addRow("xL", self.xL)
        f.addRow("xR", self.xR)
        f.addRow("yB", self.yB)
        f.addRow("yT", self.yT)

        # ── Scan amplitudes ───────────────────────────────────────────────────
        f = group("Scan Amplitudes (mm)")
        self.ax = _dbl(0.0, 200.0, DEFAULTS["ax_mm"], 0.5)
        self.ay = _dbl(0.0, 200.0, DEFAULTS["ay_mm"], 0.5)
        f.addRow("X amplitude", self.ax)
        f.addRow("Y amplitude", self.ay)

        # ── Frequencies ───────────────────────────────────────────────────────
        f = group("Frequencies (both axes free)")
        note = QLabel("No fast/slow restriction.\nEither axis can be higher.")
        note.setStyleSheet("color: #888; font-size: 10px;")
        note.setWordWrap(True)
        f.addRow(note)
        self.fx = _dbl(0.5, 50000.0, DEFAULTS["fx_hz"], 10.0, decimals=1)
        self.fy = _dbl(0.5, 50000.0, DEFAULTS["fy_hz"], 10.0, decimals=1)
        f.addRow("f₁ (Hz)", self.fx)
        f.addRow("f₂ (Hz)", self.fy)

        # ── Pattern ───────────────────────────────────────────────────────────
        f = group("Pattern")
        self.pattern = QComboBox()
        self.pattern.addItems(
            ["classic", "alt_axes", "lissajous", "spiral", "sinusoidal", "wobble"]
        )
        self.phase = _dbl(0.0, 360.0, DEFAULTS["lissajous_phase_deg"], 1.0, decimals=1)
        f.addRow("Pattern", self.pattern)
        f.addRow("Lissajous phase (°)", self.phase)

        # ── Simulation ────────────────────────────────────────────────────────
        f = group("Simulation")
        self.T_ms     = _dbl(1.0, 5000.0, DEFAULTS["T_total_ms"],     10.0, decimals=1)
        self.n_samples = _int(1000, 1000000, DEFAULTS["n_time_samples"], 5000)
        self.grid_nx   = _int(32,   1024,    DEFAULTS["grid_nx"],        32)
        self.grid_ny   = _int(32,   1024,    DEFAULTS["grid_ny"],        32)
        f.addRow("T_total (ms)", self.T_ms)
        f.addRow("Time samples", self.n_samples)
        f.addRow("Grid Nx", self.grid_nx)
        f.addRow("Grid Ny", self.grid_ny)

        # ── Physics ───────────────────────────────────────────────────────────
        f = group("Physics / FDRT")
        self.tau_recomb  = _dbl(0.01, 100.0,   DEFAULTS["tau_recomb_ms"],    0.1)
        self.fdrt_thresh = _dbl(10.0, 50000.0, DEFAULTS["fdrt_threshold_hz"], 50.0, decimals=0)
        f.addRow("τ_recomb (ms)",       self.tau_recomb)
        f.addRow("FDRT threshold (Hz)", self.fdrt_thresh)

        # ── Optimizer weights ─────────────────────────────────────────────────
        f = group("Optimizer Weights")
        self.w1 = _dbl(0.0, 5.0, DEFAULTS["w1"], 0.1)
        self.w2 = _dbl(0.0, 5.0, DEFAULTS["w2"], 0.1)
        self.w3 = _dbl(0.0, 5.0, DEFAULTS["w3"], 0.1)
        self.w4 = _dbl(0.0, 5.0, DEFAULTS["w4"], 0.1)
        self.w5 = _dbl(0.0, 5.0, DEFAULTS["w5"], 0.1)
        f.addRow("w1 (flatness)",    self.w1)
        f.addRow("w2 (pinch)",       self.w2)
        f.addRow("w3 (FDRT penalty)", self.w3)
        f.addRow("w4 (BW penalty)",  self.w4)
        f.addRow("w5 (dose conserv.)", self.w5)

        root.addStretch()

    def get_params(self) -> dict:
        return {
            "fwhm_x_mm":          self.fwhm_x.value(),
            "fwhm_y_mm":          self.fwhm_y.value(),
            "aperture_xL_mm":     self.xL.value(),
            "aperture_xR_mm":     self.xR.value(),
            "aperture_yB_mm":     self.yB.value(),
            "aperture_yT_mm":     self.yT.value(),
            "ax_mm":              self.ax.value(),
            "ay_mm":              self.ay.value(),
            "fx_hz":              self.fx.value(),
            "fy_hz":              self.fy.value(),
            "pattern":            self.pattern.currentText(),
            "lissajous_phase_deg": self.phase.value(),
            "T_total_ms":         self.T_ms.value(),
            "n_time_samples":     self.n_samples.value(),
            "grid_nx":            self.grid_nx.value(),
            "grid_ny":            self.grid_ny.value(),
            "tau_recomb_ms":      self.tau_recomb.value(),
            "D_interstitial_m2s": DEFAULTS["D_interstitial_m2s"],
            "fdrt_threshold_hz":  self.fdrt_thresh.value(),
            "amplifier_bw_hz":    DEFAULTS["amplifier_bw_hz"],
            "flatness_target_pct": DEFAULTS["flatness_target_pct"],
            "w1": self.w1.value(),
            "w2": self.w2.value(),
            "w3": self.w3.value(),
            "w4": self.w4.value(),
            "w5": self.w5.value(),
        }

    def set_fx(self, value: float):
        self.fx.setValue(value)

    def set_fy(self, value: float):
        self.fy.setValue(value)


# ─── Metrics tab ──────────────────────────────────────────────────────────────

class MetricsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Status badges
        self.status_ss   = QLabel("–")
        self.status_fwhm = QLabel("–")
        for lbl in (self.status_ss, self.status_fwhm):
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("padding: 6px; border-radius: 4px; font-weight: bold;")
            lbl.setMinimumHeight(36)

        status_row = QHBoxLayout()
        status_row.addWidget(self.status_ss)
        status_row.addWidget(self.status_fwhm)
        layout.addLayout(status_row)

        # Table
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Metric", "Value"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

        # Help text
        help_lbl = QLabel(
            "Flatness ≤10% (ASTM E521) = good uniformity.\n"
            "Pinch = edge hot-spot excess vs centre. "
            "FDRT steady-state = beam revisit faster than recombination."
        )
        help_lbl.setWordWrap(True)
        help_lbl.setStyleSheet("color: #666; font-size: 10px; padding: 4px;")
        layout.addWidget(help_lbl)

    def update(self, metrics: dict):
        rows = [
            ("Flatness (%)",            f"{metrics['flatness_pct']:.3f}"),
            ("RMS deviation (%)",       f"{metrics['rms_pct']:.3f}"),
            ("Max/Min ratio",           f"{metrics['max_min_ratio']:.4f}"),
            ("Pinch (%)",               f"{metrics['pinch_pct']:.3f}"),
            ("Dwell mean (s/bin)",      f"{metrics['dwell_mean']:.4e}"),
            ("Dwell std (s/bin)",       f"{metrics['dwell_std']:.4e}"),
            ("Dwell peak/min ratio",    f"{metrics['dwell_peak_min_ratio']:.3f}"),
            ("Characteristic τ (ms)",   f"{metrics['tau_ms']:.4f}"),
            ("Diffusion length (μm)",   f"{metrics['diffusion_length_um']:.4f}"),
            ("Steady state",            str(metrics['steady_state'])),
            ("FWHM/spot rule pass",     str(metrics['fwhm_spot_pass'])),
            ("Spot spacing (mm)",       f"{metrics['spot_spacing_mm']:.4f}"),
        ]
        self.table.setRowCount(len(rows))
        for i, (k, v) in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(k))
            self.table.setItem(i, 1, QTableWidgetItem(v))

        if metrics["steady_state"]:
            self.status_ss.setText("✓  STEADY STATE (FDRT regime)")
            self.status_ss.setStyleSheet(
                "padding:6px; border-radius:4px; font-weight:bold;"
                "background:#c8f7c5; color:#155724;"
            )
        else:
            self.status_ss.setText("✗  TRANSIENT — fast freq below FDRT threshold")
            self.status_ss.setStyleSheet(
                "padding:6px; border-radius:4px; font-weight:bold;"
                "background:#f8d7da; color:#721c24;"
            )

        spacing = metrics["spot_spacing_mm"]
        if metrics["fwhm_spot_pass"]:
            self.status_fwhm.setText(f"✓  FWHM OK  (spot spacing {spacing:.3f} mm)")
            self.status_fwhm.setStyleSheet(
                "padding:6px; border-radius:4px; font-weight:bold;"
                "background:#c8f7c5; color:#155724;"
            )
        else:
            self.status_fwhm.setText(f"⚠  FWHM < 3× spot spacing ({spacing:.3f} mm)")
            self.status_fwhm.setStyleSheet(
                "padding:6px; border-radius:4px; font-weight:bold;"
                "background:#fff3cd; color:#856404;"
            )


# ─── Optimizer tab ────────────────────────────────────────────────────────────

class OptimizerTab(QWidget):
    apply_requested = pyqtSignal(float, float)  # (fx_opt, fy_opt)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ── Controls row ──────────────────────────────────────────────────────
        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Grid N:"))
        self.n_grid = QSpinBox()
        self.n_grid.setRange(3, 25)
        self.n_grid.setValue(10)
        self.n_grid.setFixedWidth(60)
        ctrl.addWidget(self.n_grid)

        self.grid_btn = QPushButton("Run Grid Search")
        self.grid_btn.setStyleSheet(
            "QPushButton { background:#2176ae; color:white; border-radius:5px;"
            " font-weight:bold; padding:4px 10px; }"
            "QPushButton:hover { background:#1a5e8f; }"
            "QPushButton:disabled { background:#aaa; }"
        )
        self.opt_btn = QPushButton("Run Optimizer (DE)  ⚠ slow")
        self.opt_btn.setStyleSheet(
            "QPushButton { background:#6f4e9e; color:white; border-radius:5px;"
            " font-weight:bold; padding:4px 10px; }"
            "QPushButton:hover { background:#5a3d80; }"
            "QPushButton:disabled { background:#aaa; }"
        )
        ctrl.addWidget(self.grid_btn)
        ctrl.addWidget(self.opt_btn)
        ctrl.addStretch()
        layout.addLayout(ctrl)

        # ── Progress ──────────────────────────────────────────────────────────
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(6)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # ── Result label ──────────────────────────────────────────────────────
        self.result_lbl = QLabel(
            "Run Grid Search for a fast landscape view.\n"
            "Run Optimizer (DE) for the best f₁/f₂ within the grid bounds (500–10000 Hz, 1–500 Hz)."
        )
        self.result_lbl.setWordWrap(True)
        self.result_lbl.setStyleSheet("color: #444; padding: 4px;")
        layout.addWidget(self.result_lbl)

        # ── Apply button ──────────────────────────────────────────────────────
        self.apply_btn = QPushButton("Apply Optimal f₁ / f₂ to Parameters")
        self.apply_btn.setEnabled(False)
        self.apply_btn.setStyleSheet(
            "QPushButton { background:#2ca02c; color:white; border-radius:5px;"
            " font-weight:bold; padding:4px 10px; }"
            "QPushButton:hover { background:#228822; }"
            "QPushButton:disabled { background:#ccc; color:#888; }"
        )
        layout.addWidget(self.apply_btn)

        # ── Plot area ─────────────────────────────────────────────────────────
        self.plot_tab = PlotTab()
        layout.addWidget(self.plot_tab, stretch=1)

        self._optimal_fx = None
        self._optimal_fy = None
        self._worker     = None
        self._get_params = lambda: {}

        self.grid_btn.clicked.connect(self._run_grid)
        self.opt_btn.clicked.connect(self._run_opt)
        self.apply_btn.clicked.connect(self._apply)

    def set_params_getter(self, getter):
        self._get_params = getter

    def _run_grid(self):
        if self._worker and self._worker.isRunning():
            return
        self._start_worker("grid", self.n_grid.value())

    def _run_opt(self):
        if self._worker and self._worker.isRunning():
            return
        reply = QMessageBox.question(
            self, "Run Optimizer",
            "Differential evolution can take 1–5 minutes.\nProceed?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._start_worker("optimize")

    def _start_worker(self, mode, n_grid=10):
        self.grid_btn.setEnabled(False)
        self.opt_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.result_lbl.setText(
            "Running grid search…" if mode == "grid"
            else "Running optimizer (differential evolution)… please wait."
        )
        self._worker = OptimizerWorker(mode, self._get_params(), n_grid)
        self._worker.finished.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_result(self, result):
        self.grid_btn.setEnabled(True)
        self.opt_btn.setEnabled(True)
        self.progress.setVisible(False)

        mode = result[0]
        if mode == "grid":
            _, fx_vals, fy_vals, J_grid = result
            best_idx = np.unravel_index(np.argmin(J_grid), J_grid.shape)
            self._optimal_fx = fx_vals[best_idx[0]]
            self._optimal_fy = fy_vals[best_idx[1]]
            best_J           = J_grid[best_idx]
            self.apply_btn.setEnabled(True)
            self.result_lbl.setText(
                f"Grid best:  f₁ = {self._optimal_fx:.0f} Hz,  "
                f"f₂ = {self._optimal_fy:.0f} Hz,  J = {best_J:.3f}\n"
                "Press 'Apply' to copy these values to the parameter panel."
            )
            self._show_grid_plot(fx_vals, fy_vals, J_grid,
                                 self._optimal_fx, self._optimal_fy)
        else:
            _, opt_result = result
            fx_opt, fy_opt, ax_f, ay_f = opt_result.x
            self._optimal_fx = fx_opt
            self._optimal_fy = fy_opt
            self.apply_btn.setEnabled(True)
            self.result_lbl.setText(
                f"Optimizer converged!  J = {opt_result.fun:.4f}\n"
                f"f₁ = {fx_opt:.0f} Hz   f₂ = {fy_opt:.0f} Hz   "
                f"X-overscan = {ax_f:.3f}×   Y-overscan = {ay_f:.3f}×\n"
                "Press 'Apply' to copy f₁/f₂ to the parameter panel."
            )

    def _on_error(self, msg):
        self.grid_btn.setEnabled(True)
        self.opt_btn.setEnabled(True)
        self.progress.setVisible(False)
        self.result_lbl.setText(f"Error: {msg}")
        QMessageBox.critical(self, "Optimizer Error", msg)

    def _apply(self):
        if self._optimal_fx is not None:
            self.apply_requested.emit(self._optimal_fx, self._optimal_fy)

    def _show_grid_plot(self, fx_vals, fy_vals, J_grid, best_fx, best_fy):
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(7, 5))
        pcm = ax.pcolormesh(fy_vals, fx_vals, J_grid, cmap="viridis_r", shading="auto")
        fig.colorbar(pcm, ax=ax, label="Objective J  (lower = better)")
        ax.scatter([best_fy], [best_fx], color="red", marker="*", s=250,
                   zorder=5, label=f"Best  f₁={best_fx:.0f}, f₂={best_fy:.0f}")
        ax.set_xlabel("f₂ (Hz)")
        ax.set_ylabel("f₁ (Hz)")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_title("Objective Landscape (lower = better)")
        ax.legend(fontsize=9)
        fig.tight_layout()
        self.plot_tab.set_figure(fig)


# ─── Main Window ──────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ion-Beam Raster Scan Analysis Tool  —  UW-IBL / MIBL")
        self.resize(1440, 920)

        self._worker      = None
        self._last_result = None

        # ── Central widget ────────────────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(4)

        # ── Top bar ──────────────────────────────────────────────────────────
        top = QHBoxLayout()
        title = QLabel("Ion-Beam Raster Scan Analysis Tool")
        title.setFont(QFont("", 14, QFont.Bold))

        self.run_btn = QPushButton("▶  Run")
        self.run_btn.setFixedHeight(36)
        self.run_btn.setMinimumWidth(120)
        self.run_btn.setStyleSheet(
            "QPushButton { background:#2176ae; color:white; border-radius:6px;"
            " font-weight:bold; font-size:13px; }"
            "QPushButton:hover { background:#1a5e8f; }"
            "QPushButton:disabled { background:#aaa; }"
        )

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(8)
        self.progress.setVisible(False)

        self.status_lbl = QLabel("Ready")
        self.status_lbl.setStyleSheet("color: #555; font-size: 11px;")

        top.addWidget(title)
        top.addStretch()
        top.addWidget(self.status_lbl)
        top.addWidget(self.run_btn)
        main_layout.addLayout(top)
        main_layout.addWidget(self.progress)

        # ── Splitter ─────────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter, stretch=1)

        self.params_panel = ParamPanel()
        splitter.addWidget(self.params_panel)

        self.tabs = QTabWidget()
        splitter.addWidget(self.tabs)
        splitter.setSizes([300, 1140])

        # Plot tabs
        self.tab_dose2d   = PlotTab()
        self.tab_dose3d   = PlotTab()
        self.tab_velocity = PlotTab()
        self.tab_dwell    = PlotTab()
        self.tab_traj     = PlotTab()
        self.tab_metrics  = MetricsTab()
        self.tab_optimizer = OptimizerTab()

        self.tabs.addTab(self.tab_dose2d,    "Dose Map (2D)")
        self.tabs.addTab(self.tab_dose3d,    "Dose Surface (3D)")
        self.tabs.addTab(self.tab_velocity,  "Velocity Profile")
        self.tabs.addTab(self.tab_dwell,     "Dwell Distribution")
        self.tabs.addTab(self.tab_traj,      "Trajectory")
        self.tabs.addTab(self.tab_metrics,   "Metrics")
        self.tabs.addTab(self.tab_optimizer, "Optimizer")

        # Wire optimizer ↔ params panel
        self.tab_optimizer.set_params_getter(self.params_panel.get_params)
        self.tab_optimizer.apply_requested.connect(self._apply_optimal)

        # Signals
        self.run_btn.clicked.connect(self.run)

        # Auto-run on start
        QTimer.singleShot(200, self.run)

    # ── Compute ───────────────────────────────────────────────────────────────

    def run(self):
        if self._worker and self._worker.isRunning():
            return
        params = self.params_panel.get_params()
        self.run_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.status_lbl.setText("Computing…")

        self._worker = ComputeWorker(params)
        self._worker.finished.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_result(self, result):
        dose, rho, xe, ye, t_arr, x_arr, y_arr, metrics = result
        params   = self.params_panel.get_params()
        aperture = (params["aperture_xL_mm"], params["aperture_xR_mm"],
                    params["aperture_yB_mm"], params["aperture_yT_mm"])

        self.tab_dose2d.set_figure(plot_heatmap(dose, xe, ye, aperture, metrics))
        self.tab_dose3d.set_figure(plot_dose_3d(dose, xe, ye, aperture, metrics))
        self.tab_velocity.set_figure(plot_velocity_profile(params, t_arr, x_arr, y_arr))

        mask = _aperture_mask_from_edges(xe, ye, *aperture)
        self.tab_dwell.set_figure(plot_dwell_hist(rho, mask))

        self.tab_traj.set_figure(self._make_trajectory_fig(x_arr, y_arr, t_arr, aperture, params))
        self.tab_metrics.update(metrics)

        flat  = metrics["flatness_pct"]
        color = "#2ca02c" if flat <= 10 else ("#d62728" if flat > 30 else "#ff7f0e")
        self.status_lbl.setText(
            f"Done — flatness: <span style='color:{color};font-weight:bold'>"
            f"{flat:.1f}%</span>  pinch: {metrics['pinch_pct']:.1f}%"
        )
        self.status_lbl.setTextFormat(Qt.RichText)
        self.run_btn.setEnabled(True)
        self.progress.setVisible(False)
        self._last_result = result

    def _on_error(self, msg):
        self.run_btn.setEnabled(True)
        self.progress.setVisible(False)
        self.status_lbl.setText(f"Error: {msg}")
        QMessageBox.critical(self, "Compute Error", msg)

    def _apply_optimal(self, fx: float, fy: float):
        self.params_panel.set_fx(fx)
        self.params_panel.set_fy(fy)

    @staticmethod
    def _make_trajectory_fig(x_arr, y_arr, t_arr, aperture, params):
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches

        n_preview = min(3000, len(x_arr))
        fig, ax   = plt.subplots(figsize=(6, 5))
        sc = ax.scatter(x_arr[:n_preview], y_arr[:n_preview],
                        c=t_arr[:n_preview] * 1000, cmap="plasma", s=1, alpha=0.7)
        xL, xR, yB, yT = aperture
        rect = mpatches.Rectangle(
            (xL, yB), xR - xL, yT - yB,
            linewidth=2, edgecolor="#00e5cc", facecolor="none", linestyle="--",
        )
        ax.add_patch(rect)
        fig.colorbar(sc, ax=ax, label="Time (ms)")
        ax.set_xlabel("X (mm)")
        ax.set_ylabel("Y (mm)")
        pattern = params.get("pattern", "")
        fx      = params.get("fx_hz", 0)
        fy      = params.get("fy_hz", 0)
        ax.set_title(f"{pattern}  f₁={fx:.0f} Hz  f₂={fy:.0f} Hz")
        ax.set_facecolor("#111")
        fig.patch.set_facecolor("#111")
        ax.tick_params(colors="white")
        ax.xaxis.label.set_color("white")
        ax.yaxis.label.set_color("white")
        ax.title.set_color("white")
        fig.tight_layout()
        return fig


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    from PyQt5.QtGui import QPalette
    pal = app.palette()
    pal.setColor(QPalette.Window, QColor(245, 245, 245))
    app.setPalette(pal)

    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
