"""
Raster Scan Analysis Tool — Native Desktop GUI
Run: python app.py   (from inside the rbl/ directory)

PySide6 + embedded matplotlib figures. No browser, no server.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter,
    QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QDoubleSpinBox, QSpinBox,
    QComboBox, QTabWidget, QScrollArea,
    QGroupBox, QProgressBar, QSizePolicy, QMessageBox,
    QTabBar, QStackedWidget,
    QTableWidget, QTableWidgetItem, QHeaderView,
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QFont, QColor, QPalette

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

from rbla.config.defaults import DEFAULTS
from rbla.scan.patterns import get_realistic_trajectory
from rbla.scan.dose import compute_dose
from rbla.scan.metrics import compute_all_metrics, _aperture_mask_from_edges
from rbla.gui.viz import plot_heatmap, plot_dose_3d, plot_velocity_profile, plot_dwell_hist, plot_waveform_comparison
from rbla.physics.deflection_physics import (
    SPECIES_TABLE, DEFAULT_TRAVEL_MM, AMPLIFIER_MAX_KV, FG_MAX_VPP,
    BEAMLINE_POIS, calculate_drive_for_deflection,
    calculate_deflection_for_voltage,
)
from rbla.physics.magnet_physics import (
    calculate_magnet_field, MAGNET_MAX_GAUSS, MAGNET_REFERENCE_ROWS,
)
from rbla.config.lab_presets import FREQUENCY_PRESETS


# ─── Worker threads ───────────────────────────────────────────────────────────

class ComputeWorker(QThread):
    finished = Signal(object)
    error    = Signal(str)

    def __init__(self, params):
        super().__init__()
        self.params = params

    def run(self):
        try:
            p = self.params
            t, x, y = get_realistic_trajectory(p)
            dose, rho, xe, ye = compute_dose(p, t, x, y)
            dt = t[1] - t[0] if len(t) > 1 else 1.0
            m  = compute_all_metrics(dose, rho, x, y, dt, xe, ye, p)
            self.finished.emit((dose, rho, xe, ye, t, x, y, m))
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
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self._layout.addWidget(self.toolbar)
        self._layout.addWidget(self.canvas)
        self.canvas.draw()
        plt.close(fig)

    def set_metrics(self, rows):
        """Display a key: value summary bar above the figure."""
        for i in range(self._layout.count()):
            w = self._layout.itemAt(i)
            if w and w.widget() and w.widget().objectName() == "_metrics_bar":
                w.widget().deleteLater()
                break
        if not rows:
            return
        text = "   |   ".join(f"{k}: {v}" for k, v in rows)
        lbl = QLabel(text)
        lbl.setObjectName("_metrics_bar")
        lbl.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #1a1a3a; padding: 5px 8px;"
            " background: #dde4f0; border-bottom: 2px solid #8899bb;"
        )
        lbl.setWordWrap(True)
        self._layout.insertWidget(0, lbl)


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
        self.setMaximumWidth(400)

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
        note.setStyleSheet("color: #555; font-size: 10px;")
        note.setWordWrap(True)
        f.addRow(note)
        self.fx = _dbl(0.5, 50000.0, DEFAULTS["fx_hz"], 10.0, decimals=1)
        self.fy = _dbl(0.5, 50000.0, DEFAULTS["fy_hz"], 10.0, decimals=1)
        f.addRow("f₁ (Hz)", self.fx)
        f.addRow("f₂ (Hz)", self.fy)

        # ── Lab frequency preset (added) ─────────────────────────────────────
        self.freq_preset = QComboBox()
        self.freq_preset.addItem("— Select preset —")
        for _name in FREQUENCY_PRESETS:
            self.freq_preset.addItem(_name)
        self.freq_preset.currentTextChanged.connect(self._apply_freq_preset)
        f.addRow("Lab preset", self.freq_preset)

        # ── Amplifier (EEL5000) ──────────────────────────────────────────────
        f = group("Amplifier (EEL5000)")
        from PySide6.QtWidgets import QCheckBox
        self.simulate_amp = QCheckBox("Simulate amplifier (global)")
        self.simulate_amp.setChecked(bool(DEFAULTS["simulate_amplifier"]))
        f.addRow(self.simulate_amp)

        self.amp_bw   = _dbl(100.0, 100000.0, DEFAULTS["amplifier_bw_hz"],        500.0, decimals=1)
        self.amp_slew = _dbl(1.0,   5000.0,   DEFAULTS["amplifier_slew_V_per_us"], 10.0, decimals=1)
        self.amp_kvmm = _dbl(0.001, 10.0,     DEFAULTS["kV_per_mm"],               0.01, decimals=3)
        f.addRow("-3 dB BW (Hz)",        self.amp_bw)
        f.addRow("Slew (V/us)",          self.amp_slew)
        f.addRow("Calibration (kV/mm)",  self.amp_kvmm)

        def _toggle_amp(state):
            enabled = bool(state)
            for w in (self.amp_bw, self.amp_slew, self.amp_kvmm):
                w.setEnabled(enabled)
        self.simulate_amp.stateChanged.connect(_toggle_amp)
        _toggle_amp(self.simulate_amp.isChecked())

        # ── Pattern ───────────────────────────────────────────────────────────
        f = group("Pattern")
        self.pattern = QComboBox()
        self.pattern.addItems(
            ["classic", "alt_axes", "lissajous", "spiral", "sinusoidal", "wobble"]
        )
        self.phase = _dbl(0.0, 360.0, DEFAULTS["lissajous_phase_deg"], 1.0, decimals=1)
        f.addRow("Pattern", self.pattern)
        f.addRow("Lissajous phase (°)", self.phase)

        self.pattern_desc = QLabel()
        self.pattern_desc.setWordWrap(True)
        self.pattern_desc.setStyleSheet("color: #555; font-size: 10px;")
        f.addRow(self.pattern_desc)

        self.spiral_warn = QLabel("⚠ f₁ is unused by spiral — only f₂ sets rotation rate.")
        self.spiral_warn.setWordWrap(True)
        self.spiral_warn.setStyleSheet("color: #a06000; font-size: 10px;")
        self.spiral_warn.setVisible(False)
        f.addRow(self.spiral_warn)

        self.pattern.currentTextChanged.connect(self._on_pattern_changed)
        self._on_pattern_changed(self.pattern.currentText())

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

        root.addStretch()

    _PATTERN_FORMULAS = {
        "classic":    "x = triangle(f₁·t),  y = ramp(f₂·t)",
        "alt_axes":   "x/y swap fast↔slow each frame at f₂",
        "lissajous":  "x = sin(f₁·t),  y = sin(f₂·t + φ)",
        "spiral":     "r = t·r_max/T,  θ = f₂·t  [f₁ unused]",
        "sinusoidal": "x = sin(f₁·t),  y = ramp(f₂·t)",
        "wobble":     "x = sin(f₁·t),  y = sin(f₂·t)",
    }

    def _on_pattern_changed(self, name: str):
        self.pattern_desc.setText(self._PATTERN_FORMULAS.get(name, ""))
        self.spiral_warn.setVisible(name == "spiral")

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
            "amplifier_bw_hz":           self.amp_bw.value(),
            "simulate_amplifier":        self.simulate_amp.isChecked(),
            "amplifier_slew_V_per_us":   self.amp_slew.value(),
            "kV_per_mm":                 self.amp_kvmm.value(),
            "flatness_target_pct": DEFAULTS["flatness_target_pct"],
        }

    def set_fx(self, value: float):
        self.fx.setValue(value)

    def set_fy(self, value: float):
        self.fy.setValue(value)

    def _apply_freq_preset(self, name: str):
        """Lab dropdown handler: copy preset frequencies to f₁/f₂ spin boxes."""
        if name not in FREQUENCY_PRESETS:
            return
        p = FREQUENCY_PRESETS[name]
        self.fx.setValue(p["f1_hz"])
        self.fy.setValue(p["f2_hz"])


# ─── Voltage Calculator tab ───────────────────────────────────────────────────

class VoltageCalcTab(QWidget):
    """Voltage calculator for EEL5000 / DG1000Z amplifier chain."""

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel("Voltage / Deflection Calculator")
        title.setStyleSheet("font-size: 13px; font-weight: bold;")
        layout.addWidget(title)

        # ── Compact 2-column inputs ───────────────────────────────────────────
        inp_box = QGroupBox("Beam parameters")
        inp_h = QHBoxLayout(inp_box)
        inp_h.setSpacing(16)
        inp_h.setContentsMargins(8, 4, 8, 4)

        left_form = QFormLayout()
        left_form.setSpacing(4)
        self.species = QComboBox()
        for _n in SPECIES_TABLE:
            self.species.addItem(_n)
        left_form.addRow("Species", self.species)
        self.mass = _dbl(0.1, 300.0, 1.0, 1.0, decimals=2)
        self.mass.setEnabled(False)
        left_form.addRow("Mass (amu)", self.mass)
        self.energy = _dbl(0.01, 50.0, 3.0, 0.1, decimals=3)
        left_form.addRow("Energy (MeV)", self.energy)
        self.charge = _int(1, 10, 1)
        left_form.addRow("Charge state (q)", self.charge)

        right_form = QFormLayout()
        right_form.setSpacing(4)
        self.deflection = _dbl(0.1, 100.0, 10, 0.1, decimals=3)
        right_form.addRow("X deflection at sample (mm)", self.deflection)
        self.deflection_y = _dbl(0.1, 100.0, 10, 0.1, decimals=3)
        right_form.addRow("Y deflection at sample (mm)", self.deflection_y)
        self.travel = _dbl(100.0, 10000.0, DEFAULT_TRAVEL_MM, 10.0, decimals=2)
        right_form.addRow("Travel length (mm)", self.travel)

        inp_h.addLayout(left_form)
        inp_h.addLayout(right_form)
        layout.addWidget(inp_box)

        # ── Drive & Results (merged) ──────────────────────────────────────────
        self._updating = False  # re-entrancy guard

        dr_box = QGroupBox("Drive & Results")
        dr_form = QFormLayout(dr_box)
        dr_form.setSpacing(4)
        dr_form.setContentsMargins(8, 4, 8, 4)
        self.plate_kv_x = _dbl(0.0, 10.0, 0.0, 0.001, decimals=4)
        self.plate_kv_x.setSuffix(" kV")
        dr_form.addRow("X plate voltage (peak):", self.plate_kv_x)
        self.plate_kv_y = _dbl(0.0, 10.0, 0.0, 0.001, decimals=4)
        self.plate_kv_y.setSuffix(" kV")
        dr_form.addRow("Y plate voltage (peak):", self.plate_kv_y)
        self.out_warn = QLabel("")
        self.out_warn.setStyleSheet("color: #ffa500; font-weight: bold;")
        self.out_warn.setWordWrap(True)
        dr_form.addRow("Status:", self.out_warn)
        layout.addWidget(dr_box)

        # ── Points-of-interest table ──────────────────────────────────────────
        poi_box = QGroupBox("Beamline points of interest")
        poi_layout = QVBoxLayout(poi_box)
        poi_layout.setContentsMargins(4, 4, 4, 4)
        self.poi_table = QTableWidget(0, 4)
        self.poi_table.setHorizontalHeaderLabels(
            ["Point", "Distance (mm)", "X defl (mm)", "Y defl (mm)"]
        )
        self.poi_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.poi_table.verticalHeader().setVisible(False)
        self.poi_table.setMaximumHeight(140)
        for _poi in BEAMLINE_POIS:
            self._append_poi_row(_poi["name"], _poi["distance_mm"],
                                 _poi.get("tip_x"), _poi.get("tip_y"),
                                 _poi.get("label_x"), _poi.get("label_y"))
        poi_layout.addWidget(self.poi_table)
        layout.addWidget(poi_box)

        # ── Beamline image with live overlay ──────────────────────────────────
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        self._beam_fig = Figure(figsize=(7, 3))
        self._beam_ax = self._beam_fig.add_subplot(111)
        self._beam_canvas = FigureCanvasQTAgg(self._beam_fig)
        self._beam_canvas.setMinimumHeight(200)
        self._beam_canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._beam_bg_loaded = self._load_beamline_image()
        layout.addWidget(self._beam_canvas, stretch=1)

        # ── Signals ───────────────────────────────────────────────────────────
        self.species.currentTextChanged.connect(self._on_species_changed)
        for _w in (self.energy, self.charge, self.deflection, self.deflection_y,
                   self.travel, self.mass):
            _w.valueChanged.connect(self._recompute)
        self._on_species_changed(self.species.currentText())

        self.plate_kv_x.valueChanged.connect(lambda *_: self._sync_from_voltage())
        self.plate_kv_y.valueChanged.connect(lambda *_: self._sync_from_voltage())
        self.poi_table.cellChanged.connect(self._on_poi_cell_changed)
        _sx = calculate_drive_for_deflection(
            self.deflection.value(), self.energy.value(),
            self.charge.value(), self.travel.value(),
        )
        _sy = calculate_drive_for_deflection(
            self.deflection_y.value(), self.energy.value(),
            self.charge.value(), self.travel.value(),
        )
        self.plate_kv_x.setValue(_sx["plate_kV"])
        self.plate_kv_y.setValue(_sy["plate_kV"])
        self._sync_from_voltage()

    def _on_species_changed(self, name):
        if name in SPECIES_TABLE:
            self.mass.setEnabled(name == "Custom")
            if name != "Custom":
                self.mass.setValue(SPECIES_TABLE[name]["mass"])
        self._recompute()

    def _recompute(self, *_):
        rx = calculate_drive_for_deflection(
            self.deflection.value(), self.energy.value(),
            self.charge.value(), self.travel.value(),
        )
        ry = calculate_drive_for_deflection(
            self.deflection_y.value(), self.energy.value(),
            self.charge.value(), self.travel.value(),
        )
        if not self._updating:
            self._updating = True
            self.plate_kv_x.setValue(rx["plate_kV"])
            self.plate_kv_y.setValue(ry["plate_kV"])
            self._updating = False
        self._sync_from_voltage()

    # ── Multi-point overlay machinery ─────────────────────────────────────────

    def _append_poi_row(self, name: str, distance_mm: float,
                        tip_x=None, tip_y=None,
                        label_x=None, label_y=None):
        """Add one POI row. Overlay geometry is stored as item user data."""
        r = self.poi_table.rowCount()
        self.poi_table.insertRow(r)
        name_item = QTableWidgetItem(name)
        name_item.setData(256, {"tip_x": tip_x, "tip_y": tip_y,
                                "label_x": label_x, "label_y": label_y})
        self.poi_table.setItem(r, 0, name_item)
        self.poi_table.setItem(r, 1, QTableWidgetItem(f"{distance_mm:.1f}"))
        self.poi_table.setItem(r, 2, QTableWidgetItem("—"))
        self.poi_table.setItem(r, 3, QTableWidgetItem("—"))

    def _load_beamline_image(self) -> bool:
        """Load the PNG once as a static background. Returns True on success."""
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "assets", "beamline.png")
        path = os.path.normpath(path)
        try:
            import matplotlib.image as mpimg
            self._beam_img = mpimg.imread(path)
            return True
        except Exception:
            self._beam_img = None
            return False

    def _sync_from_voltage(self):
        """MASTER recompute. Reads both plate kV spinboxes → updates POI table + image.
        X and Y axes are independent; editing one never touches the other."""
        if self._updating:
            return
        self._updating = True
        try:
            kv_x   = self.plate_kv_x.value()
            kv_y   = self.plate_kv_y.value()
            energy = self.energy.value()
            charge = self.charge.value()

            warns = []
            for axis, kv in (("X", kv_x), ("Y", kv_y)):
                if kv > AMPLIFIER_MAX_KV:
                    warns.append(f"⚠ {axis} plate exceeds EEL5000 +/-{AMPLIFIER_MAX_KV} kV")
                if kv * 2.0 > FG_MAX_VPP:
                    warns.append(f"⚠ {axis} FG Vpp exceeds DG1000Z ~{FG_MAX_VPP} V")
            self.out_warn.setText("   ".join(warns) if warns
                                   else "✓ within EEL5000 and DG1000Z limits")
            self.out_warn.setStyleSheet(
                "color: #ff6b6b; font-weight: bold;" if warns
                else "color: #4caf50; font-weight: bold;"
            )

            labels = []
            for r in range(self.poi_table.rowCount()):
                try:
                    dist = float(self.poi_table.item(r, 1).text())
                except (ValueError, AttributeError):
                    continue
                x_defl = calculate_deflection_for_voltage(kv_x, energy, charge, dist)
                y_defl = calculate_deflection_for_voltage(kv_y, energy, charge, dist)
                self.poi_table.item(r, 2).setText(f"{x_defl:.3f}")
                self.poi_table.item(r, 3).setText(f"{y_defl:.3f}")
                name = self.poi_table.item(r, 0).text()
                geo  = (self.poi_table.item(r, 0).data(256) or {})
                labels.append((name, x_defl, y_defl,
                               geo.get("tip_x"), geo.get("tip_y"),
                               geo.get("label_x"), geo.get("label_y")))
            self._redraw_overlay(labels)
        finally:
            self._updating = False

    def _redraw_overlay(self, labels):
        """Redraw image + annotations. All coordinates are absolute image pixels."""
        ax = self._beam_ax
        ax.clear()
        ax.axis("off")
        if getattr(self, "_beam_img", None) is not None:
            ax.imshow(self._beam_img, aspect="auto")
            w = self._beam_img.shape[1]
        else:
            ax.text(0.5, 0.5, "beamline.png not found",
                    ha="center", va="center", transform=ax.transAxes)
            self._beam_canvas.draw_idle()
            return
        for name, x_defl, y_defl, tip_x, tip_y, label_x, label_y in labels:
            ax.annotate(
                f"{name}\nX: {x_defl:.2f} mm\nY: {y_defl:.2f} mm",
                xy=(tip_x, tip_y),
                xytext=(label_x, label_y),
                ha="center", va="top", fontsize=7,
                arrowprops=dict(arrowstyle="-", lw=0.6),
            )
        self._beam_fig.tight_layout()
        self._beam_canvas.draw_idle()

    def _on_poi_cell_changed(self, row: int, col: int):
        """Edit entry point #2: a POI cell changed.
        - distance/name edited  -> just recompute (col 1 or 0)
        - DEFLECTION edited (col 2) -> solve voltage from THIS row,
          push to FG field, which triggers the master recompute of all rows."""
        if self._updating:
            return
        try:
            dist = float(self.poi_table.item(row, 1).text())
        except (ValueError, AttributeError):
            return
        if col == 2:
            # X deflection edited → solve X voltage only.
            try:
                val = float(self.poi_table.item(row, 2).text())
            except (ValueError, AttributeError):
                return
            r = calculate_drive_for_deflection(
                val, self.energy.value(), self.charge.value(), dist)
            self._updating = True
            self.plate_kv_x.setValue(r["plate_kV"])
            self._updating = False
            self._sync_from_voltage()
        elif col == 3:
            # Y deflection edited → solve Y voltage only.
            try:
                val = float(self.poi_table.item(row, 3).text())
            except (ValueError, AttributeError):
                return
            r = calculate_drive_for_deflection(
                val, self.energy.value(), self.charge.value(), dist)
            self._updating = True
            self.plate_kv_y.setValue(r["plate_kV"])
            self._updating = False
            self._sync_from_voltage()
        else:
            # Name or distance edited → full recompute.
            self._sync_from_voltage()


class MagnetCalcTab(QWidget):
    """Bending-magnet field calculator for the 45° switching magnet.

    Decoupled from the compute pipeline / optimizer: it only reads its own
    inputs and shows B (Gauss) + % of max. Structure mirrors VoltageCalcTab so
    the two calculators look and behave consistently, and both populate their
    Species dropdown from the shared SPECIES_TABLE (single source of truth).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._updating = False  # re-entrancy guard (species auto-fills mass)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel("Magnet Field Calculator")
        title.setStyleSheet("font-size: 13px; font-weight: bold;")
        layout.addWidget(title)

        subtitle = QLabel(
            "45° switching magnet, right beamline  ·  "
            "B = 45.54·√(m·E_keV) / (q·R),  R = 0.615 m"
        )
        subtitle.setStyleSheet("color: #555; font-size: 11px;")
        layout.addWidget(subtitle)

        # ── Beam parameters ───────────────────────────────────────────────────
        inp_box = QGroupBox("Beam parameters")
        inp_form = QFormLayout(inp_box)
        inp_form.setSpacing(4)
        inp_form.setContentsMargins(8, 4, 8, 4)

        self.species = QComboBox()
        for _n in SPECIES_TABLE:
            self.species.addItem(_n)
        inp_form.addRow("Species", self.species)

        self.mass = _dbl(0.1, 300.0, 1.0, 1.0, decimals=3)
        self.mass.setEnabled(False)
        inp_form.addRow("Mass (amu)", self.mass)

        # The magnet formula wants keV; the rest of the app works in MeV, so the
        # user enters MeV here and we convert E_keV = E_MeV * 1000 in the handler.
        self.energy = _dbl(0.01, 50.0, 2.0, 0.1, decimals=3)
        inp_form.addRow("Energy (MeV)", self.energy)

        self.charge = _int(1, 10, 2)
        inp_form.addRow("Charge state (q)", self.charge)

        layout.addWidget(inp_box)

        # ── Results ───────────────────────────────────────────────────────────
        res_box = QGroupBox("Results")
        res_form = QFormLayout(res_box)
        res_form.setSpacing(4)
        res_form.setContentsMargins(8, 4, 8, 4)

        self.field_gauss = QLabel("—")
        self.field_gauss.setStyleSheet("font-weight: bold;")
        res_form.addRow("Field (Gauss):", self.field_gauss)

        self.field_pct = QLabel("—")
        self.field_pct.setStyleSheet("font-weight: bold;")
        res_form.addRow("Field (% of max):", self.field_pct)

        self.out_warn = QLabel("")
        self.out_warn.setWordWrap(True)
        res_form.addRow("Status:", self.out_warn)

        layout.addWidget(res_box)

        # ── Notes ─────────────────────────────────────────────────────────────
        notes = QLabel(
            "Notes:  Max field ≈ 16–17 kGauss.   "
            "Typical energies: 1.5–3.0 MeV for 1+, 2.25–4.5 MeV for 2+.   "
            "3+ charge states don't yield much beam current."
        )
        notes.setWordWrap(True)
        notes.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(notes)

        # ── Reference table (read-only) ───────────────────────────────────────
        ref_box = QGroupBox("Known-good reference (surveyed species)")
        ref_layout = QVBoxLayout(ref_box)
        ref_layout.setContentsMargins(4, 4, 4, 4)
        self.ref_table = QTableWidget(len(MAGNET_REFERENCE_ROWS), 5)
        self.ref_table.setHorizontalHeaderLabels(
            ["Species", "Mass (amu)", "Energy (keV)", "q", "Field (Gauss)"]
        )
        self.ref_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.ref_table.verticalHeader().setVisible(False)
        self.ref_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for r, row in enumerate(MAGNET_REFERENCE_ROWS):
            self.ref_table.setItem(r, 0, QTableWidgetItem(str(row["species"])))
            self.ref_table.setItem(r, 1, QTableWidgetItem(f"{row['mass_amu']:.3f}"))
            self.ref_table.setItem(r, 2, QTableWidgetItem(f"{row['energy_keV']:.0f}"))
            self.ref_table.setItem(r, 3, QTableWidgetItem(str(row["charge_state"])))
            self.ref_table.setItem(r, 4, QTableWidgetItem(f"{row['field_gauss']:.1f}"))
        ref_layout.addWidget(self.ref_table)
        layout.addWidget(ref_box, stretch=1)

        # ── Signals ───────────────────────────────────────────────────────────
        self.species.currentTextChanged.connect(self._on_species_changed)
        for _w in (self.mass, self.energy, self.charge):
            _w.valueChanged.connect(self._recompute)
        self._on_species_changed(self.species.currentText())

    def _on_species_changed(self, name):
        if name in SPECIES_TABLE:
            self.mass.setEnabled(name == "Custom")
            if name != "Custom":
                self._updating = True
                self.mass.setValue(SPECIES_TABLE[name]["mass"])
                self._updating = False
        self._recompute()

    def _recompute(self, *_):
        if self._updating:
            return
        # Convert MeV (UI) -> keV (formula).
        energy_keV = self.energy.value() * 1000.0
        r = calculate_magnet_field(self.mass.value(), energy_keV,
                                   self.charge.value())
        self.field_gauss.setText(f"{r['field_gauss']:.1f} G")
        self.field_pct.setText(f"{r['pct_of_max']:.1f} %")
        if r["exceeds_max"]:
            self.out_warn.setText(
                f"⚠ Exceeds magnet max (~{MAGNET_MAX_GAUSS:.0f} G) — magnet saturates"
            )
            self.out_warn.setStyleSheet("color: #ffa500; font-weight: bold;")
        else:
            self.out_warn.setText("✓ within magnet field range")
            self.out_warn.setStyleSheet("color: #4caf50; font-weight: bold;")


