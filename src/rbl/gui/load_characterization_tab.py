"""
load_characterization_tab.py
PySide6 widget for the "Load Characterization" outer tab (Phase 1).

Measures per-channel load capacitance/leakage on demand instead of relying
on an assumed number. Three modes (rbl/services/load_characterizer.py):
impedance sweep (A), DC leakage ladder (B), charge-integral voltage ladder (C).
Every run is kept as a characterization result (rbl/config/
characterization_history.py): never overwritten, tagged with the amplifier's
serial, the plate position, the load condition and the time.

THE SPIKES PANEL
----------------
Every current spike the spike recorder logs, as one marker on a log-log chart of
peak current against duration, with the amplifier's own ratings drawn as zones:
above 100 mA is over the burst rating, and above 20 mA for longer than 4 ms is
beyond what a burst may last. Colour is the plate; a HOLLOW marker is a peak that
is only a lower bound (the spike was shorter than the monitor and sampling can
resolve, so its true peak was at least that high). The chart shows the current
run live; "Open spike file..." shows a past run's spikes.csv instead, and live
spikes then keep accumulating out of sight until "Show live" brings them back,
so a past run is never mixed with the present one.

THE AMPLIFIERS PANEL
--------------------
Every measurement belongs to the AMPLIFIER that produced it, not to the plate it
happened to be plugged into, so the operator records which serial drives which
plate ("Record amplifier swap": the first recording is the initial assignment,
later ones are swaps) and any hardware change ("Record hardware change": a new
cable, a cleaned feedthrough). Both go to rbl/config/amplifier_assignments.py,
append-only. The dialogs sit behind two replaceable methods (`_ask_assignment`,
`_ask_hardware_change`) so tests never open a modal.

THE TABLE
---------
One row per plate position, grouped by axis (X+ and X- together, Y+ and Y-
together) so the two plates of an axis can be compared at a glance. Each load
condition shows the NEWEST result for the amplifier currently assigned to that
plate, with its method and age ("1612 pF, charge_integral_ladder, 32 days ago"),
or "not measured": no number is ever substituted. The last two columns are the
differences between conditions - cable minus amplifier alone, plates minus
cable - which is how the ~1.6 nF is traced to the amplifier, the cable or the
plates. A result measured before the latest hardware change is shown in amber
with the date of that change, because it may no longer describe the load.
A result whose current-monitor scale is missing or differs from the scale in force
is shown in amber as "wrong monitor scale - remeasure".

A sibling tab to calibration_tab.py, not a sub-panel of it (per the plan's
own "the user's call") — calibration_tab.py's own docstring already
documents that file as busy, and this feature has an independent run
lifecycle (its own runner, its own profile/pair requests) that does not
share state with a calibration sweep.
"""
import csv
import logging
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QDateTime, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rbl.config import amplifier_assignments, characterization_history
from rbl.config.calibration_config import LoadCondition
from rbl.config.hardware_config import AMP_LABELS
from rbl.gui import theme
from rbl.gui.widgets.connection_bar import LabJackPanel
from rbl.gui.widgets.inputs import NoScrollComboBox
from rbl.services.load_characterizer import CLAMP_RATIO_THRESHOLD, LoadCharacterizer, Mode
from rbl.services.ramp_engine import RampEngine

log = logging.getLogger(__name__)

# The amplifier's ratings, drawn on the spike chart (mA and s).
CONTINUOUS_RATING_MA = 20.0
BURST_RATING_MA = 100.0
BURST_LIMIT_S = 4e-3
_SPIKE_LIVE_CAP = 5000       # live markers kept; a storm must not make redraws crawl

TABLE_HEADERS = ["Plate position", "Disconnected", "Cable only", "On plates",
                 "Cable minus amplifier", "Plates minus cable"]
COL_CABLE_MINUS_AMP = 4
COL_PLATES_MINUS_CABLE = 5
# Which load condition each of the first three result columns shows.
_CONDITION_COLUMNS = {1: "DISCONNECTED", 2: "CABLE_ONLY", 3: "ON_PLATES"}
# X+ and X- are the push-pull pair on one axis, Y+ and Y- the other.
_TABLE_ROWS = [("axis", "X axis"), ("plate", "X+"), ("plate", "X-"),
               ("axis", "Y axis"), ("plate", "Y+"), ("plate", "Y-")]


