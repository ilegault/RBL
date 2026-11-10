"""
amplifier_warnings_panel.py
The Overview tab's "Amplifier warnings" list and the spike count per plate.

WHY THIS EXISTS
---------------
The amplifiers protect themselves; the application is the witness
(docs/adr/0006-amplifier-faults-are-observed-not-stopped-during-a-session.md).
A warning here therefore NEVER opens a dialog and never stops anything: a modal
box in the middle of a 12-hour irradiation would block the GUI thread and tempt
an operator to click through without reading. Each warning is a row in a list
and stays until its own Dismiss button is clicked, so a warning raised while
nobody was looking is still there when they come back.

The panel only stores and shows text. Deciding WHEN to warn (a rail event, a
reference off its prediction) belongs to OverviewTab, which has the setpoints.
"""
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rbl.config.hardware_config import AMP_LABELS
from rbl.gui import theme


class AmplifierWarningsPanel(QGroupBox):
    """Per-plate spike counts above a list of dismissable warnings."""

    def __init__(self, parent=None):
        super().__init__("Amplifier warnings", parent)
        self._counts = dict.fromkeys(AMP_LABELS, 0)
        self._rows: list[tuple[str, QWidget]] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(2)

        self._lbl_counts = {}
        counts = QHBoxLayout()
        for plate in AMP_LABELS:
            lbl = QLabel()
            lbl.setStyleSheet(
                f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
            self._lbl_counts[plate] = lbl
            counts.addWidget(lbl)
        counts.addStretch(1)
        lay.addLayout(counts)

        self._rows_lay = QVBoxLayout()
        self._rows_lay.setSpacing(2)
        lay.addLayout(self._rows_lay)
        self._refresh_counts()

    # ---- spike counts -------------------------------------------------------

    def count_spike(self, plate: str) -> None:
        self._counts[plate] = self._counts.get(plate, 0) + 1
        self._refresh_counts()

    def spike_count(self, plate: str) -> int:
        return self._counts.get(plate, 0)

    def spike_count_text(self, plate: str) -> str:
        return self._lbl_counts[plate].text()

    def _refresh_counts(self) -> None:
        for plate, lbl in self._lbl_counts.items():
            lbl.setText(f"{plate} spikes: {self._counts[plate]}")

    # ---- warnings -----------------------------------------------------------

    def add_warning(self, text: str) -> None:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(theme.status_label(theme.WARN)
                          + f"font-size: {theme.FS_LABEL}px;")
        h.addWidget(lbl, stretch=1)
        btn = QPushButton("Dismiss")
        btn.clicked.connect(lambda _=False, r=row: self._dismiss(r))
        h.addWidget(btn)
        self._rows_lay.addWidget(row)
        self._rows.append((text, row))

    def warnings(self) -> list[str]:
        return [text for text, _ in self._rows]

    def dismiss_buttons(self) -> list[QPushButton]:
        return [row.findChild(QPushButton) for _, row in self._rows]

    def _dismiss(self, row: QWidget) -> None:
        self._rows = [(t, r) for t, r in self._rows if r is not row]
        self._rows_lay.removeWidget(row)
        row.setParent(None)
        row.deleteLater()
