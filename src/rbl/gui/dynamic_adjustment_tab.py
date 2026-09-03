"""
dynamic_adjustment_tab.py
PySide6 widget for the "Dynamic Adjustment" outer tab (Phase 8) — the DYNAMIC
ADJ front-panel pot A/B campaign.

WHY THE POT POSITION FIELD IS REQUIRED (Section 8.3/8.5)
------------------------------------------------------------
The pot position is operator-entered and cannot be verified in software —
the app trusts it exactly like `LoadCondition`, a recorded fact it cannot
check. Run is disabled until the field is non-blank, and the position
appears in every stored trial and every plot legend, so a campaign can never
end up with a trial whose position nobody wrote down.

THE CAVEAT (Section 8.1) — STATED IN THE UI, NOT JUST THE DOCSTRING
------------------------------------------------------------------------
At this rig's measured ~1200 pF the load is above the manual's stated 1 nF
compensation threshold, so the pot may not be able to reach a true optimum.
This panel characterizes and optimizes WITHIN the achievable range; its
"winner" is the best position tried, not a claim of correct compensation.
"""
import matplotlib

matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rbl.config.hardware_config import AMP_LABELS
from rbl.gui import theme
from rbl.gui.widgets.connection_bar import LabJackPanel
from rbl.gui.widgets.inputs import NoScrollComboBox
from rbl.services.dynamic_adjustment import DynamicAdjustmentTrial
from rbl.services.dynamic_adjustment_history import (
    append_trial,
    trials_for,
    winner_for,
)

_CAVEAT_TEXT = (
    "Caveat: at this rig's measured ~1200 pF the load is above the EEL5000 "
    "manual's stated 1 nF compensation threshold, above which factory "
    "adjustment is specified. This panel characterizes and optimizes WITHIN "
    "the achievable range with the front-panel pot — its 'winner' is the "
    "best position tried, not a claim that the channel is now well "
    "compensated. If no position gets close to zero overshoot/creep, that "
    "itself is the evidence for a factory-adjustment request."
)


