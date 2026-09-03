"""
regulation_dialog.py
Modal dialog asking the operator which EEL5000 front-panel fault LED (if
any) is lit, when the regulation detector (rbl/hardware/regulation.py,
rbl/services/regulation_monitor.py) confirms a channel has gone amp_off or
current_limited.

WHY THIS EXISTS
---------------
The user has declined to wire the fault-monitor BNCs, so software cannot
read which of the four front-panel LEDs — THERMAL LIMIT, CURRENT LIMIT-TRIP,
OUT OF REGULATION, FAN FAULT — is lit. The operator can. Asking, and logging
the answer, is the entire point of Section 7.4: it turns an otherwise-silent
software detection into a labelled data point in the trip history
(rbl/services/trip_history.py).
"""
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
)

# The four front-panel fault LEDs (EEL5000.20.100 manual, front panel
# section) plus an explicit "couldn't tell" option — never force a guess.
FAULT_REASONS = [
    "THERMAL LIMIT",
    "CURRENT LIMIT-TRIP",
    "OUT OF REGULATION",
    "FAN FAULT",
    "None lit / unsure",
]


class RegulationFaultDialog(QDialog):
    def __init__(self, label: str, state: str, reason: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Regulation fault — {label}")
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"<b>{label}</b> classified as <b>{state}</b>.<br><br>{reason}"
            "<br><br>Which front-panel fault LED is lit on this amplifier?"
        ))

        self._group = QButtonGroup(self)
        for i, text in enumerate(FAULT_REASONS):
            rb = QRadioButton(text)
            if i == 0:
                rb.setChecked(True)
            self._group.addButton(rb, i)
            layout.addWidget(rb)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def selected_reason(self) -> str:
        checked_id = self._group.checkedId()
        return FAULT_REASONS[checked_id] if checked_id >= 0 else FAULT_REASONS[-1]
