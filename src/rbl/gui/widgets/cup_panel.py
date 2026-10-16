"""
cup_panel.py
Overview-tab panel showing what the Faraday cup is doing, with Start/Stop Logging.

WHY THIS EXISTS
---------------
Since ADR 0003 amendment C1 a cup log is an explicit state: a session opens it, or
the operator starts a test log. Before this panel the only place to see that, or to
start a test log without a session, was the Faraday Cup tab. An operator watching
the Overview during a dose run needs the cup current, the cup position, whether an
insertion is open, and the running charge/fluence/dpa without switching tabs.

This panel RENDERS and never COMPUTES. The dose numbers arrive as a ``CupView``
that ``FaradayCupTab`` builds from its one ``DoseAccumulator`` (AGENTS.md invariant
2: one conversion). A second accumulator here would let two screens disagree about
the same charge. The cup current and confirmed position come from the beamline's
own ``CupState`` / ``CupActuationState`` snapshots, the same ones the tab renders.

Whether a log is open lives in ``CupLog`` alone. The button asks ``CupLog.kind``;
while a SESSION owns the log the button is disabled, because ending a session's cup
log from here would leave the session believing its file was still open.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QGridLayout, QGroupBox, QLabel, QPushButton, QVBoxLayout

from rbl.gui import theme
from rbl.hardware.cup_status import CupPosition
from rbl.hardware.current_monitor import format_current
from rbl.services.cup_log import CupLog, CupLogKind, CupView
from rbl.snapshots import CupActuationState, CupState

SESSION_OWNS_LOG_TOOLTIP = "The session owns the cup log. Stop the session to end it."

_DASH = "  —  "


class CupPanel(QGroupBox):
    """Faraday cup status plus Start/Stop Logging for a cup test log."""

    def __init__(self, cup_log: CupLog, parent=None) -> None:
        super().__init__("Faraday Cup", parent)
        self._cup_log = cup_log
        self._build_ui()
        self.on_cup_view(CupView(
            logging=cup_log.writer is not None,
            log_path=cup_log.path,
            run_open=False,
            automatic_running=False,
            charge_c=0.0,
            fluence=None,
            dpa=None,
        ))

    # ---- UI construction ---------------------------------------------------

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setSpacing(4)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)
        self.lbl_current = QLabel(_DASH)
        self.lbl_position = QLabel(_DASH)
        self.lbl_run = QLabel("No run")
        self.lbl_auto = QLabel("Manual")
        self.lbl_charge = QLabel(_DASH)
        self.lbl_fluence = QLabel(_DASH)
        self.lbl_dpa = QLabel(_DASH)
        rows = (
            ("Current", self.lbl_current),
            ("Position", self.lbl_position),
            ("Run", self.lbl_run),
            ("Insertion", self.lbl_auto),
            ("Charge", self.lbl_charge),
            ("Fluence", self.lbl_fluence),
            ("dpa", self.lbl_dpa),
        )
        for r, (caption, value) in enumerate(rows):
            grid.addWidget(QLabel(caption + ":"), r, 0)
            value.setStyleSheet(theme.status_label(theme.NEUTRAL, bold=False))
            grid.addWidget(value, r, 1)
        lay.addLayout(grid)

        self.btn_logging = QPushButton("Start Logging")
        self.btn_logging.clicked.connect(self._on_logging_clicked)
        lay.addWidget(self.btn_logging)

        self.lbl_path = QLabel("Not logging")
        self.lbl_path.setWordWrap(True)
        lay.addWidget(self.lbl_path)

    # ---- Snapshots in ------------------------------------------------------

    def on_cup_view(self, view: CupView) -> None:
        """Render the tab's accumulator-derived snapshot."""
        if view.logging and view.log_path:
            self.lbl_path.setText(view.log_path)
            self.lbl_path.setStyleSheet(
                theme.status_label(theme.OK, bold=False)
                + f"font-size: {theme.FS_CAPTION}px;")
        else:
            self.lbl_path.setText("Not logging")
            self.lbl_path.setStyleSheet(theme.status_label(theme.WARN))

        self.lbl_run.setText("Run open" if view.run_open else "No run")
        self.lbl_auto.setText("Automatic" if view.automatic_running else "Manual")
        # Same format string as FaradayCupTab._update_dose_view, so the two agree.
        self.lbl_charge.setText(f"{view.charge_c:.6e} C")
        self.lbl_fluence.setText(
            f"{view.fluence:.4e} ions/cm²" if view.fluence is not None else _DASH)
        self.lbl_dpa.setText(f"{view.dpa:.4e} dpa" if view.dpa is not None else _DASH)

        session_owns = self._cup_log.kind is CupLogKind.SESSION
        self.btn_logging.setText("Stop Logging" if view.logging else "Start Logging")
        self.btn_logging.setEnabled(not session_owns)
        self.btn_logging.setToolTip(SESSION_OWNS_LOG_TOOLTIP if session_owns else "")

    def on_cup_state(self, state: CupState) -> None:
        """Render the picoammeter reading the Faraday tab also renders."""
        if not state.connected:
            self.lbl_current.setText(_DASH)
        elif state.over_range:
            self.lbl_current.setText("OVER-RANGE")
        elif state.current is None or not state.valid:
            self.lbl_current.setText(_DASH)
        else:
            self.lbl_current.setText(format_current(state.current).strip())

    def on_cup_actuation_state(self, state: CupActuationState) -> None:
        """Render the confirmed (not commanded) position."""
        if not state.connected or state.stale:
            self.lbl_position.setText(_DASH)
            return
        confirmed: CupPosition = state.confirmed
        self.lbl_position.setText(confirmed.value.replace("_", " ").title())

    # ---- Intent out --------------------------------------------------------

    def _on_logging_clicked(self) -> None:
        if self._cup_log.kind is CupLogKind.SESSION:
            return
        if self._cup_log.writer is None:
            self._cup_log.open_test(datetime.now().astimezone())
        else:
            self._cup_log.close()
