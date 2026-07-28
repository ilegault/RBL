"""
slit_control.py
One slit's position bar chart plus the controls to move it.

Modelled on the "DESIRE / MOVE" pairs on the Michigan overview screen: the
live position sits in a scaled track, the operator types (or nudges) where
they want it, and a caret on the same track shows where that target is before
anything moves. Seeing the commanded position and the actual position on one
scale is the whole point — two separate number boxes make you do the
comparison in your head.

This widget commands nothing itself. It emits `move_requested(slit, mm)` and
lets its owner decide whether that reaches hardware — every path to the
Galil goes through Beamline (see rbl/state/beamline.py), never through a
widget holding a driver.
"""
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QDoubleSpinBox,
    QComboBox, QSizePolicy,
)

from rbl.config import hardware_config as SC
from rbl.gui import theme
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

        self.btn_minus = QPushButton("−")
        self.btn_plus = QPushButton("+")
        for btn in (self.btn_minus, self.btn_plus):
            btn.setFixedWidth(26)
            btn.setToolTip("Move one step from the current position")
        self.btn_minus.clicked.connect(lambda: self._nudge(-1))
        self.btn_plus.clicked.connect(lambda: self._nudge(+1))

        self.spn_target = QDoubleSpinBox()
        self.spn_target.setDecimals(3)
        self.spn_target.setRange(SC.SLIT_DISPLAY_MIN_MM, SC.SLIT_DISPLAY_MAX_MM)
        self.spn_target.setSingleStep(SC.SLIT_DEFAULT_STEP_MM)
        self.spn_target.setSuffix(" mm")
        self.spn_target.setToolTip(
            "Absolute distance from beam centre. The 0.2 mm home offset is "
            "applied for you — type the true distance you want."
        )
        # The caret on the bar tracks whatever is typed, so the target can be
        # compared against the live position before committing the move.
        self.spn_target.valueChanged.connect(self.bar.set_target)
        self.bar.set_target(self.spn_target.value())

        self.cbo_step = QComboBox()
        for step in SC.SLIT_STEP_CHOICES_MM:
            self.cbo_step.addItem(f"{step:g} mm", step)
        self.cbo_step.setCurrentIndex(
            SC.SLIT_STEP_CHOICES_MM.index(SC.SLIT_DEFAULT_STEP_MM))
        self.cbo_step.setToolTip("Step size for the − / + buttons")

        self.btn_move = QPushButton("Move")
        self.btn_move.setMinimumHeight(26)
        self.btn_move.setStyleSheet(
            "QPushButton { background:#004e8c; color:white; font-weight:bold; }"
            "QPushButton:hover { background:#0063b1; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        self.btn_move.clicked.connect(self._move_to_target)

        row.addWidget(self.btn_minus)
        row.addWidget(self.spn_target, stretch=1)
        row.addWidget(self.btn_plus)
        row.addWidget(self.cbo_step)
        row.addWidget(self.btn_move)
        lay.addLayout(row)

        self.lbl_state = QLabel("—")
        self.lbl_state.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.lbl_state.setStyleSheet(f"color: {theme.NEUTRAL}; font-size: 9px;")
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
            self.lbl_state.setStyleSheet(f"color: {theme.NEUTRAL}; font-size: 9px;")
        elif moving:
            self.lbl_state.setText("moving")
            self.lbl_state.setStyleSheet(theme.status_label(theme.WARN) + "font-size: 9px;")
        elif not enabled:
            self.lbl_state.setText("motor disabled")
            self.lbl_state.setStyleSheet(theme.status_label(theme.MUTED) + "font-size: 9px;")
        else:
            self.lbl_state.setText("idle")
            self.lbl_state.setStyleSheet(theme.status_label(theme.OK) + "font-size: 9px;")

    def sync_target_to_position(self):
        """Preload the target box with where the slit already is.

        Called when the link comes up so the first thing an operator sees in
        the box is the current position, not a leftover 0.000 that would drive
        the slit into centre if they hit Move without looking.
        """
        if self._position_mm is not None:
            self.spn_target.setValue(self._position_mm)

    def set_enabled(self, on: bool):
        for w in (self.btn_minus, self.btn_plus, self.spn_target,
                  self.cbo_step, self.btn_move):
            w.setEnabled(on)

    # ---- Control out ---------------------------------------------------------

    @property
    def step_mm(self) -> float:
        return float(self.cbo_step.currentData())

    def _move_to_target(self):
        self.move_requested.emit(self.slit, self.spn_target.value())

    def _nudge(self, direction: int):
        """Step from where the slit actually IS, not from the target box.

        Nudging off the target box would compound: two clicks after a move
        that is still in flight would command twice the step from a position
        the slit never reached.
        """
        base = self._position_mm
        if base is None:
            base = self.spn_target.value()
        target = base + direction * self.step_mm
        target = min(SC.SLIT_DISPLAY_MAX_MM, max(SC.SLIT_DISPLAY_MIN_MM, target))
        self.spn_target.setValue(target)
        self.move_requested.emit(self.slit, target)
