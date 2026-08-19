"""
Right Beam Line DAQ App — Native Desktop GUI
Hardware-only: Stepper Motors, Beam Current, Function Generators.
Run: python -m rbl.main

PySide6 front-end. Analysis has been split out to the rbl-analysis repo.
"""
import sys
import logging

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QTabBar, QStackedWidget, QMessageBox, QScrollArea, QSplitter,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPalette

from rbl.gui.motor_tab import MotorTab
from rbl.gui.logamp_tab import CurrentTab
from rbl.gui.amp_tab import AmpTab
from rbl.gui.funcgen_tab import FuncGenTab
from rbl.gui.overview_tab import OverviewTab
from rbl.gui.calibration_tab import CalibrationTab
from rbl.gui.load_characterization_tab import LoadCharacterizationTab
from rbl.gui.vacuum_tab import VacuumTab
from rbl.gui.profiler_tab import ProfilerTab
from rbl.gui.raster_planner_tab import RasterPlannerTab
from rbl.gui.camera_tab import CameraTab
from rbl.gui import theme
from rbl.hardware.camera_source import CameraSource
from rbl.services.beamline_snapshot import BeamlineSnapshotProvider
from rbl.services.session_recorder import SessionRecorder
from rbl.state.beamline import Beamline
from rbl import driver as ljm_driver


# ─── Split-aware tab bar ──────────────────────────────────────────────────────

