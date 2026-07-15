"""
labjack_panel.py
A shared LabJack T7 connection panel.

Both the Beam Current tab and the HV Amplifiers tab embed one of these. They all
drive the SAME LabJackT7 instance owned by MainWindow, so connecting from either
tab connects for both. That is intentional: there is one physical T7.
"""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QGroupBox, QLabel, QPushButton, QLineEdit, QComboBox,
)


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

        self.lbl_status = QLabel("● Disconnected")
        self.lbl_status.setStyleSheet("color: #666666; font-weight: bold;")
        lay.addWidget(self.lbl_status)

        self.lbl_serial = QLabel("")
        self.lbl_serial.setStyleSheet("color: #555; font-style: italic;")
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
        if connected:
            self.btn_conn.setText("Disconnect")
            self.lbl_status.setText("● Connected")
            self.lbl_status.setStyleSheet("color: #1a7a1a; font-weight: bold;")
            self.lbl_serial.setText(f"T7 serial #{serial}" if serial else "")
        else:
            self.btn_conn.setText("Connect")
            self.lbl_status.setText("● Disconnected")
            self.lbl_status.setStyleSheet("color: #666666; font-weight: bold;")
            self.lbl_serial.setText("")

    def set_enabled(self, on: bool):
        self.btn_conn.setEnabled(on)


if __name__ == "__main__":
    import sys
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    w = LabJackPanel()
    w.show()
    print("[OK] labjack_panel loads")
    sys.exit(app.exec())