def _qdatetime_from(when: datetime) -> QDateTime:
    """A QDateTime showing `when` in local time (the dialogs' default of 'now')."""
    return QDateTime.fromSecsSinceEpoch(int(when.timestamp()))


class AssignmentDialog(QDialog):
    """One serial-number field per plate position, a date and a note.

    OK is enabled only when all four serials are filled in and distinct: one
    physical amplifier cannot drive two plates.
    """

    def __init__(self, current: dict | None, now: datetime, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Record amplifier swap")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.serial_edits = {}
        for plate in AMP_LABELS:
            edit = QLineEdit((current or {}).get(plate, ""))
            edit.setPlaceholderText("amplifier serial number")
            edit.textChanged.connect(self._refresh_ok)
            self.serial_edits[plate] = edit
            form.addRow(f"{plate}:", edit)
        self.when_edit = QDateTimeEdit(_qdatetime_from(now))
        self.when_edit.setCalendarPopup(True)
        self.when_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        form.addRow("Date:", self.when_edit)
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("optional note")
        form.addRow("Note:", self.note_edit)
        layout.addLayout(form)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._refresh_ok()

    def _refresh_ok(self, *_):
        serials = [e.text().strip() for e in self.serial_edits.values()]
        ok = all(serials) and len(set(serials)) == len(serials)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)

    def answer(self):
        """(mapping, when, note) as the panel records them."""
        mapping = {plate: e.text().strip() for plate, e in self.serial_edits.items()}
        return mapping, self.when_edit.dateTime().toPython(), self.note_edit.text().strip()


class HardwareChangeDialog(QDialog):
    """A date and a note. The note is required: a hardware change with no
    description cannot be told apart from another one in six months."""

    def __init__(self, now: datetime, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Record hardware change")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.when_edit = QDateTimeEdit(_qdatetime_from(now))
        self.when_edit.setCalendarPopup(True)
        self.when_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        form.addRow("Date:", self.when_edit)
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("e.g. new HV cable, feedthrough cleaned")
        self.note_edit.textChanged.connect(self._refresh_ok)
        form.addRow("What changed:", self.note_edit)
        layout.addLayout(form)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._refresh_ok()

    def _refresh_ok(self, *_):
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            bool(self.note_edit.text().strip()))

    def answer(self):
        """(when, note) as the panel records them."""
        return self.when_edit.dateTime().toPython(), self.note_edit.text().strip()


