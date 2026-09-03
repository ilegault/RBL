"""
load_characterization_tab.py
PySide6 widget for the "Load Characterization" outer tab (Phase 1).

Measures per-channel load capacitance/leakage on demand instead of relying
on `calibration_config.CAL_LOAD_CAP_PF`'s one-off analysis. Three modes
(rbl/services/load_characterizer.py): impedance sweep (A), DC leakage
ladder (B), charge-integral cross-check (C). Results persist to
rbl/config/load_calibration_store.py and feed back into
`calibration_config.ac_peak_current_ma()`/`ac_max_peak_kv()` for future
runs — see that module's docstring for the fallback chain.

A sibling tab to calibration_tab.py, not a sub-panel of it (per the plan's
own "the user's call") — calibration_tab.py's own docstring already
documents that file as busy, and this feature has an independent run
lifecycle (its own runner, its own profile/pair requests) that does not
share state with a calibration sweep.
"""
import matplotlib

matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rbl.config.calibration_config import LoadCondition
from rbl.config.hardware_config import AMP_LABELS
from rbl.config.load_calibration_store import load_all
from rbl.gui import theme
from rbl.gui.widgets.connection_bar import LabJackPanel
from rbl.gui.widgets.inputs import NoScrollComboBox
from rbl.services.load_characterizer import LoadCharacterizer, Mode
from rbl.services.ramp_engine import RampEngine


