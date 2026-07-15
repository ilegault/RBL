"""
Right Beam Line DAQ App — Native Desktop GUI
Hardware-only: Stepper Motors, Beam Current, Function Generators.
Run: python app.py   (from inside the rbl/gui/ directory)

PySide6 front-end. Analysis has been split out to the rbl-analysis repo.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QTabBar, QStackedWidget, QMessageBox,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette

from rbl.hardware.labjack_driver import LabJackT7
from rbl.hardware.labjack_poller import LabJackPollWorker


# ─── Main Window ──────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Right Beam Line DAQ")
        self.resize(1440, 920)

        # ── Outer navigation: tab bar + stacked widget ────────────────────────
        outer_widget = QWidget()
        self.setCentralWidget(outer_widget)
        outer_layout = QVBoxLayout(outer_widget)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self._outer_tabbar = QTabBar()
        self._outer_tabbar.addTab("Stepper Motors")
        self._outer_tabbar.addTab("Beam Current")
        self._outer_tabbar.addTab("HV Amplifiers")
        self._outer_tabbar.addTab("Function Generators")
        self._outer_tabbar.setExpanding(False)
        self._outer_tabbar.setDocumentMode(True)
        outer_layout.addWidget(self._outer_tabbar)

        self._outer_stack = QStackedWidget()
        outer_layout.addWidget(self._outer_stack, stretch=1)

        # ── Pages: Motors (0), Current (1), Amplifiers (2), FuncGens (3) ───────
        from motor_tab import MotorTab
        from current_tab import CurrentTab
        from amp_tab import AmpTab
        from funcgen_tab import FuncGenTab
        self.motor_tab   = MotorTab(self)
        self.current_tab = CurrentTab(self)
        self.amp_tab     = AmpTab(self)
        self.funcgen_tab = FuncGenTab(self)
        self._outer_stack.addWidget(self.motor_tab)
        self._outer_stack.addWidget(self.current_tab)
        self._outer_stack.addWidget(self.amp_tab)
        self._outer_stack.addWidget(self.funcgen_tab)

        # ── Shared LabJack T7 ─────────────────────────────────────────────────
        #
        # ONE physical T7 -> ONE LabJackT7 instance -> ONE poll thread reading
        # all 12 AINs in a single eReadNames round trip. Both the Beam Current
        # tab (AIN0-3, log amps) and the HV Amplifier tab (AIN4-11, EEL5000
        # monitors) subscribe to the same signal and filter for their own
        # channels. Never let a tab open its own handle.
        self._lj        = LabJackT7()
        self._lj_worker = None
        self._lj_tabs   = (self.current_tab, self.amp_tab)

        for tab in self._lj_tabs:
            tab.lj_panel.connect_requested.connect(self._labjack_connect)
            tab.lj_panel.disconnect_requested.connect(self._labjack_disconnect)

        # Start on Stepper Motors
        self._outer_stack.setCurrentIndex(0)
        self._outer_tabbar.tabBarClicked.connect(self._on_outer_tab_clicked)

    # ── Shared LabJack management ─────────────────────────────────────────────

    def _labjack_connect(self, conn_type: str, identifier: str):
        if self._lj.connected:
            return
        try:
            self._lj.connect(conn_type, identifier)
            serial = self._lj.serial_number()

            self._lj_worker = LabJackPollWorker(self._lj, period_s=0.1)
            # Fan the SAME reading out to every subscribing tab. Each tab picks
            # out the AINs it owns and ignores the rest.
            for tab in self._lj_tabs:
                self._lj_worker.reading.connect(tab._on_reading)
                self._lj_worker.error.connect(tab._on_error)
            self._lj_worker.error.connect(self._on_labjack_error)
            self._lj_worker.start()

            for tab in self._lj_tabs:
                tab.on_labjack_connected(serial)
        except Exception as e:
            QMessageBox.critical(self, "LabJack connect failed", str(e))
            self._labjack_disconnect()

    def _labjack_disconnect(self):
        if self._lj_worker is not None:
            self._lj_worker.stop()
            self._lj_worker.wait(2000)
            self._lj_worker = None
        self._lj.disconnect()
        for tab in self._lj_tabs:
            tab.on_labjack_disconnected()

    def _on_labjack_error(self, msg: str):
        # The tabs each show their own warning box; we tear the connection down.
        self._labjack_disconnect()

    # ── Close ─────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        try:
            self.motor_tab.abort_and_close()
        except Exception:
            pass
        try:
            self.current_tab.shutdown()
        except Exception:
            pass
        try:
            self.amp_tab.shutdown()
        except Exception:
            pass
        try:
            self._labjack_disconnect()   # stops the shared poll thread + closes T7
        except Exception:
            pass
        try:
            self.funcgen_tab.close_session()
        except Exception:
            pass
        super().closeEvent(event)

    # ── Outer tab switching ───────────────────────────────────────────────────

    def _on_outer_tab_clicked(self, index: int):
        self._outer_stack.setCurrentIndex(index)


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    import os
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

    app = QApplication(sys.argv)
    app.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app.setStyle("Fusion")

    # Light gray palette matching TDS-T8's functional style
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,          QColor(220, 220, 220))
    pal.setColor(QPalette.ColorRole.WindowText,      QColor(20,  20,  20))
    pal.setColor(QPalette.ColorRole.Base,            QColor(245, 245, 245))
    pal.setColor(QPalette.ColorRole.AlternateBase,   QColor(210, 210, 210))
    pal.setColor(QPalette.ColorRole.ToolTipBase,     QColor(255, 255, 220))
    pal.setColor(QPalette.ColorRole.ToolTipText,     QColor(20,  20,  20))
    pal.setColor(QPalette.ColorRole.Text,            QColor(20,  20,  20))
    pal.setColor(QPalette.ColorRole.Button,          QColor(200, 200, 200))
    pal.setColor(QPalette.ColorRole.ButtonText,      QColor(20,  20,  20))
    pal.setColor(QPalette.ColorRole.BrightText,      QColor(180, 0,   0))
    pal.setColor(QPalette.ColorRole.Link,            QColor(0,   80,  180))
    pal.setColor(QPalette.ColorRole.Highlight,       QColor(0,   120, 215))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    app.setPalette(pal)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