class SplitTabBar(QTabBar):
    """QTabBar that emits a separate signal on right-click without changing the
    current tab (left-click keeps normal behaviour)."""
    tab_right_clicked = Signal(int)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            index = self.tabAt(event.pos())
            if index >= 0:
                self.tab_right_clicked.emit(index)
            # Do NOT call super() — prevents tabBarClicked / current-index change
        else:
            super().mousePressEvent(event)


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

        self._outer_tabbar = SplitTabBar()
        self._outer_tabbar.addTab("Stepper Motors")
        self._outer_tabbar.addTab("Beam Current")
        self._outer_tabbar.addTab("HV Amplifiers")
        self._outer_tabbar.addTab("Function Generators")
        self._outer_tabbar.addTab("Overview")
        self._outer_tabbar.addTab("Camera")
        self._outer_tabbar.addTab("HV Calibration")
        self._outer_tabbar.addTab("Load Characterization")
        self._outer_tabbar.addTab("Vacuum")
        self._outer_tabbar.addTab("Beam Profiler")
        self._outer_tabbar.addTab("Raster Planner")
        self._outer_tabbar.setExpanding(False)
        self._outer_tabbar.setDocumentMode(True)
        self._outer_tabbar.setToolTip("Left-click: switch tab  |  Right-click: open in split view")
        outer_layout.addWidget(self._outer_tabbar)

        # ── Prerequisite warning bar (hidden unless a driver is missing) ──
        self._prereq_bar = QWidget()
        self._prereq_bar.setStyleSheet(
            "background: #fff3cd; border-bottom: 1px solid #ffc107;"
        )
        self._prereq_layout = QVBoxLayout(self._prereq_bar)
        self._prereq_layout.setContentsMargins(8, 4, 8, 4)
        self._prereq_layout.setSpacing(2)
        self._prereq_bar.hide()
        outer_layout.addWidget(self._prereq_bar)
        # Populated dynamically by _refresh_driver_state(); each failure
        # gets a row with a label and (optionally) an Install button.

        # Content area: horizontal splitter with a primary stack (always
        # visible) and a secondary stack (right pane, hidden unless split).
        self._content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._content_splitter.setHandleWidth(8)
        self._content_splitter.setChildrenCollapsible(False)
        self._content_splitter.setStyleSheet(
            "QSplitter::handle {"
            f"  background: {theme.TRACK};"
            f"  border-left: 1px solid {theme.TRACK_EDGE};"
            f"  border-right: 1px solid {theme.TRACK_EDGE};"
            "}"
            "QSplitter::handle:hover   { background: #0078d7; }"
            "QSplitter::handle:pressed { background: #005a9e; }"
        )
        outer_layout.addWidget(self._content_splitter, stretch=1)

        self._outer_stack = QStackedWidget()
        self._outer_stack.setMinimumSize(0, 0)  # let splitter compress freely
        self._content_splitter.addWidget(self._outer_stack)

        self._split_stack = QStackedWidget()
        self._split_stack.setMinimumSize(0, 0)
        self._content_splitter.addWidget(self._split_stack)
        self._split_stack.hide()

        # -1 = not in split mode; otherwise = tab index shown in right pane
        self._split_index: int = -1
        self._split_scroll: QScrollArea | None = None

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
        self.load_char_tab   = LoadCharacterizationTab(self.beamline, self)
        self.vacuum_tab      = VacuumTab(self.beamline, self)
        self.profiler_tab    = ProfilerTab(self.beamline, self)
        self.raster_planner_tab = RasterPlannerTab(self)

        # Session recorder — shared between Overview panel and Camera tab.
        self.snapshots        = BeamlineSnapshotProvider(self.beamline, self)
        self.camera_source    = CameraSource(self)
        self.session_recorder = SessionRecorder(
            self.snapshots.snapshot, self.camera_source, self)
        self.overview_tab.attach_recorder(self.session_recorder)
        self.camera_tab = CameraTab(self.session_recorder, self.camera_source, self)

        # Each page goes inside a scroll area: when the window is narrowed past
        # what a tab's content can reflow to, a scrollbar appears rather than
        # forcing the window to stay wide. This is what makes the app
        # horizontally compressible while keeping every control reachable.
        self._outer_stack.addWidget(self._wrap_scroll(self.motor_tab))
        self._outer_stack.addWidget(self._wrap_scroll(self.current_tab))
        self._outer_stack.addWidget(self._wrap_scroll(self.amp_tab))
        self._outer_stack.addWidget(self._wrap_scroll(self.funcgen_tab))
        self._outer_stack.addWidget(self._wrap_scroll(self.overview_tab))
        self._outer_stack.addWidget(self._wrap_scroll(self.camera_tab))
        self._outer_stack.addWidget(self._wrap_scroll(self.calibration_tab))
        self._outer_stack.addWidget(self._wrap_scroll(self.load_char_tab))
        self._outer_stack.addWidget(self._wrap_scroll(self.vacuum_tab))
        self._outer_stack.addWidget(self._wrap_scroll(self.profiler_tab))
        self._outer_stack.addWidget(self._wrap_scroll(self.raster_planner_tab))

        # ── Shared LabJack T7 ─────────────────────────────────────────────────
        #
        # ONE physical T7, owned by self.beamline, and ONE conversion of each
        # window it produces. Beamline splits that window into a LogAmpState
        # (AIN0-3, the log amps) and an AmpState (AIN6-13, the EEL5000
        # monitors); each tab subscribes to the one it renders. No tab sees
        # the raw payload, so no tab can convert volts a second way.
        self._lj_tabs = (self.current_tab, self.amp_tab, self.calibration_tab, self.load_char_tab)

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
        # Pair profiles set profile + target together in one restart.
        self.amp_tab.pair_profile_requested.connect(self.beamline.set_stream_pair_profile)
        # Funcgen readback into the amp tab so its table can show what each
        # amplifier was ASKED for beside what it is doing. Readback rather than
        # command intent, so it also catches a command that failed to take.
        self.beamline.funcgens_changed.connect(self.amp_tab.on_funcgens_changed)

        # Calibration tab forces CAL_PROFILE at run start and restores the
        # prior profile afterward, via the same profile_change_requested ->
        # beamline.set_stream_profile path AmpTab uses. It needs the RAW
        # (unconverted) window payload — amps_changed only carries the
        # already-converted kV/mA state — so it is wired to
        # Beamline.raw_window_ready instead of amps_changed.
        self.calibration_tab.profile_change_requested.connect(self.beamline.set_stream_profile)
        # A per-channel sweep re-points AMP_PAIR at each amplifier as it comes
        # up in the sequence; the runner asks, the beamline owns the handle.
        self.calibration_tab.pair_change_requested.connect(self.beamline.set_stream_pair)
        # Run start and run end acquire/restore profile + pair together, in one
        # stream restart. Two separate requests were unreliable from some prior
        # stream states — see Beamline.set_stream_pair_profile.
        self.calibration_tab.pair_profile_requested.connect(
            self.beamline.set_stream_pair_profile)
        self.beamline.raw_window_ready.connect(self.calibration_tab.on_window)
        # A calibration run switching the stream profile out from under
        # AmpTab would corrupt the run if AmpTab's own Apply fired mid-run.
        self.calibration_tab.run_state_changed.connect(
            lambda running: self.amp_tab.set_profile_controls_enabled(not running)
        )
        # An over-current trip freezes AmpTab's plot and stops it ingesting, so
        # the current trace leading up to the trip survives long enough to be
        # read. Without this the interlock aborts the run, the stream keeps
        # flowing, and the excursion scrolls out of the history before anyone
        # can look at it — worst in RMS mode, where each point is a whole
        # window's average and the peak is averaged away as well.
        self.calibration_tab.overcurrent_tripped.connect(
            lambda amp, ma, limit: self.amp_tab.freeze_on_safety_event(
                f"over-current on {amp}: {ma:.1f} mA (limit {limit:.0f} mA)"
            )
        )

        # Load Characterization tab (Phase 1): same profile/pair/raw-window
        # wiring as the calibration tab, for the same reason — it needs the
        # RAW per-monitor waveform to compute lock-in fundamentals, not the
        # already-converted kV/mA snapshots.
        self.load_char_tab.profile_change_requested.connect(self.beamline.set_stream_profile)
        self.load_char_tab.pair_profile_requested.connect(
            self.beamline.set_stream_pair_profile)
        self.beamline.raw_window_ready.connect(self.load_char_tab.on_window)
        self.load_char_tab.run_state_changed.connect(
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
        self._outer_tabbar.tab_right_clicked.connect(self._on_tab_right_clicked)

        # ── Driver preflight check ───────────────────────────────────────
        self._refresh_driver_state()

    # ── Layout helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _wrap_scroll(widget: QWidget) -> QScrollArea:
        """Put *widget* in a resizable scroll area.

        With widgetResizable=True the tab fills the viewport normally; only when
        the viewport shrinks below the content's minimumSizeHint do scrollbars
        appear.  setMinimumSize(0, 0) removes the scroll area's own minimum so
        the splitter (in split-screen mode) can compress either pane freely
        without the boundary overlapping the neighbouring pane.
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setMinimumSize(0, 0)
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
            # If the connect failed because the driver is missing, let the
            # driver warning bar (with its actionable Install button) win
            # over the generic error dialog the user just dismissed.
            self._refresh_driver_state()

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

    # ── Prerequisite checks: detect missing drivers, offer one-click install ─

    def _refresh_driver_state(self) -> None:
        """Check every system-level prerequisite and populate the warning bar.

        Each missing dependency gets its own row with a message and, when a
        bundled installer is available, an Install button.  The bar hides
        itself entirely when everything is present.
        """
        # Clear previous rows
        while self._prereq_layout.count():
            item = self._prereq_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        failures = ljm_driver.check_all()
        if not failures:
            self._prereq_bar.hide()
            return

        _DOWNLOAD_HINTS = {
            "ljm":    "install the LJM software from labjack.com",
            "visa":   "install NI-VISA Runtime from ni.com/visa",
            "serial": "install the driver for your USB-serial adapter",
        }

        for failure in failures:
            row = QWidget()
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(0, 0, 0, 0)
            row_lay.setSpacing(8)

            lbl = QLabel()
            lbl.setStyleSheet("color: #664d03; font-weight: bold;")

            installer = failure["installer"]
            key = failure["key"]

            if installer:
                lbl.setText(failure["message"]
                            + f"  —  click 'Install {failure['name']}'")
                btn = QPushButton(f"Install {failure['name']}")
                btn.setToolTip(f"Launch the bundled {failure['name']} installer "
                               "(requires admin).")
                # Capture installer path and name in the lambda closure
                btn.clicked.connect(
                    lambda _checked=False, p=installer, n=failure["name"]:
                        self._install_prerequisite(p, n)
                )
                row_lay.addWidget(lbl, stretch=1)
                row_lay.addWidget(btn)
            else:
                hint = _DOWNLOAD_HINTS.get(key, "install the required driver")
                lbl.setText(failure["message"] + f"  —  {hint}.")
                row_lay.addWidget(lbl, stretch=1)

            self._prereq_layout.addWidget(row)

        self._prereq_bar.show()

    def _install_prerequisite(self, path: str, name: str) -> None:
        """Launch a bundled installer with a UAC prompt, then update the bar."""
        if QMessageBox.question(
                self, f"Install {name}",
                f"This will launch the {name} installer.\n\n"
                "Windows will ask for administrator permission.  When it "
                "finishes, close and reopen RBL so it can find the "
                f"driver.\n\nContinue?") != QMessageBox.StandardButton.Yes:
            return

        if ljm_driver.launch_installer(path):
            QMessageBox.information(
                self, f"Install {name}",
                f"{name} installer launched — finish it, then restart RBL.")
            # Re-check; the install is async so the driver may still appear
            # missing until the user restarts, but re-checking is harmless.
            self._refresh_driver_state()
        else:
            QMessageBox.warning(
                self, f"Install {name}",
                "Could not launch the installer:\n" + path)

    # ── Close ─────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        try:
            if self.session_recorder.is_recording():
                self.session_recorder.stop()
        except Exception:
            pass
        try:
            self.camera_source.close()
        except Exception:
            pass
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
            self.load_char_tab.shutdown()
        except Exception:
            pass
        try:
            self.vacuum_tab.shutdown()
        except Exception:
            pass
        try:
            self.profiler_tab.shutdown()
        except Exception:
            pass
        try:
            self.beamline.shutdown()   # LabJack, Galil, both DG1022Z — one call
        except Exception:
            pass
        super().closeEvent(event)

    # ── Outer tab switching ───────────────────────────────────────────────────

    def _on_outer_tab_clicked(self, index: int):
        if self._split_index == index:
            # Left-click on the right-pane tab → collapse split, show full-screen
            self._exit_split(show_in_left=True)
        else:
            self._outer_stack.setCurrentIndex(self._stack_index(index))

    def _on_tab_right_clicked(self, index: int):
        if self._split_index == index:
            # Right-click same tab again → dismiss split
            self._exit_split(show_in_left=False)
        elif self._split_index >= 0:
            # Already split — change the right pane to a different tab
            self._exit_split(show_in_left=False)
            self._enter_split(index)
        else:
            if index == self._outer_stack.currentIndex():
                return  # can't split the same tab onto both sides
            self._enter_split(index)

    def _enter_split(self, index: int):
        """Move the scroll area at *index* into the right split pane.

        Uses QStackedWidget.removeWidget / addWidget — the documented safe way
        to migrate a widget between stacked widgets without touching Qt's
        internal viewport/scroll-area bookkeeping.
        """
        left_index = self._outer_stack.currentIndex()

        scroll = self._outer_stack.widget(index)
        self._outer_stack.removeWidget(scroll)
        # Removing index shifts all positions above it down by 1 in _outer_stack
        if left_index > index:
            self._outer_stack.setCurrentIndex(left_index - 1)

        self._split_scroll = scroll
        self._split_stack.addWidget(scroll)
        self._split_stack.setCurrentWidget(scroll)
        self._split_stack.show()

        total = self._content_splitter.width()
        half = max(total // 2, 300)
        self._content_splitter.setSizes([half, half])

        self._split_index = index
        self._mark_split_tab(index)

    def _exit_split(self, *, show_in_left: bool):
        """Restore the right-pane scroll area back to _outer_stack."""
        if self._split_index < 0:
            return

        left_index = self._outer_stack.currentIndex()

        scroll = self._split_scroll
        self._split_stack.removeWidget(scroll)
        self._split_stack.hide()

        # Re-insert at the original position; positions >= split_index shift up
        self._outer_stack.insertWidget(self._split_index, scroll)
        if left_index >= self._split_index:
            left_index += 1

        old_index = self._split_index
        self._split_index = -1
        self._split_scroll = None
        self._mark_split_tab(-1)

        if show_in_left:
            self._outer_stack.setCurrentIndex(old_index)
            self._outer_tabbar.setCurrentIndex(old_index)
        else:
            self._outer_stack.setCurrentIndex(left_index)

    def _stack_index(self, tab_index: int) -> int:
        """Map a tab-bar index to the current _outer_stack index.

        While in split mode the scroll area for self._split_index has been
        removed from _outer_stack, so every tab-bar position above that slot
        is shifted down by one inside the stack.
        """
        if self._split_index < 0 or tab_index < self._split_index:
            return tab_index
        return tab_index - 1

    def _mark_split_tab(self, index: int):
        """Colour the right-pane tab blue; reset all others to default."""
        blue = QColor(0, 120, 215)
        default = QColor()  # invalid = use palette default
        for i in range(self._outer_tabbar.count()):
            self._outer_tabbar.setTabTextColor(i, blue if i == index else default)


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
