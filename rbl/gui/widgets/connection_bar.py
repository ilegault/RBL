"""
connection_bar.py
Shared connection-status pill and the LabJack T7 connection panel.

The Beam Current tab and the HV Amplifiers tab each embed a LabJackPanel.
They all drive the SAME LabJackT7 instance owned by MainWindow, so connecting
from either tab connects for both. That is intentional: there is one physical
T7.
"""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QLabel, QHBoxLayout, QGroupBox, QPushButton, QLineEdit, QComboBox,
)

from rbl.gui import theme


class StatusPill(QLabel):
    """The recurring '● Connected' / '● Disconnected' status label.

    Callers own the text (it varies: "● Connected", "● Gen A: connected
    [serial]", ...) — this widget only tracks the connected/disconnected
    colour so every connection indicator in the app reads the same way.
    """

    def __init__(self, connected_text="● Connected",
                 disconnected_text="● Disconnected", parent=None):
        super().__init__(disconnected_text, parent)
        self._connected_text = connected_text
        self._disconnected_text = disconnected_text
        self.setStyleSheet(theme.pill(False))

    def set_connected(self, connected: bool, text: str = None):
        self.setText(text if text is not None else
                     (self._connected_text if connected else self._disconnected_text))
        self.setStyleSheet(theme.pill(connected))


class LabJackPanel(QGroupBox):
    """Connection controls for the shared T7.

    Emits connect_requested(conn_type, identifier) and disconnect_requested().
    Does NOT touch hardware itself — MainWindow does that.
    """
    connect_requested    = Signal(str, str)
    disconnect_requested = Signal()

    def __init__(self, parent=None):
        super().__init__("LabJack T7 Connection (shared)", parent)
        lay = QHBoxLayout(self)

        lay.addWidget(QLabel("Connection:"))
        self.cbo_conn = QComboBox()
        self.cbo_conn.addItems(["USB", "ETHERNET", "ANY"])
        lay.addWidget(self.cbo_conn)

        lay.addWidget(QLabel("Identifier:"))
        self.le_ident = QLineEdit("ANY")
        self.le_ident.setMaximumWidth(140)
        lay.addWidget(self.le_ident)

        self.btn_conn = QPushButton("Connect")
        self.btn_conn.clicked.connect(self._on_click)
        lay.addWidget(self.btn_conn)

        self.lbl_status = StatusPill()
        lay.addWidget(self.lbl_status)

        self.lbl_serial = QLabel("")
        self.lbl_serial.setStyleSheet(f"color: {theme.NEUTRAL}; font-style: italic;")
        lay.addWidget(self.lbl_serial, stretch=1)

        self._connected = False

    def _on_click(self):
        if self._connected:
            self.disconnect_requested.emit()
        else:
            self.connect_requested.emit(
                self.cbo_conn.currentText(),
                self.le_ident.text().strip() or "ANY",
            )

    def set_connected(self, connected: bool, serial: str = ""):
        """Called by MainWindow to push state down to every panel at once."""
        self._connected = connected
        self.btn_conn.setText("Disconnect" if connected else "Connect")
        self.lbl_status.set_connected(connected)
        self.lbl_serial.setText(f"T7 serial #{serial}" if connected and serial else "")

    def set_enabled(self, on: bool):
        self.btn_conn.setEnabled(on)
