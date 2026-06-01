"""
motor_tab.py
PySide6 widget for the "Stepper Motors" outer tab.

Connects to the Galil DMC-4103 over TCP, polls all 4 axes at ~5 Hz, and
exposes jog / move / stop / define-zero controls per axis.

Safety:
  - Big red EMERGENCY STOP at the top, always visible.
  - On widget shutdown(), poll thread stops and motors are NOT auto-aborted
    (user may close the tab while a planned move runs). closeEvent of the
    main window calls .abort_and_close() instead, which DOES abort.
"""
import time

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QPushButton, QLineEdit, QSpinBox, QTextEdit, QFormLayout,
    QMessageBox,
)

from rbl.hardware.galil_driver import GalilController, GalilError
from rbl.hardware import slit_config as SC


# --- Background poll thread --------------------------------------------------

class GalilPollWorker(QThread):
    """Polls all 4 axes at ~5 Hz, emits a dict of state."""
    state = Signal(dict)   # {axis: {"pos": int, "moving": bool, "switches": {...}}}
    error = Signal(str)

    def __init__(self, galil: GalilController, period_s: float = 0.2):
        super().__init__()
        self.galil    = galil
        self.period   = period_s
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        while self._running and self.galil.connected:
            t0 = time.time()
            try:
                snapshot = {}
                for axis in SC.AXIS_LETTERS:
                    snapshot[axis] = {
                        "pos":      self.galil.get_position(axis),
                        "moving":   self.galil.is_moving(axis),
                        "switches": self.galil.get_switch_states(axis),
                    }
                self.state.emit(snapshot)
            except Exception as e:
                self.error.emit(str(e))
                break
            # sleep the remainder of the period
            elapsed = time.time() - t0
            remaining = max(0.0, self.period - elapsed)
            self.msleep(int(remaining * 1000))


# --- Per-axis control widget -------------------------------------------------

