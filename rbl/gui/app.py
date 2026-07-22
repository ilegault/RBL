"""
Right Beam Line DAQ App — Native Desktop GUI
Hardware-only: Stepper Motors, Beam Current, Function Generators.
Run: python app.py   (from inside the rbl/gui/ directory)

PySide6 front-end. Analysis has been split out to the rbl-analysis repo.
"""
import sys
import os
import time
import atexit
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QTabBar, QStackedWidget, QMessageBox,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette

from rbl.hardware.labjack_driver import LabJackT7
from rbl.hardware.labjack_stream_worker import LabJackStreamWorker
from rbl.config.labjack_stream_config import (
    DEFAULT_PROFILE, STREAM_PROFILES, DEFAULT_SINGLE_CHANNEL,
    SINGLE_CHANNEL_CHOICES, is_single_channel,
)


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
        from logamp_tab import CurrentTab
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
        # ONE physical T7 -> ONE LabJackT7 instance -> ONE stream worker reading
        # all channels at high rate. Both the Beam Current tab (AIN0-3, log amps)
        # and the HV Amplifier tab (AIN6-13, EEL5000 monitors) subscribe to the
        # same window_ready signal and filter for their own channels.
        # Never let a tab open its own handle.
        self._lj              = LabJackT7()
        self._lj_worker       = None
        self._active_profile  = DEFAULT_PROFILE
        # Target channel for single-channel profiles (SINGLE_FAST/SINGLE_HIRES).
        # Ignored while a multi-channel profile is active.
        self._active_channel  = DEFAULT_SINGLE_CHANNEL
        self._profile_updating = False   # re-entrancy guard for set_profile / set_channel
        # Shared monotonic epoch for every stream worker this connection spawns.
        # Set on connect so payload timestamps stay continuous across the
        # stop/reconfigure/start cycles that profile and channel switches need.
        self._stream_t0       = None
        self._lj_tabs         = (self.current_tab, self.amp_tab)

        # Last-resort safety net: if the process is torn down without a clean
        # closeEvent (e.g. an unhandled exit), still stop the stream and close
        # the handle so the T7 is never left in stream mode.
        atexit.register(self._emergency_labjack_shutdown)

        for tab in self._lj_tabs:
            tab.lj_panel.connect_requested.connect(self._labjack_connect)
            tab.lj_panel.disconnect_requested.connect(self._labjack_disconnect)

        # Profile selector in AmpTab drives profile switches; its single-channel
        # target selector drives which channel a single-channel profile streams.
        self.amp_tab.profile_change_requested.connect(self._set_stream_profile)
        self.amp_tab.single_channel_change_requested.connect(self._set_stream_channel)

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
            self._stream_t0 = time.monotonic()   # anchor the shared timeline
            self._start_stream_worker(self._active_profile)
            for tab in self._lj_tabs:
                tab.on_labjack_connected(serial)
        except Exception as e:
            QMessageBox.critical(self, "LabJack connect failed", str(e))
            self._labjack_disconnect()

    def _start_stream_worker(self, profile_name: str):
        """Create and start a stream worker for *profile_name*.

        For single-channel profiles the current channel target is passed as the
        override.  Caller is responsible for stopping any existing worker first.
        """
        override = self._active_channel if is_single_channel(profile_name) else None
        worker = LabJackStreamWorker(
            self._lj.handle, profile_name, override, t0=self._stream_t0
        )
        for tab in self._lj_tabs:
            worker.window_ready.connect(tab._on_window)
            worker.error.connect(tab._on_error)
        worker.error.connect(self._on_labjack_error)
        worker.start()
        self._lj_worker = worker

    def _set_stream_profile(self, profile_name: str):
        """Stop the running stream, reconfigure, and restart with a new profile.

        Hardware constraint: the T7 scan list cannot be changed mid-stream.
        A full eStreamStop -> reconfigure -> eStreamStart cycle is required.
        This is user-driven and takes ~tens of ms — never call mid-capture.
        """
        if self._profile_updating:
            return   # ignore re-entrant call while a switch is in progress
        if profile_name not in STREAM_PROFILES:
            return

        # Remember the request even while disconnected so it takes effect on the
        # next connect (the stream worker is started from _active_profile).
        changed = (profile_name != self._active_profile)
        self._active_profile = profile_name
        if not self._lj.connected or self._lj_worker is None:
            return
        if not changed:
            return

        self._profile_updating = True
        try:
            self._restart_stream_worker(profile_name)

            for tab in self._lj_tabs:
                if hasattr(tab, "on_profile_changed"):
                    tab.on_profile_changed(profile_name)
        finally:
            self._profile_updating = False

    def _set_stream_channel(self, ain_name: str):
        """Change which channel a single-channel profile streams.

        No-op unless a single-channel profile is active.  Like a profile
        switch, changing the scan list requires a full stop -> reconfigure ->
        start cycle (the T7 cannot change its scan list mid-stream).
        """
        if self._profile_updating:
            return
        if ain_name not in SINGLE_CHANNEL_CHOICES:
            return   # not a valid single-channel target

        # Remember the target regardless of the active profile so a later switch
        # to a single-channel profile starts on the channel the user picked.
        changed = (ain_name != self._active_channel)
        self._active_channel = ain_name
        if not changed:
            return
        if not self._lj.connected or self._lj_worker is None:
            return   # remembered; applied when a single-channel profile starts
        if not is_single_channel(self._active_profile):
            return   # remembered; the live scan list is fixed in multi-channel mode

        self._profile_updating = True
        try:
            self._restart_stream_worker(self._active_profile)
        finally:
            self._profile_updating = False

    def _restart_stream_worker(self, profile_name: str):
        """Stop the running worker (if any) and start a fresh one.

        Hardware constraint: the T7 scan list cannot be changed mid-stream, so
        both profile switches and single-channel target changes go through this
        stop -> reconfigure -> start cycle.  Callers hold _profile_updating.
        """
        if self._lj_worker is not None:
            self._lj_worker.stop()
            self._lj_worker.wait(5000)
            self._lj_worker = None
        self._start_stream_worker(profile_name)

    def _labjack_disconnect(self):
        # Stop the stream before closing the handle (hardware order matters).
        if self._lj_worker is not None:
            self._lj_worker.stop()
            if not self._lj_worker.wait(3000):
                # The drain thread did not exit in time (e.g. blocked on a slow
                # eStreamRead).  Fall through anyway: LabJackT7.disconnect()
                # force-stops the stream on the handle before closing it, so the
                # device is never left streaming even in this degraded case.
                pass
            self._lj_worker = None
        self._stream_t0 = None
        self._lj.disconnect()   # force-stops the stream, then closes the handle
        for tab in self._lj_tabs:
            tab.on_labjack_disconnected()

    def _emergency_labjack_shutdown(self):
        """atexit safety net — never leave the T7 in stream mode.

        Runs at interpreter exit for any path that skipped closeEvent.  It must
        not raise; a best-effort stream stop + handle close is all that matters.
        """
        try:
            if self._lj_worker is not None:
                self._lj_worker.stop()
                self._lj_worker.wait(2000)
                self._lj_worker = None
        except Exception:
            pass
        try:
            self._lj.stop_stream()   # explicit, in case the worker never ran finally
            self._lj.disconnect()
        except Exception:
            pass

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