class DynamicAdjustmentTab(QWidget):
    """The 'Dynamic Adjustment' outer tab: one trial at a time, an overlay
    plot of every trial run this session, and the persisted-history winner."""

    profile_change_requested = Signal(str)
    single_channel_change_requested = Signal(str)
    run_state_changed = Signal(bool)

    def __init__(self, beamline, parent=None):
        super().__init__(parent)
        self.beamline = beamline
        self._trial: DynamicAdjustmentTrial = None
        self._connected = False
        self._current_profile = None
        self._prior_profile = None
        self._session_trials: list = []   # this session's completed records

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        caveat = QLabel(_CAVEAT_TEXT)
        caveat.setWordWrap(True)
        caveat.setStyleSheet(theme.status_label(theme.WARN, bold=False))
        layout.addWidget(caveat)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        left_col = QVBoxLayout()
        left_col.setSpacing(8)

        self.lj_panel = LabJackPanel()
        left_col.addWidget(self.lj_panel)

        cfg_box = QGroupBox("Trial Setup")
        cfg_form = QFormLayout(cfg_box)

        self.cb_channel = NoScrollComboBox()
        self.cb_channel.addItems(AMP_LABELS)
        cfg_form.addRow("Channel:", self.cb_channel)

        self.le_pot_position = QLineEdit()
        self.le_pot_position.setPlaceholderText(
            "Required — e.g. clock position marked on the dial")
        cfg_form.addRow("Pot position (operator-entered):", self.le_pot_position)

        btn_row = QHBoxLayout()
        self.btn_run = QPushButton("Run Trial")
        self.btn_run.clicked.connect(self._on_run_clicked)
        btn_row.addWidget(self.btn_run)
        cfg_form.addRow(btn_row)

        self.lbl_status = QLabel("Idle")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet(f"color: {theme.NEUTRAL};")
        cfg_form.addRow("Status:", self.lbl_status)

        left_col.addWidget(cfg_box)
        left_col.addStretch()
        top_row.addLayout(left_col, stretch=0)

        plot_box = QGroupBox("Trial Overlay (Voltage Step Response)")
        plot_lay = QVBoxLayout(plot_box)
        self._fig = Figure(figsize=(6, 4))
        self._canvas = FigureCanvasQTAgg(self._fig)
        self._canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._ax = self._fig.add_subplot(111)
        plot_lay.addWidget(self._canvas)
        top_row.addWidget(plot_box, stretch=1)

        layout.addLayout(top_row, stretch=1)

        table_box = QGroupBox("Trials — Winner by Figure of Merit")
        table_lay = QVBoxLayout(table_box)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Pot position", "Overshoot %", "Settling 1% (ms)",
             "Flat-top creep %", "Peak I (mA)", "Figure of merit"])
        table_lay.addWidget(self.table)
        self.lbl_hint = QLabel("—")
        self.lbl_hint.setWordWrap(True)
        table_lay.addWidget(self.lbl_hint)
        layout.addWidget(table_box)

        self._refresh_table()

    # ------------------------------------------------------------------
    # Run control
    # ------------------------------------------------------------------

    def _on_run_clicked(self):
        if not self._connected:
            QMessageBox.warning(self, "Not connected", "Connect the LabJack T7 first.")
            return
        pot_position = self.le_pot_position.text().strip()
        if not pot_position:
            QMessageBox.warning(self, "Pot position required",
                                 "Enter the pot's dial position before running a trial "
                                 "(Section 8.3) — it cannot be read back from the hardware.")
            return
        amp_label = self.cb_channel.currentText()
        funcgen_map = self.beamline.build_funcgen_map()
        if funcgen_map.get(amp_label, (None, None))[0] is None:
            QMessageBox.warning(self, "Generator not connected",
                                 f"{amp_label}'s function generator is not connected.")
            return

        try:
            self._trial = DynamicAdjustmentTrial(amp_label, funcgen_map, pot_position)
        except ValueError as e:
            QMessageBox.warning(self, "Cannot start trial", str(e))
            return

        self._trial.stage_changed.connect(self._on_stage_changed)
        self._trial.trial_completed.connect(self._on_trial_completed)
        self._trial.error.connect(self._on_trial_error)

        self._prior_profile = self._current_profile
        self._set_running(True)
        self._trial.start()
        self.single_channel_change_requested.emit(self._trial.target_ain())
        self.profile_change_requested.emit("SINGLE_FAST")

    def _on_stage_changed(self, stage: str):
        self.lbl_status.setText(f"{self.cb_channel.currentText()}: {stage} stage")
        if stage in ("voltage", "current") and self._trial is not None:
            self.single_channel_change_requested.emit(self._trial.target_ain())

    def _on_trial_completed(self, record: dict):
        self._set_running(False)
        self._session_trials.append(record)
        append_trial(record)
        self.lbl_status.setText(f"Trial complete: {record['pot_position']} "
                                 f"(figure of merit {record['figure_of_merit']:.3f})")
        if self._prior_profile:
            self.profile_change_requested.emit(self._prior_profile)
        self._redraw_plot()
        self._refresh_table()

    def _on_trial_error(self, msg: str):
        self._set_running(False)
        QMessageBox.warning(self, "Trial error", msg)

    def _set_running(self, running: bool):
        self.btn_run.setEnabled(not running)
        self.cb_channel.setEnabled(not running)
        self.le_pot_position.setEnabled(not running)
        self.run_state_changed.emit(running)

    # ------------------------------------------------------------------
    # Plotting and table
    # ------------------------------------------------------------------

    def _redraw_plot(self):
        self._ax.clear()
        for record in self._session_trials:
            v = record.get("voltage", {})
            t = v.get("trace_t_s")
            trace = v.get("trace_v_kv")
            if t and trace:
                self._ax.plot(t, trace, label=record["pot_position"])
        self._ax.set_xlabel("Time since edge (s)")
        self._ax.set_ylabel("Voltage (kV)")
        self._ax.grid(True, alpha=0.3)
        if self._session_trials:
            self._ax.legend(fontsize="small")
        self._fig.tight_layout()
        self._canvas.draw_idle()

    def _refresh_table(self):
        amp_label = self.cb_channel.currentText()
        # Every completed trial is persisted before _refresh_table() runs
        # (see _on_trial_completed), so the persistent store already
        # includes this session's trials — no separate merge needed.
        records = trials_for(amp_label)
        winner = winner_for(amp_label)

        self.table.setRowCount(len(records))
        for row, rec in enumerate(records):
            v = rec.get("voltage", {})
            c = rec.get("current", {})
            settling_ms = v.get("settling_1pct_s", float("nan"))
            settling_ms = settling_ms * 1000.0 if settling_ms == settling_ms else settling_ms
            values = [
                rec.get("pot_position", "—"),
                f"{v.get('overshoot_pct', float('nan')):.3f}" if "overshoot_pct" in v else "—",
                f"{settling_ms:.3f}" if settling_ms == settling_ms else "—",
                f"{v.get('flat_top_creep_pct', float('nan')):.3f}" if "flat_top_creep_pct" in v else "—",
                f"{c.get('peak_current_ma', float('nan')):.3f}" if "peak_current_ma" in c else "—",
                f"{rec.get('figure_of_merit', float('nan')):.3f}",
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(str(text))
                if winner is not None and rec.get("pot_position") == winner.get("pot_position"):
                    from PySide6.QtGui import QColor
                    item.setBackground(QColor(theme.OK))
                self.table.setItem(row, col, item)

        if winner is not None:
            creep = winner.get("voltage", {}).get("flat_top_creep_pct", float("nan"))
            if creep == creep and abs(creep) > 0.5:
                direction = "increase" if creep > 0 else "decrease"
                hint = (f"Winner: {winner['pot_position']} (creep {creep:+.2f}%). "
                        f"If continuing to search, try positions that {direction} the "
                        "pot from there — the sign of the creep is the steering direction.")
            else:
                hint = f"Winner: {winner['pot_position']} — flat-top creep is small; near optimal for this range."
            self.lbl_hint.setText(hint)
        else:
            self.lbl_hint.setText("No trials recorded yet for this channel.")

    # ------------------------------------------------------------------
    # _lj_tabs contract (see rbl/gui/app.py)
    # ------------------------------------------------------------------

    def on_labjack_connected(self, serial: str):
        self._connected = True
        self.lj_panel.set_connected(True, serial)

    def on_labjack_disconnected(self):
        self._connected = False
        self.lj_panel.set_connected(False)

    def on_labjack_error(self, msg: str):
        QMessageBox.warning(self, "LabJack poll error", msg)

    def on_profile_changed(self, profile_name: str):
        self._current_profile = profile_name

    def on_window(self, payload: dict):
        if self._trial is not None:
            self._trial.on_window(payload)

    def shutdown(self):
        pass