class AxisControls(QGroupBox):
    """One self-contained panel for one slit jaw."""

    def __init__(self, axis_letter: str, get_galil_fn, log_fn, parent=None):
        super().__init__(f"{SC.AXIS_NAMES[axis_letter]}  (axis {axis_letter})", parent)
        self.axis      = axis_letter
        self.get_galil = get_galil_fn      # callable -> GalilController instance
        self.log       = log_fn            # callable(str) for the command console

        layout = QFormLayout(self)
        layout.setSpacing(4)

        # --- Live readouts ----------------------------------------------------
        self.lbl_counts  = QLabel("—")
        self.lbl_mm      = QLabel("—")
        self.lbl_status  = QLabel("Disconnected")
        self.lbl_status.setStyleSheet("color: #888;")
        self.lbl_switches = QLabel("F:— R:— H:—")
        self.lbl_softlim = QLabel("BL:— FL:—")
        layout.addRow("Position (counts):", self.lbl_counts)
        layout.addRow("Position (mm):",     self.lbl_mm)
        layout.addRow("Status:",            self.lbl_status)
        layout.addRow("Switches:",          self.lbl_switches)
        layout.addRow("Soft limits:",       self.lbl_softlim)

        # --- Jog row ----------------------------------------------------------
        jog_row = QHBoxLayout()
        self.btn_jog_neg = QPushButton("◀ Jog −")
        self.btn_jog_pos = QPushButton("Jog + ▶")
        self.btn_jog_neg.pressed.connect(lambda: self._jog(-1))
        self.btn_jog_neg.released.connect(self._stop)
        self.btn_jog_pos.pressed.connect(lambda: self._jog(+1))
        self.btn_jog_pos.released.connect(self._stop)
        jog_row.addWidget(self.btn_jog_neg)
        jog_row.addWidget(self.btn_jog_pos)
        layout.addRow("Jog:", jog_row)

        # --- Absolute move ----------------------------------------------------
        move_row = QHBoxLayout()
        self.spn_target = QSpinBox()
        self.spn_target.setRange(-10_000_000, 10_000_000)
        self.spn_target.setValue(0)
        self.spn_target.setSuffix(" counts")
        self.btn_move = QPushButton("Move to")
        self.btn_move.clicked.connect(self._move_absolute)
        move_row.addWidget(self.spn_target)
        move_row.addWidget(self.btn_move)
        layout.addRow("Target:", move_row)

        # --- Stop / Zero / Speed ---------------------------------------------
        ctrl_row = QHBoxLayout()
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(self._stop)
        self.btn_zero = QPushButton("Define here as 0")
        self.btn_zero.clicked.connect(self._define_zero)
        ctrl_row.addWidget(self.btn_stop)
        ctrl_row.addWidget(self.btn_zero)
        layout.addRow(ctrl_row)

        self.spn_speed = QSpinBox()
        self.spn_speed.setRange(1, 1_000_000)
        self.spn_speed.setValue(SC.DEFAULT_JOG_SPEED)
        self.spn_speed.setSuffix(" cps")
        layout.addRow("Jog speed:", self.spn_speed)

        # Disable all buttons until connected
        self.set_enabled(False)

    def set_enabled(self, on: bool):
        for w in (self.btn_jog_neg, self.btn_jog_pos, self.btn_stop,
                  self.btn_move, self.btn_zero, self.spn_target,
                  self.spn_speed):
            w.setEnabled(on)

    # ---- GUI -> Galil action handlers ---------------------------------------

    def _jog(self, direction: int):
        g = self.get_galil()
        if g is None or not g.connected:
            return
        try:
            speed = self.spn_speed.value() * (1 if direction > 0 else -1)
            self.log(f"> JG {self.axis}={speed} ; BG {self.axis}")
            g.jog_start(self.axis, speed)
        except (GalilError, ConnectionError) as e:
            self.log(f"! {e}")

    def _stop(self):
        g = self.get_galil()
        if g is None or not g.connected:
            return
        try:
            self.log(f"> ST {self.axis}")
            g.stop(self.axis)
        except (GalilError, ConnectionError) as e:
            self.log(f"! {e}")

    def _move_absolute(self):
        g = self.get_galil()
        if g is None or not g.connected:
            return
        target = self.spn_target.value()
        try:
            self.log(f"> PA {self.axis}={target} ; BG {self.axis}")
            g.move_absolute(self.axis, target)
        except GalilError as e:
            self.log(f"! {e}")
            if e.code == 22:
                QMessageBox.warning(self, "Soft-limit hit",
                                    f"{self.axis} target {target} cts is beyond the burned soft limit.")
        except ConnectionError as e:
            self.log(f"! {e}")

    def _define_zero(self):
        g = self.get_galil()
        if g is None or not g.connected:
            return
        try:
            self.log(f"> DP {self.axis}=0")
            g.define_zero(self.axis)
        except (GalilError, ConnectionError) as e:
            self.log(f"! {e}")

    # ---- State update slot --------------------------------------------------

    def update_state(self, axis_state: dict):
        pos = axis_state["pos"]
        self.lbl_counts.setText(f"{pos:,}")
        self.lbl_mm.setText(f"{SC.counts_to_mm(self.axis, pos):+.4f} mm")
        if axis_state["moving"]:
            self.lbl_status.setText("Moving")
            self.lbl_status.setStyleSheet("color: #FF8800; font-weight: bold;")
        else:
            self.lbl_status.setText("Idle")
            self.lbl_status.setStyleSheet("color: #00FF00;")
        sw = axis_state["switches"]
        def fmt(b): return "●" if b else "○"
        self.lbl_switches.setText(
            f"F:{fmt(sw['forward_switch'])} "
            f"R:{fmt(sw['reverse_switch'])} "
            f"H:{fmt(sw['home_switch'])}"
        )

    def update_soft_limits(self, fl_counts: int, bl_counts: int):
        self.lbl_softlim.setText(f"BL:{bl_counts:,}   FL:{fl_counts:,}")