class LoadCharacterizationTab(QWidget):
    """The 'Load Characterization' outer tab: Modes A/B/C, live plot, and
    the per-plate comparison table."""

    profile_change_requested = Signal(str)
    pair_profile_requested   = Signal(str, str)   # (profile_name, amp_label)
    run_state_changed        = Signal(bool)
    # A run has written a new characterization result.  Anything planning from
    # the results (the Raster Planner) has to be told: it reads files, and a
    # file does not emit anything when it changes.  Wired in app.py.
    measurements_changed     = Signal()

    def __init__(self, beamline, parent=None, now_fn=None):
        super().__init__(parent)
        self.beamline = beamline
        # Injected so the ages shown are testable.
        self._now_fn = now_fn or (lambda: datetime.now().astimezone())
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
        self.rb_mode_clamp = QRadioButton("Clamp test")
        self.rb_mode_a.setChecked(True)
        self._mode_group = QButtonGroup(self)
        for rb in (self.rb_mode_a, self.rb_mode_b, self.rb_mode_c, self.rb_mode_clamp):
            self._mode_group.addButton(rb)
            mode_row.addWidget(rb)
        cfg_form.addRow("Mode:", mode_row)

        # Section 2.4: tagging every result with the load condition, and
        # letting DISCONNECTED vs ON_PLATES be compared, is first-class.
        cond_row = QHBoxLayout()
        self.rb_disconnected = QRadioButton("DISCONNECTED")
        self.rb_on_plates = QRadioButton("ON_PLATES")
        self.rb_cable_only = QRadioButton("Cable only (far end open)")
        self.rb_on_plates.setChecked(True)
        self._cond_group = QButtonGroup(self)
        for rb in (self.rb_disconnected, self.rb_on_plates,
                   self.rb_cable_only):
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

        # ── Amplifiers: which serial drives which plate ───────────────────────
        amp_box = QGroupBox("Amplifiers")
        amp_form = QFormLayout(amp_box)
        self.lbl_serials = {}
        for plate in AMP_LABELS:
            lbl = QLabel("not set")
            self.lbl_serials[plate] = lbl
            amp_form.addRow(f"{plate}:", lbl)
        amp_btns = QHBoxLayout()
        self.btn_record_swap = QPushButton("Record amplifier swap")
        self.btn_record_swap.clicked.connect(self._on_record_swap)
        self.btn_record_hw_change = QPushButton("Record hardware change")
        self.btn_record_hw_change.clicked.connect(self._on_record_hardware_change)
        amp_btns.addWidget(self.btn_record_swap)
        amp_btns.addWidget(self.btn_record_hw_change)
        amp_form.addRow(amp_btns)
        left_col.addWidget(amp_box)
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

        # ── Spikes: peak current vs duration, live or from a past run ─────────
        spike_box = QGroupBox("Spikes")
        spike_lay = QVBoxLayout(spike_box)
        spike_btns = QHBoxLayout()
        self.btn_open_spikes = QPushButton("Open spike file...")
        self.btn_open_spikes.clicked.connect(self._on_open_spike_file)
        self.btn_show_live = QPushButton("Show live")
        self.btn_show_live.clicked.connect(self._on_show_live_spikes)
        self.btn_show_live.setEnabled(False)
        self.lbl_spikes = QLabel("")
        self.lbl_spikes.setWordWrap(True)
        spike_btns.addWidget(self.btn_open_spikes)
        spike_btns.addWidget(self.btn_show_live)
        spike_btns.addWidget(self.lbl_spikes, stretch=1)
        spike_lay.addLayout(spike_btns)
        self._fig_spikes = Figure(figsize=(6, 3))
        self._canvas_spikes = FigureCanvasQTAgg(self._fig_spikes)
        self._canvas_spikes.setMinimumHeight(260)
        self._canvas_spikes.setSizePolicy(QSizePolicy.Policy.Expanding,
                                          QSizePolicy.Policy.Expanding)
        self._ax_spikes = self._fig_spikes.add_subplot(111)
        spike_lay.addWidget(self._canvas_spikes)
        layout.addWidget(spike_box)
        self._live_spikes: list = []
        self._file_spikes: list | None = None      # None = showing live
        self._file_name = ""
        self._file_unreadable = 0
        self._redraw_spikes()

        # ── Per-plate comparison table, grouped by axis ──────────────────────
        table_box = QGroupBox("Load by plate position (newest result per condition)")
        table_lay = QVBoxLayout(table_box)
        self.table = QTableWidget(len(_TABLE_ROWS), len(TABLE_HEADERS))
        self.table.setHorizontalHeaderLabels(TABLE_HEADERS)
        for row, (kind, label) in enumerate(_TABLE_ROWS):
            item = QTableWidgetItem(label)
            self.table.setItem(row, 0, item)
            if kind == "axis":
                self.table.setSpan(row, 0, 1, len(TABLE_HEADERS))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
        table_lay.addWidget(self.table)
        layout.addWidget(table_box)

        self.refresh_results()

    # ------------------------------------------------------------------
    # Run control
    # ------------------------------------------------------------------

    def _selected_mode(self) -> Mode:
        if self.rb_mode_a.isChecked():
            return Mode.A
        if self.rb_mode_b.isChecked():
            return Mode.B
        if self.rb_mode_clamp.isChecked():
            return Mode.CLAMP
        return Mode.C

    def _selected_load_condition(self) -> LoadCondition:
        if self.rb_on_plates.isChecked():
            return LoadCondition.ON_PLATES
        if self.rb_cable_only.isChecked():
            return LoadCondition.CABLE_ONLY
        return LoadCondition.DISCONNECTED

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
        elif mode == Mode.CLAMP:
            self._runner.start_clamp_test(amp_label)
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
        rule = getattr(self._runner, "abort_rule", None)
        clamp = getattr(self._runner, "clamp_result", None)
        if clamp is not None and not rule:
            if clamp["reached"]:
                if clamp.get("at_rail"):
                    current_str = "the current reached the monitor's rail (at least 20 mA)"
                else:
                    current_str = f"{clamp['clamp_ma']:.1f} mA"
                self.lbl_status.setText(
                    f"Clamp test: the output stopped following at {clamp['clamp_freq_hz']:.0f} Hz, "
                    f"{current_str} (at {clamp['peak_kv']:.1f} kV peak).")
            else:
                self.lbl_status.setText(
                    "Clamp test: not reached - the output followed its input up to "
                    "the top of the frequency ladder.")
        elif rule:
            # Which rule stopped it is a finding, not just "aborted".
            self.lbl_status.setText(f"Stopped by rule '{rule}'"
                                    + (f" (CSV: {csv_path})" if csv_path else "."))
        else:
            self.lbl_status.setText(
                f"Finished: {csv_path}" if csv_path else "Finished (aborted).")
        if self._prior_profile:
            self.profile_change_requested.emit(self._prior_profile)
        self.refresh_results()
        self.measurements_changed.emit()

    def _on_characterizer_error(self, msg: str):
        QMessageBox.warning(self, "Load characterization error", msg)

    def _set_running(self, running: bool):
        self.btn_run.setEnabled(not running)
        self.btn_abort.setEnabled(running)
        self.cb_channel.setEnabled(not running)
        for rb in (self.rb_mode_a, self.rb_mode_b, self.rb_mode_c, self.rb_mode_clamp,
                   self.rb_disconnected, self.rb_on_plates, self.rb_cable_only):
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
        elif mode == Mode.CLAMP:
            freqs = [p["freq_hz"] for p in self._plot_points]
            ratios = [p["regulation_ratio"] for p in self._plot_points]
            self._ax1.plot(freqs, ratios, "o-", color="tab:purple")
            self._ax1.axhline(CLAMP_RATIO_THRESHOLD, color=theme.FAULT, linestyle=":")
            self._ax1.set_xlabel("Frequency (Hz)")
            self._ax1.set_ylabel("Output / command (fundamental)")
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
    # Per-plate comparison table
    # ------------------------------------------------------------------

    def refresh_results(self):
        """Re-read the amplifier assignment and characterization results and
        redraw the panel and the table."""
        now = self._now_fn()
        in_force = amplifier_assignments.current_assignment(now) or {}
        for plate, lbl in self.lbl_serials.items():
            lbl.setText(in_force.get(plate, "not set"))
        hw_change = amplifier_assignments.latest_hardware_change(now)
        stale_tip = ("" if hw_change is None else
                     f"measured before the hardware change on {hw_change:%Y-%m-%d}")
        clear = QColor(0, 0, 0, 0)
        on_plates_g: dict = {}

        for row, (kind, label) in enumerate(_TABLE_ROWS):
            if kind == "axis":
                continue
            found: dict = {}
            for col, condition in _CONDITION_COLUMNS.items():
                rec = characterization_history.newest_with_capacitance(
                    label, condition, now)
                found[condition] = rec
                if rec is None:
                    wrong = characterization_history.newest_wrong_scale(
                        label, condition, now)
                    if wrong is not None:
                        item = QTableWidgetItem("wrong monitor scale - remeasure")
                        item.setBackground(QColor(theme.WARN))
                    else:
                        item = QTableWidgetItem("not measured")
                        item.setBackground(clear)
                else:
                    item = QTableWidgetItem(self._result_text(rec))
                    item.setBackground(clear)
                    if rec["predates_hardware_change"]:
                        item.setBackground(QColor(theme.WARN))
                        item.setToolTip(stale_tip)
                self.table.setItem(row, col, item)

            c = {cond: (rec["values"]["c_pf"] if rec else None)
                 for cond, rec in found.items()}
            self.table.setItem(row, COL_CABLE_MINUS_AMP, QTableWidgetItem(
                self._difference_text(c["CABLE_ONLY"], c["DISCONNECTED"])))
            self.table.setItem(row, COL_PLATES_MINUS_CABLE, QTableWidgetItem(
                self._difference_text(c["ON_PLATES"], c["CABLE_ONLY"])))
            rec = found["ON_PLATES"]
            on_plates_g[row] = None if rec is None else rec["values"].get("g_us")

        # Outlier highlight: a plate whose conductance is notably higher than
        # the others' is direct evidence of a leakage path (Section 2.6) - the
        # diagnostic this table exists for. Judged on the on-plates results only:
        # the load a run actually drives.
        finite = sorted(g for g in on_plates_g.values() if g is not None)
        median_floor = max(finite[len(finite) // 2], 1e-9) if len(finite) >= 2 else None
        for row, g in on_plates_g.items():
            outlier = (median_floor is not None and g is not None
                       and g > 3 * median_floor)
            first = self.table.item(row, 0)
            first.setBackground(QColor(theme.FAULT) if outlier else clear)

    # ------------------------------------------------------------------
    # Spikes panel
    # ------------------------------------------------------------------

    @property
    def spike_axes(self):
        """The spike chart's matplotlib axes (for tests)."""
        return self._ax_spikes

    def set_spike_recorder(self, recorder) -> None:
        """Plot every spike the recorder logs, as it happens."""
        recorder.spike_recorded.connect(self.add_spike)

    def add_spike(self, row: dict) -> None:
        """One live spike (a row as `SpikeRecorder.spike_recorded` emits it)."""
        spike = self._parse_spike(row)
        if spike is None:
            return
        self._live_spikes.append(spike)
        del self._live_spikes[:-_SPIKE_LIVE_CAP]
        if self._file_spikes is None:           # not paused on a past file
            self._redraw_spikes()

    @staticmethod
    def _parse_spike(row: dict):
        """(plate, duration_s, peak_ma, lower_bound) from a recorder or CSV row."""
        try:
            plate = row["plate_position"]
            if plate not in AMP_LABELS:
                return None
            duration = float(row["duration_s"])
            peak = float(row["peak_ma"])
            lower = row["peak_is_lower_bound"]
            if not isinstance(lower, bool):
                lower = str(lower).strip().lower() in ("true", "1")
            if not (duration > 0 and peak > 0):          # log axes cannot show these
                return None
            return plate, duration, peak, lower
        except (KeyError, TypeError, ValueError):
            return None

    def _ask_spike_file(self):
        """-> Path or None. The only place the file dialog opens."""
        name, _ = QFileDialog.getOpenFileName(
            self, "Open spike file", "", "Spike files (spikes.csv *.csv);;All files (*)")
        return Path(name) if name else None

    def _on_open_spike_file(self):
        path = self._ask_spike_file()
        if path is None:
            return
        spikes, unreadable = [], 0
        try:
            with open(path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    spike = self._parse_spike(row)
                    if spike is None:
                        unreadable += 1
                    else:
                        spikes.append(spike)
        except OSError as e:
            QMessageBox.warning(self, "Spike file not opened", str(e))
            return
        self._file_spikes = spikes
        self._file_name = Path(path).parent.name + "/" + Path(path).name
        self._file_unreadable = unreadable
        self.btn_show_live.setEnabled(True)
        self._redraw_spikes()

    def _on_show_live_spikes(self):
        self._file_spikes = None
        self.btn_show_live.setEnabled(False)
        self._redraw_spikes()

    def _redraw_spikes(self):
        showing_file = self._file_spikes is not None
        spikes = self._file_spikes if showing_file else self._live_spikes
        ax = self._ax_spikes
        ax.clear()

        # Limits first: a log scale on an axes with no positive data raises.
        durations = [s[1] for s in spikes]
        peaks = [s[2] for s in spikes]
        x_lo = min([5e-5, *durations]) / 2
        x_hi = max([10.0, *durations]) * 2
        y_lo = min([1.0, *peaks]) / 2
        y_hi = max([BURST_RATING_MA * 3, *peaks]) * 2
        ax.set_xlim(x_lo, x_hi)
        ax.set_ylim(y_lo, y_hi)
        ax.set_xscale("log")
        ax.set_yscale("log")

        ax.fill_between([x_lo, x_hi], [BURST_RATING_MA] * 2, [y_hi] * 2,
                        color=theme.FAULT, alpha=0.12, label="over burst rating")
        ax.fill_between([BURST_LIMIT_S, x_hi], [CONTINUOUS_RATING_MA] * 2, [y_hi] * 2,
                        color=theme.WARN, alpha=0.12, label="beyond 4 ms burst")
        ax.axhline(CONTINUOUS_RATING_MA, color=theme.WARN, linestyle="-", linewidth=1.0)
        ax.axhline(BURST_RATING_MA, color=theme.FAULT, linestyle=":", linewidth=1.0)
        ax.text(x_lo * 1.2, CONTINUOUS_RATING_MA * 1.08, "20 mA continuous", fontsize=7,
                color=theme.WARN, va="bottom")
        ax.text(x_lo * 1.2, BURST_RATING_MA * 1.08, "100 mA burst", fontsize=7,
                color=theme.FAULT, va="bottom")
        ax.axvline(BURST_LIMIT_S, color=theme.NEUTRAL, linestyle="--", linewidth=0.8)

        counts = {plate: 0 for plate in AMP_LABELS}
        for i, plate in enumerate(AMP_LABELS):
            mine = [s for s in spikes if s[0] == plate]
            counts[plate] = len(mine)
            if not mine:
                continue
            colour = f"C{i}"
            ax.scatter([s[1] for s in mine], [s[2] for s in mine],
                       s=36, edgecolors=colour, linewidths=1.4,
                       facecolors=["none" if s[3] else colour for s in mine],
                       label=plate, zorder=3)
        ax.set_xlabel("Spike duration (s)")
        ax.set_ylabel("Peak current (mA)")
        ax.grid(True, which="both", alpha=0.25)
        if any(counts.values()):
            ax.legend(fontsize="x-small", loc="lower left")
        self._fig_spikes.tight_layout()
        self._canvas_spikes.draw_idle()

        per_plate = "   ".join(f"{plate} {n}" for plate, n in counts.items())
        if showing_file:
            note = f"{self._file_name}: {per_plate}"
            if self._file_unreadable:
                note += (f"   ({self._file_unreadable} unreadable row(s) skipped; "
                         f"live spikes paused)")
            else:
                note += "   (live spikes paused)"
        else:
            note = f"live: {per_plate}"
        self.lbl_spikes.setText(note)

    # ------------------------------------------------------------------
    # Amplifiers panel
    # ------------------------------------------------------------------

    def _ask_assignment(self, current: dict | None):
        """-> (mapping, when, note) or None. The only place the swap dialog opens."""
        dlg = AssignmentDialog(current, self._now_fn(), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return dlg.answer()

    def _ask_hardware_change(self):
        """-> (when, note) or None. The only place the hardware-change dialog opens."""
        dlg = HardwareChangeDialog(self._now_fn(), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return dlg.answer()

    def _on_record_swap(self):
        current = amplifier_assignments.current_assignment(self._now_fn())
        answer = self._ask_assignment(current)
        if answer is None:
            return
        mapping, when, note = answer
        # The first recording establishes the assignment; later ones are swaps.
        has_one = any(r.get("kind") in ("initial", "swap")
                      for r in amplifier_assignments.history())
        try:
            amplifier_assignments.record_assignment(
                mapping, when, "swap" if has_one else "initial", note)
        except ValueError as e:
            QMessageBox.warning(self, "Amplifier assignment not recorded", str(e))
            return
        self.refresh_results()
        # The planner reads the newest result for the amplifier now at each plate.
        self.measurements_changed.emit()

    def _on_record_hardware_change(self):
        answer = self._ask_hardware_change()
        if answer is None:
            return
        when, note = answer
        amplifier_assignments.record_hardware_change(when, note)
        self.refresh_results()
        self.measurements_changed.emit()

    @staticmethod
    def _result_text(rec: dict) -> str:
        return (f"{rec['values']['c_pf']:.0f} pF, {rec['method']}, "
                f"{characterization_history.age_text(rec['age_s'])}")

    @staticmethod
    def _difference_text(minuend, subtrahend) -> str:
        if minuend is None or subtrahend is None:
            return ""
        return f"{minuend - subtrahend:.0f} pF"

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
