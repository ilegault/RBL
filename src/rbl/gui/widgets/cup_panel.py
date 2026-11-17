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

This panel RENDERS and never COMPUTES. The dose numbers and convergence-bar
fractions arrive as a ``CupView`` that ``FaradayCupTab`` builds from its one
``DoseAccumulator`` and ``compute_convergence_bars`` (AGENTS.md invariant 2: one
conversion). A second accumulator or bar math here would let two screens disagree
about the same run. While an insertion is open, two thin stacked bars show
uncertainty draining toward the target and elapsed time against the dwell; while the
cup is out, a one-line summary of the last insertion replaces them. The cup current
and confirmed position come from the beamline's own ``CupState`` /
``CupActuationState`` snapshots, the same ones the tab renders.

Whether a log is open lives in ``CupLog`` alone. The button asks ``CupLog.kind``;
while a SESSION owns the log the button is disabled, because ending a session's cup
log from here would leave the session believing its file was still open.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rbl.gui import theme
from rbl.hardware.cup_status import CupPosition
from rbl.hardware.current_monitor import format_current
from rbl.services.cup_log import CupLog, CupLogKind, CupView
from rbl.snapshots import CupActuationState, CupState

SESSION_OWNS_LOG_TOOLTIP = "The session owns the cup log. Stop the session to end it."

_DASH = "  —  "
_BAR_RANGE = 1000
_TARGET_MARK_FRACTION = 0.25


def _bar_stylesheet(chunk_colour: str) -> str:
    return (
        "QProgressBar {"
        f"  background: {theme.TRACK};"
        f"  border: 1px solid {theme.TRACK_EDGE};"
        "  border-radius: 2px;"
        "}"
        "QProgressBar::chunk {"
        f"  background: {chunk_colour};"
        "}"
    )


def _format_uncertainty_text(view: CupView) -> str:
    if view.reason_text is not None:
        return view.reason_text
    if view.uncertainty is None:
        return _DASH
    parts: list[str] = []
    pct = f"{view.uncertainty * 100:.1f} %"
    if view.dwell_mode == "target_uncertainty" and view.target is not None:
        pct += f" → {view.target * 100:.0f} %"
    parts.append(pct)
    if view.n_needed is not None:
        parts.append(f"n = {view.n} / ~{view.n_needed}")
    else:
        parts.append(f"n = {view.n}")
    if view.time_left_s is not None and view.dwell_mode == "target_uncertainty":
        parts.append(f"~{view.time_left_s:.1f} s left")
    return "   ".join(parts)


def _format_insertion_text(view: CupView) -> str:
    if view.dwell_limit_s is None:
        return f"{view.elapsed_s:.1f} s"
    unit = "dwell" if view.dwell_mode == "fixed" else "max"
    return f"{view.elapsed_s:.1f} s / {view.dwell_limit_s:.1f} s {unit}"


class _UncertaintyBar(QProgressBar):
    """Thin progress bar with a child target-mark widget at 25 % of its width."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setRange(0, _BAR_RANGE)
        self.setTextVisible(False)
        self.setFixedHeight(10)
        self.target_mark = QWidget(self)
        self.target_mark.setFixedWidth(2)
        self.target_mark.setStyleSheet(f"background: {theme.MARKER};")
        self._position_target_mark()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_target_mark()

    def _position_target_mark(self) -> None:
        x = max(0, int(round(self.width() * _TARGET_MARK_FRACTION)) - 1)
        self.target_mark.setGeometry(x, 0, 2, self.height())


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

        self.uncertainty_bar = _UncertaintyBar()
        self.uncertainty_target_mark = self.uncertainty_bar.target_mark
        self.lbl_uncertainty_bar = QLabel("")
        self.lbl_uncertainty_bar.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;"
        )
        unc_row = QHBoxLayout()
        unc_row.setSpacing(6)
        unc_row.addWidget(self.uncertainty_bar, 1)
        unc_row.addWidget(self.lbl_uncertainty_bar)
        lay.addLayout(unc_row)

        self.insertion_bar = QProgressBar()
        self.insertion_bar.setRange(0, _BAR_RANGE)
        self.insertion_bar.setTextVisible(False)
        self.insertion_bar.setFixedHeight(8)
        self.insertion_bar.setStyleSheet(_bar_stylesheet(theme.NEUTRAL))
        self.lbl_insertion_bar = QLabel("")
        self.lbl_insertion_bar.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;"
        )
        ins_row = QHBoxLayout()
        ins_row.setSpacing(6)
        ins_row.addWidget(self.insertion_bar, 1)
        ins_row.addWidget(self.lbl_insertion_bar)
        lay.addLayout(ins_row)

        self.lbl_last_insertion = QLabel("")
        self.lbl_last_insertion.setWordWrap(True)
        self.lbl_last_insertion.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;"
        )
        lay.addWidget(self.lbl_last_insertion)

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

        open_ins = view.insertion_open
        self.uncertainty_bar.setVisible(open_ins)
        self.lbl_uncertainty_bar.setVisible(open_ins)
        self.insertion_bar.setVisible(open_ins)
        self.lbl_insertion_bar.setVisible(open_ins)
        if open_ins:
            target_mode = view.dwell_mode == "target_uncertainty"
            self.uncertainty_target_mark.setVisible(target_mode)
            if view.reason_text is not None:
                colour = theme.MUTED
            elif target_mode and view.converged:
                colour = theme.OK
            else:
                colour = theme.WARN
            self.uncertainty_bar.setStyleSheet(_bar_stylesheet(colour))
            fill = (
                0
                if view.drain_fraction is None
                else max(0, min(_BAR_RANGE, int(round(view.drain_fraction * _BAR_RANGE))))
            )
            self.uncertainty_bar.setValue(fill)
            self.lbl_uncertainty_bar.setText(_format_uncertainty_text(view))

            if view.dwell_limit_s is not None and view.dwell_limit_s > 0.0:
                ins_fill = max(
                    0,
                    min(
                        _BAR_RANGE,
                        int(round((view.elapsed_s / view.dwell_limit_s) * _BAR_RANGE)),
                    ),
                )
            else:
                ins_fill = 0
            self.insertion_bar.setValue(ins_fill)
            self.lbl_insertion_bar.setText(_format_insertion_text(view))
            self.lbl_last_insertion.setVisible(False)
        else:
            self.uncertainty_target_mark.setVisible(False)
            self.lbl_last_insertion.setText(view.last_insertion_text or "")
            self.lbl_last_insertion.setVisible(bool(view.last_insertion_text))

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