# --- Top-level tab widget ----------------------------------------------------

class MotorTab(QWidget):
    """The "Stepper Motors" outer tab."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.galil  = GalilController()
        self.worker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Connection bar ──────────────────────────────────────────────────
        conn_box = QGroupBox("Galil DMC-4103 Connection")
        conn = QHBoxLayout(conn_box)
        conn.addWidget(QLabel("IP:"))
        self.ip_edit = QLineEdit("192.168.1.10")
        self.ip_edit.setMaximumWidth(140)
        conn.addWidget(self.ip_edit)
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.clicked.connect(self._toggle_connection)
        conn.addWidget(self.btn_connect)
        self.lbl_conn_status = QLabel("● Disconnected")
        self.lbl_conn_status.setStyleSheet("color: #888888; font-weight: bold;")
        conn.addWidget(self.lbl_conn_status)
        self.lbl_model = QLabel("")
        self.lbl_model.setStyleSheet("color: #888; font-style: italic;")
        conn.addWidget(self.lbl_model, stretch=1)
        layout.addWidget(conn_box)

        # ── Emergency stop ──────────────────────────────────────────────────
        estop_row = QHBoxLayout()
        self.btn_estop = QPushButton("EMERGENCY STOP (AB)")
        self.btn_estop.setMinimumHeight(44)
        self.btn_estop.setStyleSheet(
            "QPushButton { background:#aa0000; color:white; font-size:15px;"
            " font-weight:bold; border:2px solid #cc0000; }"
            "QPushButton:hover { background:#cc0000; }"
        )
        self.btn_estop.clicked.connect(self._emergency_stop)
        estop_row.addWidget(self.btn_estop, stretch=3)
        self.btn_enable  = QPushButton("Enable All (SH ABCD)")
        self.btn_disable = QPushButton("Disable All (MO ABCD)")
        self.btn_enable.clicked.connect(lambda: self._do(lambda g: g.enable("ABCD")))
        self.btn_disable.clicked.connect(lambda: self._do(lambda g: g.disable("ABCD")))
        estop_row.addWidget(self.btn_enable,  stretch=1)
        estop_row.addWidget(self.btn_disable, stretch=1)
        layout.addLayout(estop_row)

        # ── 4 axis panels in 2x2 grid ───────────────────────────────────────
        grid = QGridLayout()
        grid.setSpacing(6)
        self.axes = {}
        for i, axis in enumerate(SC.AXIS_LETTERS):
            panel = AxisControls(axis, lambda: self.galil, self._log_line, self)
            self.axes[axis] = panel
            grid.addWidget(panel, i // 2, i % 2)
        layout.addLayout(grid, stretch=1)

        # ── Console ────────────────────────────────────────────────────────
        cons_box = QGroupBox("Command Console")
        cons = QVBoxLayout(cons_box)
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setFont(QFont("Menlo", 9))
        self.console.setMaximumHeight(140)
        cons.addWidget(self.console)
        manual_row = QHBoxLayout()
        self.manual_cmd = QLineEdit()
        self.manual_cmd.setPlaceholderText("Manual DMC command (e.g. MG _RPA, TH, LS)")
        self.manual_cmd.returnPressed.connect(self._send_manual)
        self.btn_send = QPushButton("Send")
        self.btn_send.clicked.connect(self._send_manual)
        manual_row.addWidget(self.manual_cmd, stretch=1)
        manual_row.addWidget(self.btn_send)
        cons.addLayout(manual_row)
        layout.addWidget(cons_box)

        self._set_buttons_connected(False)

    # ---- Connection lifecycle ------------------------------------------------

    def _toggle_connection(self):
        if self.galil.connected:
            self._do_disconnect()
        else:
            self._do_connect()

    def _do_connect(self):
        ip = self.ip_edit.text().strip()
        try:
            self._log_line(f"# Connecting to {ip}:23 ...")
            self.galil.connect(ip)
            self._log_line(f"# Connected.")
            try:
                model = self.galil.model_info()
                self.lbl_model.setText(model)
                self._log_line(f"> TH\n< {model}")
            except Exception as e:
                self._log_line(f"! TH failed: {e}")
            self._log_line("# Startup sequence: CN 1, SH ABCD, SP, AC, DC ...")
            self.galil.startup_sequence(
                axes="ABCD",
                speed=SC.DEFAULT_SPEED_COUNTS_PER_SEC,
                accel=SC.DEFAULT_ACCEL_COUNTS_PER_SEC2,
            )
            # Read soft limits once and display
            for axis, panel in self.axes.items():
                try:
                    lim = self.galil.get_soft_limits(axis)
                    panel.update_soft_limits(lim["forward_counts"], lim["back_counts"])
                except Exception as e:
                    self._log_line(f"! Could not read soft limits for {axis}: {e}")
            # Start poll thread
            self.worker = GalilPollWorker(self.galil, period_s=0.2)
            self.worker.state.connect(self._on_state)
            self.worker.error.connect(self._on_poll_error)
            self.worker.start()
            self._set_buttons_connected(True)
        except Exception as e:
            self._log_line(f"! Connect failed: {e}")
            QMessageBox.critical(self, "Galil connection failed", str(e))

    def _do_disconnect(self):
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(2000)
            self.worker = None
        self.galil.disconnect()
        self._set_buttons_connected(False)
        self.lbl_model.setText("")
        self._log_line("# Disconnected.")

    def _set_buttons_connected(self, on: bool):
        self.btn_connect.setText("Disconnect" if on else "Connect")
        self.lbl_conn_status.setText("● Connected" if on else "● Disconnected")
        self.lbl_conn_status.setStyleSheet(
            "color: #00FF00; font-weight: bold;" if on else "color: #888888; font-weight: bold;"
        )
        self.btn_estop.setEnabled(on)
        self.btn_enable.setEnabled(on)
        self.btn_disable.setEnabled(on)
        self.btn_send.setEnabled(on)
        self.manual_cmd.setEnabled(on)
        for panel in self.axes.values():
            panel.set_enabled(on)

    # ---- Action helpers ------------------------------------------------------

    def _do(self, fn):
        """Run a galil action safely, logging exceptions."""
        if not self.galil.connected:
            return
        try:
            fn(self.galil)
        except Exception as e:
            self._log_line(f"! {e}")

    def _emergency_stop(self):
        if not self.galil.connected:
            return
        self._log_line("> AB  (EMERGENCY STOP)")
        self.galil.abort()

    def _send_manual(self):
        cmd = self.manual_cmd.text().strip()
        if not cmd or not self.galil.connected:
            return
        try:
            resp = self.galil.cmd(cmd)
            self._log_line(f"> {cmd}\n< {resp if resp else ':'}")
        except (GalilError, ConnectionError) as e:
            self._log_line(f"! {e}")
        self.manual_cmd.clear()

    # ---- Slots ---------------------------------------------------------------

    def _on_state(self, snapshot: dict):
        for axis, axis_state in snapshot.items():
            self.axes[axis].update_state(axis_state)

    def _on_poll_error(self, msg: str):
        self._log_line(f"! Poll thread error: {msg}")
        self._do_disconnect()

    def _log_line(self, line: str):
        ts = time.strftime("%H:%M:%S")
        self.console.append(f"[{ts}] {line}")

    # ---- Owner-callable cleanup ---------------------------------------------

    def abort_and_close(self):
        """Called by MainWindow.closeEvent. Stops motors and disconnects."""
        try:
            if self.galil.connected:
                self.galil.abort()
        except Exception:
            pass
        self._do_disconnect()


# Standalone smoke test
if __name__ == "__main__":
    import sys
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    w = MotorTab()
    w.resize(900, 700)
    w.show()
    print("[OK] motor_tab loads")
    sys.exit(app.exec())
