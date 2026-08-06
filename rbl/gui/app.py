"""
Right Beam Line DAQ App — Native Desktop GUI
Hardware-only: Stepper Motors, Beam Current, Function Generators.
Run: python -m rbl.main

PySide6 front-end. Analysis has been split out to the rbl-analysis repo.
"""
import sys
import logging

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QTabBar, QStackedWidget, QMessageBox, QScrollArea,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette

from rbl.gui.motor_tab import MotorTab
from rbl.gui.logamp_tab import CurrentTab
from rbl.gui.amp_tab import AmpTab
from rbl.gui.funcgen_tab import FuncGenTab
from rbl.gui.overview_tab import OverviewTab
from rbl.gui.calibration_tab import CalibrationTab
from rbl.gui.vacuum_tab import VacuumTab
from rbl.state.beamline import Beamline


# ─── Main Window ──────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Right Beam Line DAQ")
        self.resize(1440, 920)
        # Keep the floor low so the whole app stays compressible in the
        # horizontal direction; each tab is wrapped in a scroll area (see
        # _wrap_scroll) so content that no longer fits scrolls instead of
        # pinning a large minimum window width.
        self.setMinimumSize(480, 400)

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
        self._outer_tabbar.addTab("Overview")
        self._outer_tabbar.addTab("HV Calibration")
        self._outer_tabbar.addTab("Vacuum")
        self._outer_tabbar.setExpanding(False)
        self._outer_tabbar.setDocumentMode(True)
        outer_layout.addWidget(self._outer_tabbar)

        self._outer_stack = QStackedWidget()
        outer_layout.addWidget(self._outer_stack, stretch=1)

        # Beamline: the single owner of every instrument (LabJack T7, Galil,
        # both DG1022Z) plus the state-snapshot layer built on top of them.
        # No tab constructs or owns a driver instance — see rbl/state/beamline.py.
        self.beamline = Beamline(self)

        # ── Pages: Motors (0), Current (1), Amplifiers (2), FuncGens (3) ───────
        self.motor_tab   = MotorTab(self.beamline, self)
        self.current_tab = CurrentTab(self)
        self.amp_tab     = AmpTab(self)
        self.funcgen_tab = FuncGenTab(self.beamline, self)
        self.overview_tab = OverviewTab(self.beamline, self)
        self.calibration_tab = CalibrationTab(self.beamline, self)
        self.vacuum_tab      = VacuumTab(self.beamline, self)
        # Each page goes inside a scroll area: when the window is narrowed past
        # what a tab's content can reflow to, a scrollbar appears rather than
        # forcing the window to stay wide. This is what makes the app
        # horizontally compressible while keeping every control reachable.
        self._outer_stack.addWidget(self._wrap_scroll(self.motor_tab))
        self._outer_stack.addWidget(self._wrap_scroll(self.current_tab))
        self._outer_stack.addWidget(self._wrap_scroll(self.amp_tab))
        self._outer_stack.addWidget(self._wrap_scroll(self.funcgen_tab))
        self._outer_stack.addWidget(self._wrap_scroll(self.overview_tab))
        self._outer_stack.addWidget(self._wrap_scroll(self.calibration_tab))
        self._outer_stack.addWidget(self._wrap_scroll(self.vacuum_tab))

        # ── Shared LabJack T7 ─────────────────────────────────────────────────
        #
        # ONE physical T7, owned by self.beamline, and ONE conversion of each
        # window it produces. Beamline splits that window into a LogAmpState
        # (AIN0-3, the log amps) and an AmpState (AIN6-13, the EEL5000
        # monitors); each tab subscribes to the one it renders. No tab sees
        # the raw payload, so no tab can convert volts a second way.
        self._lj_tabs = (self.current_tab, self.amp_tab, self.calibration_tab)

        for tab in self._lj_tabs:
            tab.lj_panel.connect_requested.connect(self._labjack_connect)
            tab.lj_panel.disconnect_requested.connect(self._labjack_disconnect)
        self.beamline.labjack_connected.connect(self._on_labjack_connected)
        self.beamline.labjack_disconnected_evt.connect(self._on_labjack_disconnected)
        self.beamline.logamps_changed.connect(self.current_tab.on_logamp_state)
        self.beamline.amps_changed.connect(self.amp_tab.on_amp_state)
        self.beamline.stream_error.connect(self._on_labjack_error)
        self.beamline.profile_changed.connect(self._on_profile_changed)

        # Profile selector in AmpTab drives profile switches; its single-channel
        # target selector drives which channel a single-channel profile streams.
        self.amp_tab.profile_change_requested.connect(self.beamline.set_stream_profile)
        self.amp_tab.single_channel_change_requested.connect(self.beamline.set_stream_channel)

        # Calibration tab forces CAL_PROFILE at run start and restores the
        # prior profile afterward, via the same profile_change_requested ->
        # beamline.set_stream_profile path AmpTab uses. It needs the RAW
        # (unconverted) window payload — amps_changed only carries the
        # already-converted kV/mA state — so it is wired to
        # Beamline.raw_window_ready instead of amps_changed.
        self.calibration_tab.profile_change_requested.connect(self.beamline.set_stream_profile)
        self.beamline.raw_window_ready.connect(self.calibration_tab.on_window)
        # A calibration run switching the stream profile out from under
        # AmpTab would corrupt the run if AmpTab's own Apply fired mid-run.
        self.calibration_tab.run_state_changed.connect(
            lambda running: self.amp_tab.set_profile_controls_enabled(not running)
        )

        # Motor poll data feeds Beamline, which derives MotorState (typed,
        # richer than slit_state) and republishes it. The beam-position
        # indicator renders from that: the log-amp currents only become
        # millimetres once you know where the slits are.
        self.motor_tab.raw_state_changed.connect(self.beamline.ingest_motor_poll)
        self.motor_tab.motors_disconnected.connect(self.beamline.motors_disconnected)
        self.beamline.motors_changed.connect(self.current_tab.on_motor_state)

        # Start on Stepper Motors
        self._outer_stack.setCurrentIndex(0)
        self._outer_tabbar.tabBarClicked.connect(self._on_outer_tab_clicked)

    # ── Layout helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _wrap_scroll(widget: QWidget) -> QScrollArea:
        """Put *widget* in a resizable scroll area.

        With widgetResizable=True the tab fills the viewport normally; only when
        the window shrinks below what the tab can reflow to do scrollbars appear.
        That decouples the window's minimum size from each tab's content width,
        which is what lets the app be compressed horizontally.
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    # ── Shared LabJack management ─────────────────────────────────────────────
    #
    # Beamline owns the LabJackT7 + stream worker and does the actual connect/
    # disconnect/profile-switch work; MainWindow's role here is purely GUI:
    # show a dialog on failure and fan the resulting events out to the tabs.

    def _labjack_connect(self, conn_type: str, identifier: str):
        try:
            self.beamline.connect_labjack(conn_type, identifier)
        except Exception as e:
            QMessageBox.critical(self, "LabJack connect failed", str(e))
            self.beamline.disconnect_labjack()

    def _labjack_disconnect(self):
        self.beamline.disconnect_labjack()

    def _on_labjack_connected(self, serial: str):
        for tab in self._lj_tabs:
            tab.on_labjack_connected(serial)

    def _on_labjack_disconnected(self):
        for tab in self._lj_tabs:
            tab.on_labjack_disconnected()

    def _on_labjack_error(self, msg: str):
        # Beamline has already torn the connection down; just surface it.
        for tab in self._lj_tabs:
            tab._on_error(msg)

    def _on_profile_changed(self, profile_name: str):
        for tab in self._lj_tabs:
            if hasattr(tab, "on_profile_changed"):
                tab.on_profile_changed(profile_name)

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
            self.funcgen_tab.close_session()
        except Exception:
            pass
        try:
            self.calibration_tab.shutdown()
        except Exception:
            pass
        try:
            self.vacuum_tab.shutdown()
        except Exception:
            pass
        try:
            self.beamline.shutdown()   # LabJack, Galil, both DG1022Z — one call
        except Exception:
            pass
        super().closeEvent(event)

    # ── Outer tab switching ───────────────────────────────────────────────────

    def _on_outer_tab_clicked(self, index: int):
        self._outer_stack.setCurrentIndex(index)


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    import os
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
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
