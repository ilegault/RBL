"""
slit_control.py
One slit's position bar chart plus the controls to move it.

Modelled on the "DESIRE / MOVE" pairs on the Michigan overview screen: the
live position sits in a scaled track, the operator types where they want it,
and a caret on the same track shows where that target is before anything
moves. Seeing the commanded position and the actual position on one scale is
the whole point — two separate number boxes make you do the comparison in your
head.

Deliberately target-only: no step size and no -/+ nudge buttons. An absolute
target is the whole interaction, and the spinbox's own arrows still step it for
anyone who wants to creep up on a number. Relative jogging lives on the Stepper
Motors tab, which has the limit-switch context that makes it safe.

This widget commands nothing itself. It emits `move_requested(slit, mm)` and
lets its owner decide whether that reaches hardware — every path to the
Galil goes through Beamline (see rbl/state/beamline.py), never through a
widget holding a driver.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from rbl.config import hardware_config as SC
from rbl.gui import theme
from rbl.gui.widgets.inputs import QuietDoubleSpinBox, unit_row
from rbl.gui.widgets.mini import MiniBar


class SlitControl(QWidget):
    """Position readout + target entry + move controls for ONE slit."""

    move_requested = Signal(str, float)   # slit label, absolute mm from centre

    def __init__(self, slit: str, parent=None):
        super().__init__(parent)
        self.slit = slit
        self._position_mm = None      # last live position, None when unknown

        # Four of these stack in a column; each keeps its natural height rather
        # than growing to share the slack, which would spread three rows of
        # controls over half a screen.
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Maximum)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)

        self.bar = MiniBar(
            f"{slit} position", SC.SLIT_DISPLAY_MIN_MM, SC.SLIT_DISPLAY_MAX_MM,
            unit="mm", color=theme.SLIT_COLORS.get(slit), decimals=3,
        )
        lay.addWidget(self.bar)

        row = QHBoxLayout()
        row.setSpacing(3)

        self.spn_target = QuietDoubleSpinBox()
        self.spn_target.setDecimals(3)
        self.spn_target.setRange(SC.SLIT_DISPLAY_MIN_MM, SC.SLIT_DISPLAY_MAX_MM)
        self.spn_target.setSingleStep(SC.SLIT_DEFAULT_STEP_MM)
        self.spn_target.setMinimumHeight(30)
        self.spn_target.setStyleSheet(f"font-size: {theme.FS_LABEL}px;")
        self.spn_target.setToolTip(
            "Absolute distance from beam centre. The 0.2 mm home offset is "
            "applied for you — type the true distance you want."
        )
        # The caret on the bar tracks whatever is typed, so the target can be
        # compared against the live position before committing the move.
        self.spn_target.valueChanged.connect(self.bar.set_target)
        self.bar.set_target(self.spn_target.value())

        self.btn_move = QPushButton("Move")
        self.btn_move.setMinimumHeight(30)
        self.btn_move.setStyleSheet(
            "QPushButton { background:#004e8c; color:white; font-weight:bold;"
            f" font-size:{theme.FS_LABEL}px; padding: 2px 10px; }}"
            "QPushButton:hover { background:#0063b1; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        self.btn_move.clicked.connect(self._move_to_target)

        # The unit sits in a label beside the box, never inside it as a
        # spinbox suffix — see rbl/gui/widgets/inputs.py for what a suffix
        # does to a value being typed.
        row.addLayout(unit_row(self.spn_target, "mm", font_size=theme.FS_LABEL),
                      stretch=1)
        row.addWidget(self.btn_move)
        lay.addLayout(row)

        self.lbl_state = QLabel("—")
        self.lbl_state.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.lbl_state.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
        lay.addWidget(self.lbl_state)

        self.set_enabled(False)

    # ---- Live state in -------------------------------------------------------

    def set_position(self, mm: float, stale: bool = False,
                     moving: bool = False, enabled: bool = True):
        """Latest position for this slit, in absolute mm from beam centre."""
        self._position_mm = None if stale else mm
        role = theme.WARN if moving else None
        self.bar.set(mm, stale=stale, role=role)

        if stale:
            self.lbl_state.setText("no position — Galil not connected")
            self.lbl_state.setStyleSheet(
                f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
        elif moving:
            self.lbl_state.setText("moving")
            self.lbl_state.setStyleSheet(
                theme.status_label(theme.WARN) + f"font-size: {theme.FS_CAPTION}px;")
        elif not enabled:
            self.lbl_state.setText("motor disabled")
            self.lbl_state.setStyleSheet(
                theme.status_label(theme.MUTED) + f"font-size: {theme.FS_CAPTION}px;")
        else:
            self.lbl_state.setText("idle")
            self.lbl_state.setStyleSheet(
                theme.status_label(theme.OK) + f"font-size: {theme.FS_CAPTION}px;")

    def set_target_mm(self, mm: float):
        """Show a target commanded from anywhere — here, or the Stepper Motors
        tab. Deferred while this box has focus so it cannot eat live typing."""
        self.spn_target.sync_value(mm)
        self.bar.set_target(mm)

    def sync_target_to_position(self):
        """Preload the target box with where the slit already is.

        Called when the link comes up so the first thing an operator sees in
        the box is the current position, not a leftover 0.000 that would drive
        the slit into centre if they hit Move without looking.
        """
        if self._position_mm is not None:
            self.spn_target.setValue(self._position_mm)

    def set_enabled(self, on: bool):
        for w in (self.spn_target, self.btn_move):
            w.setEnabled(on)

    # ---- Control out ---------------------------------------------------------

    def _move_to_target(self):
        self.move_requested.emit(self.slit, self.spn_target.value())