class LoadCharacterizationTab(QWidget):
    """The 'Load Characterization' outer tab: Modes A/B/C, live plot, and
    the four-channel comparison table."""

    profile_change_requested = Signal(str)
    pair_profile_requested   = Signal(str, str)   # (profile_name, amp_label)
    run_state_changed        = Signal(bool)
    # A run has written a new per-channel measurement to
    # load_calibration_store.  Anything planning from that store (the Raster
    # Planner) has to be told: it reads the file, and a file does not emit
    # anything when it changes.  Wired in app.py.
    measurements_changed     = Signal()

    def __init__(self, beamline, parent=None):
        super().__init__(parent)
        self.beamline = beamline
        self._runner: LoadCharacterizer = None
        self._ramp_engine = None
        self._connected = False
        self._current_profile = None
        self._prior_profile = None
        self._plot_points: list = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        left_col = QVBoxLayout()
        left_col.setSpacing(8)

        self.lj_panel = LabJackPanel()
        left_col.addWidget(self.lj_panel)

        cfg_box = QGroupBox("Load Characterization")
        cfg_form = QFormLayout(cfg_box)

        self.cb_channel = NoScrollComboBox()
        self.cb_channel.addItems(AMP_LABELS)
        cfg_form.addRow("Channel:", self.cb_channel)

        mode_row = QHBoxLayout()
        self.rb_mode_a = QRadioButton("A: Impedance sweep")
        self.rb_mode_b = QRadioButton("B: DC leakage vs. voltage")
        self.rb_mode_c = QRadioButton("C: Charge integral")
        self.rb_mode_a.setChecked(True)
        self._mode_group = QButtonGroup(self)
        for rb in (self.rb_mode_a, self.rb_mode_b, self.rb_mode_c):
            self._mode_group.addButton(rb)
            mode_row.addWidget(rb)
        cfg_form.addRow("Mode:", mode_row)

        # Section 2.4: tagging every result with the load condition, and
        # letting DISCONNECTED vs ON_PLATES be compared, is first-class.
        cond_row = QHBoxLayout()
        self.rb_disconnected = QRadioButton("DISCONNECTED")
        self.rb_on_plates = QRadioButton("ON_PLATES")
        self.rb_on_plates.setChecked(True)
        self._cond_group = QButtonGroup(self)
        for rb in (self.rb_disconnected, self.rb_on_plates):
            self._cond_group.addButton(rb)
            cond_row.addWidget(rb)
        cfg_form.addRow("Load condition:", cond_row)

        btn_row = QHBoxLayout()
        self.btn_run = QPushButton("Run")
        self.btn_run.clicked.connect(self._on_run_clicked)
        self.btn_abort = QPushButton("Abort")
        self.btn_abort.setEnabled(False)
        self.btn_abort.clicked.connect(self._on_abort_clicked)
        btn_row.addWidget(self.btn_run)
        btn_row.addWidget(self.btn_abort)
        cfg_form.addRow(btn_row)

        self.lbl_status = QLabel("Idle")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet(f"color: {theme.NEUTRAL};")
        cfg_form.addRow("Status:", self.lbl_status)

        left_col.addWidget(cfg_box)
        left_col.addStretch()
        top_row.addLayout(left_col, stretch=0)

        # ── Live plot ──────────────────────────────────────────────────────
        plot_box = QGroupBox("Live Result")
        plot_lay = QVBoxLayout(plot_box)
        self._fig = Figure(figsize=(6, 4))
        self._canvas = FigureCanvasQTAgg(self._fig)
        self._canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._ax1 = self._fig.add_subplot(111)
        self._ax2 = self._ax1.twinx()
        self._fig.tight_layout()
        plot_lay.addWidget(self._canvas)
        top_row.addWidget(plot_box, stretch=1)

        layout.addLayout(top_row, stretch=1)

        # ── Four-channel comparison table (Section 2.6) ──────────────────────
        table_box = QGroupBox("Four-Channel Comparison")
        table_lay = QVBoxLayout(table_box)
        self.table = QTableWidget(len(AMP_LABELS), 5)
        self.table.setHorizontalHeaderLabels(
            ["Channel", "C (pF)", "G (uS)", "tan(delta)", "Load condition shown"])
        for row, label in enumerate(AMP_LABELS):
            self.table.setItem(row, 0, QTableWidgetItem(label))
        table_lay.addWidget(self.table)
        layout.addWidget(table_box)

        self._refresh_table()

    # ------------------------------------------------------------------
    # Run control
    # ------------------------------------------------------------------

    def _selected_mode(self) -> Mode:
        if self.rb_mode_a.isChecked():
            return Mode.A
        if self.rb_mode_b.isChecked():
            return Mode.B
        return Mode.C

    def _selected_load_condition(self) -> LoadCondition:
        return (LoadCondition.ON_PLATES if self.rb_on_plates.isChecked()
                else LoadCondition.DISCONNECTED)

    def _on_run_clicked(self):
        if not self._connected:
            QMessageBox.warning(self, "Not connected", "Connect the LabJack T7 first.")
            return
        amp_label = self.cb_channel.currentText()
        mode = self._selected_mode()
        load_condition = self._selected_load_condition()

        funcgen_map = self.beamline.build_funcgen_map()
        if funcgen_map.get(amp_label, (None, None))[0] is None:
            QMessageBox.warning(self, "Generator not connected",
                                 f"{amp_label}'s function generator is not connected.")
            return

        self._ramp_engine = RampEngine(funcgen_map)
        self._runner = LoadCharacterizer(
            funcgen_map, load_condition, ramp_engine=self._ramp_engine,
            pressure_provider=self._read_pressure)
        self._runner.point_measured.connect(self._on_point_measured)
        self._runner.finished.connect(self._on_finished)
        self._runner.error.connect(self._on_characterizer_error)
        self._runner.progress.connect(self._on_progress)

        self._plot_points = []
        self._prior_profile = self._current_profile
        # Every mode drives one channel and needs both its monitors —
        # AMP_PAIR gives the highest per-channel sample density for that.
        self.pair_profile_requested.emit("AMP_PAIR", amp_label)

        self._set_running(True)
        if mode == Mode.A:
            self._runner.start_mode_a(amp_label)
        elif mode == Mode.B:
            self._runner.start_mode_b(amp_label)
        else:
            self._runner.start_mode_c(amp_label)

    def _read_pressure(self) -> float:
        return getattr(self.beamline, "_hv_pressure_torr", float("nan"))

    def _on_abort_clicked(self):
        if self._runner is not None:
            self._runner.abort()

    def _on_progress(self, done: int, total: int, label: str):
        self.lbl_status.setText(label)

    def _on_point_measured(self, point: dict):
        self._plot_points.append(point)
        self._redraw_plot()

    def _on_finished(self, csv_path: str):
        self._set_running(False)
        self.lbl_status.setText(f"Finished: {csv_path}" if csv_path else "Finished (aborted).")
        if self._prior_profile:
            self.profile_change_requested.emit(self._prior_profile)
        self._refresh_table()
        self.measurements_changed.emit()

    def _on_characterizer_error(self, msg: str):
        QMessageBox.warning(self, "Load characterization error", msg)

    def _set_running(self, running: bool):
        self.btn_run.setEnabled(not running)
        self.btn_abort.setEnabled(running)
        self.cb_channel.setEnabled(not running)
        for rb in (self.rb_mode_a, self.rb_mode_b, self.rb_mode_c,
                   self.rb_disconnected, self.rb_on_plates):
            rb.setEnabled(not running)
        self.run_state_changed.emit(running)

    # ------------------------------------------------------------------
    # Plotting — Mode A: C and G vs frequency; Mode B: leakage vs voltage;
    # Mode C: recovered capacitance per point (a cross-check, not a sweep).
    # ------------------------------------------------------------------

    def _redraw_plot(self):
        self._ax1.clear()
        self._ax2.clear()
        self._ax2.set_visible(True)
        mode = self._selected_mode()
        if mode == Mode.A:
            freqs = [p["freq_hz"] for p in self._plot_points]
            c_vals = [p["c_pf"] for p in self._plot_points]
            g_vals = [p["g_us"] for p in self._plot_points]
            self._ax1.plot(freqs, c_vals, "o-", color="tab:blue", label="C (pF)")
            self._ax2.plot(freqs, g_vals, "s--", color="tab:red", label="G (uS)")
            self._ax1.set_xlabel("Frequency (Hz)")
            self._ax1.set_ylabel("C (pF)", color="tab:blue")
            self._ax2.set_ylabel("G (uS)", color="tab:red")
        elif mode == Mode.B:
            kvs = [p["commanded_kv"] for p in self._plot_points]
            leak = [p["leak_ua"] for p in self._plot_points]
            self._ax1.plot(kvs, leak, "o-", color="tab:orange")
            self._ax1.set_xlabel("Commanded voltage (kV)")
            self._ax1.set_ylabel("Leakage (uA)")
            self._ax2.set_visible(False)
        else:
            n = list(range(1, len(self._plot_points) + 1))
            c_vals = [p["c_pf_mean"] for p in self._plot_points]
            self._ax1.plot(n, c_vals, "o-", color="tab:green")
            self._ax1.set_xlabel("Point")
            self._ax1.set_ylabel("C (pF, charge integral)")
            self._ax2.set_visible(False)
        self._ax1.grid(True, alpha=0.3)
        self._fig.tight_layout()
        self._canvas.draw_idle()

    # ------------------------------------------------------------------
    # Four-channel comparison table
    # ------------------------------------------------------------------

    def _refresh_table(self):
        import math
        data = load_all()
        g_values = [None] * len(AMP_LABELS)

        for row, label in enumerate(AMP_LABELS):
            per_channel = data.get(label, {})
            # ON_PLATES is the operationally relevant condition; fall back to
            # whatever else exists so a DISCONNECTED-only channel still shows.
            condition = "ON_PLATES" if "ON_PLATES" in per_channel else (
                "DISCONNECTED" if "DISCONNECTED" in per_channel else None)
            record = per_channel.get(condition) if condition else None

            c_pf = record["c_pf"] if record else None
            g_us = record["g_us"] if record else None
            g_values[row] = g_us
            tan_delta = None
            if record and c_pf and c_pf > 0:
                # tan(delta) at 1 kHz, purely for a single comparable number
                # in this table — the measured loss_tangent from a Mode A
                # sweep already carries the frequency it was measured at.
                tan_delta = (g_us * 1e-6) / (2 * math.pi * 1000.0 * c_pf * 1e-12)

            self.table.setItem(row, 1, QTableWidgetItem("—" if c_pf is None else f"{c_pf:.1f}"))
            self.table.setItem(row, 2, QTableWidgetItem("—" if g_us is None else f"{g_us:.4f}"))
            self.table.setItem(row, 3, QTableWidgetItem("—" if tan_delta is None else f"{tan_delta:.4f}"))
            self.table.setItem(row, 4, QTableWidgetItem(condition or "—"))

        # Outlier highlight: a channel whose conductance is notably higher
        # than the others' is direct evidence of a leakage path (Section 2.6)
        # — the diagnostic this whole table exists for.
        finite = sorted(g for g in g_values if g is not None)
        if len(finite) >= 2:
            median = finite[len(finite) // 2]
            floor = max(median, 1e-9)
            for row, g in enumerate(g_values):
                is_outlier = g is not None and g > 3 * floor
                color = QColor(theme.FAULT) if is_outlier else None
                for col in range(5):
                    item = self.table.item(row, col)
                    if item:
                        item.setBackground(color if color else QColor(0, 0, 0, 0))

    # ------------------------------------------------------------------
    # _lj_tabs contract (see rbl/gui/app.py) — same shape as AmpTab/CalibrationTab.
    # ------------------------------------------------------------------

    def on_labjack_connected(self, serial: str):
        self._connected = True
        self.lj_panel.set_connected(True, serial)

    def on_labjack_disconnected(self):
        self._connected = False
        self.lj_panel.set_connected(False)
        if self._runner is not None:
            self._runner.abort()

    def on_labjack_error(self, msg: str):
        QMessageBox.warning(self, "LabJack poll error", msg)

    def on_profile_changed(self, profile_name: str):
        self._current_profile = profile_name

    def on_window(self, payload: dict):
        """Connected to Beamline.raw_window_ready (see rbl/gui/app.py)."""
        if self._runner is not None:
            self._runner.on_window(payload)

    def shutdown(self):
        if self._runner is not None:
            self._runner.abort()