# ─── Hardware view (Motors + Current, switchable / splittable) ───────────────

class HardwareView(QWidget):
    """Holds MotorTab and CurrentTab in a splitter.
    Call show_motors_only / show_current_only / show_both to configure layout.

    Each panel is wrapped in a QScrollArea so that, in split view, it can shrink
    below its natural width and grow an internal scrollbar instead of forcing the
    splitter wider than the window (which previously pushed the handle off-centre
    and shoved content off-screen). With the panels free to shrink, the splitter
    can always honour an exact 50/50 division that stays inside the viewport.
    """

    def __init__(self, motor_tab, current_tab, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)

        self._motor_scroll   = self._wrap(motor_tab)
        self._current_scroll = self._wrap(current_tab)
        self._splitter.addWidget(self._motor_scroll)
        self._splitter.addWidget(self._current_scroll)
        # Equal stretch so any leftover space is shared evenly between panels.
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 1)
        layout.addWidget(self._splitter)

        self._split_mode = False
        self.show_motors_only()

    @staticmethod
    def _wrap(widget):
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QScrollArea.Shape.NoFrame)
        sa.setWidget(widget)
        return sa

    def show_motors_only(self):
        self._split_mode = False
        self._splitter.widget(0).setVisible(True)
        self._splitter.widget(1).setVisible(False)

    def show_current_only(self):
        self._split_mode = False
        self._splitter.widget(0).setVisible(False)
        self._splitter.widget(1).setVisible(True)

    def show_both(self):
        self._split_mode = True
        self._splitter.widget(0).setVisible(True)
        self._splitter.widget(1).setVisible(True)
        self._apply_even_split()
        # Re-apply once the event loop has finalised the splitter geometry, in
        # case width() was not yet up to date at click time.
        QTimer.singleShot(0, self._apply_even_split)

    def _apply_even_split(self):
        """Divide the splitter exactly 50/50 within the current width."""
        if not self._split_mode:
            return
        w = self._splitter.width()
        if w > 0:
            self._splitter.setSizes([w // 2, w // 2])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Keep the divider centred as the window is resized while split.
        self._apply_even_split()


# ─── Main Window ──────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Right Beam Line Analysis Tool")
        self.resize(1440, 920)

        self._worker      = None
        self._last_result = None

        # ── Outer navigation: tab bar + stacked widget ────────────────────────
        # Three tabs share one stacked widget.  Tabs 1 & 2 (hardware) map to
        # the same HardwareView page so motors/current widgets are never
        # duplicated.  Clicking from one hardware tab to the other → split view.
        outer_widget = QWidget()
        self.setCentralWidget(outer_widget)
        outer_layout = QVBoxLayout(outer_widget)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self._outer_tabbar = QTabBar()
        self._outer_tabbar.addTab("Analysis")
        self._outer_tabbar.setExpanding(False)
        self._outer_tabbar.setDocumentMode(True)
        self._outer_tabbar.hide()   # single-page analysis app: no outer nav needed
        outer_layout.addWidget(self._outer_tabbar)

        self._outer_stack = QStackedWidget()
        outer_layout.addWidget(self._outer_stack, stretch=1)

        # ── Analysis page (stack index 0) ─────────────────────────────────────
        central = QWidget()
        self._outer_stack.addWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(4)

        # ── Top bar ──────────────────────────────────────────────────────────
        top = QHBoxLayout()
        title = QLabel("Ion-Beam Raster Scan Analysis Tool")
        title.setFont(QFont("", 14, QFont.Weight.Bold))

        self.run_btn = QPushButton("Run")
        self.run_btn.setFixedHeight(30)
        self.run_btn.setMinimumWidth(100)
        self.run_btn.setStyleSheet(
            "QPushButton { background:#0078d4; color:white; border:1px solid #005fa3;"
            " font-weight:bold; font-size:13px; }"
            "QPushButton:hover { background:#106ebe; }"
            "QPushButton:disabled { background:#b0b0b0; color:#707070; border:1px solid #999; }"
        )
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(8)
        self.progress.setVisible(False)

        self.status_lbl = QLabel("Ready")
        self.status_lbl.setStyleSheet("color: #444; font-size: 11px;")

        top.addWidget(title)
        top.addStretch()
        top.addWidget(self.status_lbl)
        top.addWidget(self.run_btn)
        main_layout.addLayout(top)
        main_layout.addWidget(self.progress)

        # ── Splitter ─────────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
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
        self.tab_waveform = PlotTab()
        self.tab_voltage  = VoltageCalcTab()
        self.tab_magnet   = MagnetCalcTab()

        self.tabs.addTab(self.tab_dose2d,    "Dose Map (2D)")
        self.tabs.addTab(self.tab_dose3d,    "Dose Surface (3D)")
        self.tabs.addTab(self.tab_velocity,  "Velocity Profile")
        self.tabs.addTab(self.tab_dwell,     "Dwell Distribution")
        self.tabs.addTab(self.tab_traj,      "Trajectory")
        self.tabs.addTab(self.tab_waveform,  "Waveform Comparison")
        self.tabs.addTab(self.tab_voltage,   "Voltage Calculator")
        self.tabs.addTab(self.tab_magnet,    "Magnet Calculator")

        # Signals
        self.run_btn.clicked.connect(self.run)

        # Auto-run on start
        QTimer.singleShot(200, self.run)

    # ── Close ─────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        super().closeEvent(event)

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
        self.tab_dose2d.set_metrics([
            ("Flatness", f"{metrics['flatness_pct']:.2f}%"),
            ("RMS dev", f"{metrics['rms_pct']:.2f}%"),
            ("Max/Min", f"{metrics['max_min_ratio']:.3f}"),
            ("Pinch", f"{metrics['pinch_pct']:.2f}%"),
        ])

        self.tab_dose3d.set_figure(plot_dose_3d(dose, xe, ye, aperture, metrics))
        self.tab_dose3d.set_metrics([
            ("Flatness", f"{metrics['flatness_pct']:.2f}%"),
            ("Pinch", f"{metrics['pinch_pct']:.2f}%"),
            ("RMS dev", f"{metrics['rms_pct']:.2f}%"),
        ])

        self.tab_velocity.set_figure(plot_velocity_profile(params, t_arr, x_arr, y_arr))
        self.tab_velocity.set_metrics([
            ("Slew margin", f"{metrics['slew_margin_pct']:+.1f}%"),
            ("Slew limited", str(metrics['slew_limited'])),
            ("FWHM/spot pass", str(metrics['fwhm_spot_pass'])),
            ("Spot spacing", f"{metrics['spot_spacing_mm']:.3f} mm"),
            ("Triangularity", f"{metrics['triangularity']:.3f}"),
        ])

        mask = _aperture_mask_from_edges(xe, ye, *aperture)
        self.tab_dwell.set_figure(plot_dwell_hist(rho, mask))
        self.tab_dwell.set_metrics([
            ("Dwell mean", f"{metrics['dwell_mean']:.3e} s/bin"),
            ("Dwell std", f"{metrics['dwell_std']:.3e} s/bin"),
            ("Peak/min ratio", f"{metrics['dwell_peak_min_ratio']:.3f}"),
            ("Max off-time", f"{metrics['max_pixel_off_time_ms']:.3f} ms"),
        ])

        self.tab_traj.set_figure(self._make_trajectory_fig(x_arr, y_arr, t_arr, aperture, params))
        self.tab_waveform.set_figure(plot_waveform_comparison(params, n_cycles=3))

        flat  = metrics["flatness_pct"]
        color = "#2ca02c" if flat <= 10 else ("#d62728" if flat > 30 else "#ff7f0e")
        self.status_lbl.setText(
            f"Done — flatness: <span style='color:{color};font-weight:bold'>"
            f"{flat:.1f}%</span>  pinch: {metrics['pinch_pct']:.1f}%"
        )
        self.status_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.run_btn.setEnabled(True)
        self.progress.setVisible(False)
        self._last_result = result

    def _on_error(self, msg):
        self.run_btn.setEnabled(True)
        self.progress.setVisible(False)
        self.status_lbl.setText(f"Error: {msg}")
        QMessageBox.critical(self, "Compute Error", msg)

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
        ax.set_facecolor("#f0f0f0")
        fig.patch.set_facecolor("#e0e0e0")
        fig.tight_layout()
        return fig


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Light gray palette matching TDS-T8's functional style
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,          QColor(220, 220, 220))
    pal.setColor(QPalette.ColorRole.WindowText,      QColor(20,  20,  20))
    pal.setColor(QPalette.ColorRole.Base,            QColor(245, 245, 245))
    pal.setColor(QPalette.ColorRole.AlternateBase,   QColor(210, 210, 210))
    pal.setColor(QPalette.ColorRole.ToolTipBase,     QColor(255, 255, 220))
    pal.setColor(QPalette.ColorRole.ToolTipText,     QColor(20,  20,  20))
    pal.setColor(QPalette.ColorRole.Text,            QColor(20,  20,  20))
    pal.setColor(QPalette.ColorRole.Button,          QColor(200, 200, 200))
    pal.setColor(QPalette.ColorRole.ButtonText,      QColor(20,  20,  20))
    pal.setColor(QPalette.ColorRole.BrightText,      QColor(180, 0,   0))
    pal.setColor(QPalette.ColorRole.Link,            QColor(0,   80,  180))
    pal.setColor(QPalette.ColorRole.Highlight,       QColor(0,   120, 215))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    app.setPalette(pal)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
