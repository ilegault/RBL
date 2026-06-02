"""
motor_tab.py
PySide6 widget for the "Stepper Motors" outer tab.

Features:
  - Per-axis jog/move/stop/zero controls.
  - Jog speed and target position in cps OR mm/s / mm (unit toggle).
  - Per-axis Enable (SH) / Disable (MO) buttons; also single-axis and all-axis.
  - Automated homing with progressive speed retry (900 → 450 → 225 cps).
  - Big red EMERGENCY STOP always visible.
"""
import time

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QPushButton, QDoubleSpinBox, QTextEdit, QFormLayout, QComboBox,
    QMessageBox,
)

from rbl.hardware.galil_driver import GalilController, GalilError
from rbl.hardware import slit_config as SC


# ─── Background poll thread ───────────────────────────────────────────────────

class GalilPollWorker(QThread):
    state = Signal(dict)
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
            elapsed   = time.time() - t0
            remaining = max(0.0, self.period - elapsed)
            self.msleep(int(remaining * 1000))


# ─── Auto-homing worker ───────────────────────────────────────────────────────

class HomingWorker(QThread):
    """Progressive-speed homing: 900 → 450 → 225 cps, backing up between tries."""
    progress = Signal(str)
    done     = Signal(bool, str)   # success, message

    _SPEEDS  = [900, 450, 225]
    _BACKUPS = [2000, 4000, 6000]  # counts to back up before each retry

    def __init__(self, galil: GalilController, axis: str, parent=None):
        super().__init__(parent)
        self.galil = galil
        self.axis  = axis
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        g    = self.galil
        axis = self.axis
        try:
            # If already on home switch, back off first
            sw = g.get_switch_states(axis)
            if sw["home_switch"]:
                self.progress.emit(f"{axis}: on home switch — backing off 2000 counts…")
                g.move_relative(axis, 2000)
                if not self._wait_idle(timeout=10.0):
                    self.done.emit(False, "Timeout while backing off home switch")
                    return

            for attempt, speed in enumerate(self._SPEEDS):
                if self._cancelled:
                    self.done.emit(False, "Homing cancelled by user")
                    return

                if attempt > 0:
                    backup = self._BACKUPS[attempt - 1]
                    self.progress.emit(f"{axis}: backing up {backup} counts before retry…")
                    g.move_relative(axis, backup)
                    if not self._wait_idle(timeout=15.0):
                        self.done.emit(False, f"Timeout on backup move (attempt {attempt+1})")
                        return

                self.progress.emit(
                    f"{axis}: attempt {attempt+1}/{len(self._SPEEDS)} — "
                    f"HM at {speed} cps "
                    f"({SC.cps_to_mm_per_sec(axis, speed):.2f} mm/s)…"
                )
                g.begin_home(axis, speed)
                time.sleep(0.5)   # let motion start

                if not self._wait_idle(timeout=30.0):
                    self.progress.emit(f"{axis}: HM timeout on attempt {attempt+1}")
                    g.stop(axis)
                    time.sleep(0.3)
                    continue

                sw = g.get_switch_states(axis)
                if sw["home_switch"]:
                    g.define_zero(axis)
                    g.set_speed(axis, SC.DEFAULT_SPEED_COUNTS_PER_SEC)
                    self.done.emit(True, f"{axis}: homed at {speed} cps, DP=0, SP restored")
                    return
                else:
                    self.progress.emit(f"{axis}: home switch not found on attempt {attempt+1}")

            # Restore speed even on failure
            try:
                g.set_speed(axis, SC.DEFAULT_SPEED_COUNTS_PER_SEC)
            except Exception:
                pass
            self.done.emit(
                False,
                f"{axis}: failed to home after {len(self._SPEEDS)} attempts"
            )

        except Exception as e:
            self.done.emit(False, f"{axis}: homing error — {e}")

    def _wait_idle(self, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._cancelled:
                return False
            try:
                if not self.galil.is_moving(self.axis):
                    return True
            except Exception:
                return False
            time.sleep(0.2)
        return False


# ─── Per-axis control groupbox ────────────────────────────────────────────────

class AxisControls(QGroupBox):
    """One self-contained panel for one slit jaw."""

    def __init__(self, axis_letter: str, get_galil_fn, log_fn, parent=None):
        super().__init__(f"{SC.AXIS_NAMES[axis_letter]}  (axis {axis_letter})", parent)
        self.axis      = axis_letter
        self.get_galil = get_galil_fn
        self.log       = log_fn
        self._homing_worker: HomingWorker | None = None

        layout = QFormLayout(self)
        layout.setSpacing(4)

        # --- Live readouts -----------------------------------------------
        self.lbl_counts  = QLabel("—")
        self.lbl_mm      = QLabel("—")
        self.lbl_status  = QLabel("Disconnected")
        self.lbl_status.setStyleSheet("color: #888;")
        self.lbl_switches = QLabel("F:— R:— H:—")
        self.lbl_softlim  = QLabel("BL:— FL:—")
        layout.addRow("Position (counts):", self.lbl_counts)
        layout.addRow("Position (mm):",     self.lbl_mm)
        layout.addRow("Status:",            self.lbl_status)
        layout.addRow("Switches:",          self.lbl_switches)
        layout.addRow("Soft limits:",       self.lbl_softlim)

        # --- Jog row -----------------------------------------------------
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

        # --- Jog speed with unit toggle ----------------------------------
        speed_row = QHBoxLayout()
        self.spn_speed = QDoubleSpinBox()
        self.spn_speed.setDecimals(2)
        self.cbo_speed_unit = QComboBox()
        self.cbo_speed_unit.addItems(["cps", "mm/s"])
        self.cbo_speed_unit.currentIndexChanged.connect(self._on_speed_unit_changed)
        speed_row.addWidget(self.spn_speed, stretch=1)
        speed_row.addWidget(self.cbo_speed_unit)
        layout.addRow("Jog speed:", speed_row)
        self._set_speed_unit_range("cps")
        self.spn_speed.setValue(SC.DEFAULT_JOG_SPEED)

        # --- Target move with unit toggle --------------------------------
        move_row = QHBoxLayout()
        self.spn_target = QDoubleSpinBox()
        self.spn_target.setDecimals(3)
        self.cbo_target_unit = QComboBox()
        self.cbo_target_unit.addItems(["counts", "mm"])
        self.cbo_target_unit.currentIndexChanged.connect(self._on_target_unit_changed)
        self.btn_move = QPushButton("Move to")
        self.btn_move.clicked.connect(self._move_absolute)
        move_row.addWidget(self.spn_target, stretch=1)
        move_row.addWidget(self.cbo_target_unit)
        move_row.addWidget(self.btn_move)
        layout.addRow("Target:", move_row)
        self._set_target_unit_range("counts")

        # --- Stop / Zero / Enable / Disable / Home -----------------------
        ctrl_row1 = QHBoxLayout()
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(self._stop)
        self.btn_zero = QPushButton("Define here as 0")
        self.btn_zero.clicked.connect(self._define_zero)
        ctrl_row1.addWidget(self.btn_stop)
        ctrl_row1.addWidget(self.btn_zero)
        layout.addRow(ctrl_row1)

        ctrl_row2 = QHBoxLayout()
        self.btn_enable_axis  = QPushButton(f"Enable {axis_letter}")
        self.btn_disable_axis = QPushButton(f"Disable {axis_letter}")
        self.btn_home         = QPushButton(f"Home {axis_letter}")
        self.btn_enable_axis.setStyleSheet(
            "QPushButton { background:#1a7000; color:white; font-weight:bold; }"
            "QPushButton:hover { background:#228a00; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        self.btn_disable_axis.setStyleSheet(
            "QPushButton { background:#8c5800; color:white; }"
            "QPushButton:hover { background:#a06600; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        self.btn_home.setStyleSheet(
            "QPushButton { background:#004e8c; color:white; font-weight:bold; }"
            "QPushButton:hover { background:#0063b1; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        self.btn_enable_axis.clicked.connect(self._enable_axis)
        self.btn_disable_axis.clicked.connect(self._disable_axis)
        self.btn_home.clicked.connect(self._start_homing)
        ctrl_row2.addWidget(self.btn_enable_axis)
        ctrl_row2.addWidget(self.btn_disable_axis)
        ctrl_row2.addWidget(self.btn_home)
        layout.addRow(ctrl_row2)

        self.set_enabled(False)

    # ---- Unit helpers -------------------------------------------------------

    def _set_speed_unit_range(self, unit: str):
        if unit == "cps":
            self.spn_speed.setRange(1.0, 100_000.0)
            self.spn_speed.setSingleStep(100.0)
            self.spn_speed.setSuffix(" cps")
        else:  # mm/s
            max_mms = SC.cps_to_mm_per_sec(self.axis, 100_000)
            self.spn_speed.setRange(0.01, max_mms)
            self.spn_speed.setSingleStep(0.1)
            self.spn_speed.setSuffix(" mm/s")

    def _set_target_unit_range(self, unit: str):
        if unit == "counts":
            self.spn_target.setRange(-10_000_000.0, 10_000_000.0)
            self.spn_target.setSingleStep(100.0)
            self.spn_target.setSuffix(" cts")
        else:  # mm
            max_mm = SC.counts_to_mm(self.axis, 10_000_000)
            self.spn_target.setRange(-max_mm, max_mm)
            self.spn_target.setSingleStep(0.1)
            self.spn_target.setSuffix(" mm")

    def _on_speed_unit_changed(self, idx: int):
        unit = self.cbo_speed_unit.itemText(idx)
        old  = self.spn_speed.value()
        self._set_speed_unit_range(unit)
        if unit == "mm/s":
            # convert old cps value to mm/s
            self.spn_speed.setValue(SC.cps_to_mm_per_sec(self.axis, old))
        else:
            # convert old mm/s value to cps
            self.spn_speed.setValue(float(SC.mm_per_sec_to_cps(self.axis, old)))

    def _on_target_unit_changed(self, idx: int):
        unit = self.cbo_target_unit.itemText(idx)
        old  = self.spn_target.value()
        self._set_target_unit_range(unit)
        if unit == "mm":
            # old was counts
            self.spn_target.setValue(SC.counts_to_mm(self.axis, old))
        else:
            # old was mm
            self.spn_target.setValue(float(SC.mm_to_counts(self.axis, old)))

    def _speed_in_cps(self) -> int:
        v    = self.spn_speed.value()
        unit = self.cbo_speed_unit.currentText()
        if unit == "mm/s":
            return SC.mm_per_sec_to_cps(self.axis, v)
        return int(round(v))

    def _target_in_counts(self) -> int:
        v    = self.spn_target.value()
        unit = self.cbo_target_unit.currentText()
        if unit == "mm":
            return SC.mm_to_counts(self.axis, v)
        return int(round(v))

    # ---- Enable state -------------------------------------------------------

    def set_enabled(self, on: bool):
        for w in (self.btn_jog_neg, self.btn_jog_pos, self.btn_stop,
                  self.btn_move, self.btn_zero, self.spn_target,
                  self.spn_speed, self.btn_enable_axis,
                  self.btn_disable_axis, self.btn_home):
            w.setEnabled(on)

    # ---- GUI -> Galil action handlers ----------------------------------------

    def _jog(self, direction: int):
        g = self.get_galil()
        if g is None or not g.connected:
            return
        try:
            speed = self._speed_in_cps() * (1 if direction > 0 else -1)
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
        target = self._target_in_counts()
        target_mm = SC.counts_to_mm(self.axis, target)
        try:
            self.log(f"> PA {self.axis}={target} ({target_mm:+.3f} mm) ; BG {self.axis}")
            g.move_absolute(self.axis, target)
        except GalilError as e:
            self.log(f"! {e}")
            if e.code == 22:
                QMessageBox.warning(self, "Soft-limit hit",
                                    f"{self.axis} target {target} cts ({target_mm:+.3f} mm) "
                                    f"is beyond the soft limit.")
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

    def _enable_axis(self):
        g = self.get_galil()
        if g is None or not g.connected:
            return
        try:
            self.log(f"> SH {self.axis}")
            g.enable(self.axis)
        except (GalilError, ConnectionError) as e:
            self.log(f"! {e}")

    def _disable_axis(self):
        g = self.get_galil()
        if g is None or not g.connected:
            return
        try:
            self.log(f"> MO {self.axis}")
            g.disable(self.axis)
        except (GalilError, ConnectionError) as e:
            self.log(f"! {e}")

    def _start_homing(self):
        g = self.get_galil()
        if g is None or not g.connected:
            return
        if self._homing_worker is not None and self._homing_worker.isRunning():
            self._homing_worker.cancel()
            self.btn_home.setText(f"Home {self.axis}")
            return

        self.log(f"# Starting auto-home for axis {self.axis}…")
        self.btn_home.setText("Cancel Home")
        self.lbl_status.setText("Homing…")
        self.lbl_status.setStyleSheet("color: #c07000; font-weight: bold;")

        self._homing_worker = HomingWorker(g, self.axis, self)
        self._homing_worker.progress.connect(self.log)
        self._homing_worker.done.connect(self._on_homing_done)
        self._homing_worker.start()

    def _on_homing_done(self, success: bool, msg: str):
        self.btn_home.setText(f"Home {self.axis}")
        self.log(f"{'✓' if success else '✗'} {msg}")
        if not success:
            QMessageBox.warning(self, "Homing failed", msg)

    # ---- State update slot --------------------------------------------------

    def update_state(self, axis_state: dict):
        pos = axis_state["pos"]
        self.lbl_counts.setText(f"{pos:,}")
        self.lbl_mm.setText(f"{SC.counts_to_mm(self.axis, pos):+.4f} mm")

        # Don't overwrite "Homing…" while worker is running
        if self._homing_worker is None or not self._homing_worker.isRunning():
            if axis_state["moving"]:
                self.lbl_status.setText("Moving")
                self.lbl_status.setStyleSheet("color: #c05000; font-weight: bold;")
            else:
                self.lbl_status.setText("Idle")
                self.lbl_status.setStyleSheet("color: #1a7a1a; font-weight: bold;")

        sw = axis_state["switches"]
        def fmt(b): return "●" if b else "○"
        self.lbl_switches.setText(
            f"F:{fmt(sw['forward_switch'])} "
            f"R:{fmt(sw['reverse_switch'])} "
            f"H:{fmt(sw['home_switch'])}"
        )

    def update_soft_limits(self, fl_counts: int, bl_counts: int):
        self.lbl_softlim.setText(f"BL:{bl_counts:,}   FL:{fl_counts:,}")


# ─── Top-level tab widget ─────────────────────────────────────────────────────

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
        from PySide6.QtWidgets import QLineEdit
        self.ip_edit = QLineEdit("192.168.1.10")
        self.ip_edit.setMaximumWidth(140)
        conn.addWidget(self.ip_edit)
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.clicked.connect(self._toggle_connection)
        conn.addWidget(self.btn_connect)
        self.lbl_conn_status = QLabel("● Disconnected")
        self.lbl_conn_status.setStyleSheet("color: #666666; font-weight: bold;")
        conn.addWidget(self.lbl_conn_status)
        self.lbl_model = QLabel("")
        self.lbl_model.setStyleSheet("color: #555; font-style: italic;")
        conn.addWidget(self.lbl_model, stretch=1)
        layout.addWidget(conn_box)

        # ── Emergency stop + enable/disable rows ────────────────────────────
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

        self.btn_enable_all  = QPushButton("Enable All (SH ABCD)")
        self.btn_disable_all = QPushButton("Disable All (MO)")
        self.btn_enable_all.setStyleSheet(
            "QPushButton { background:#1a7000; color:white; font-weight:bold; }"
            "QPushButton:hover { background:#228a00; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        self.btn_disable_all.setStyleSheet(
            "QPushButton { background:#8c5800; color:white; }"
            "QPushButton:hover { background:#a06600; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        self.btn_enable_all.clicked.connect(lambda: self._do(lambda g: g.enable("ABCD")))
        self.btn_disable_all.clicked.connect(lambda: self._do(lambda g: g.disable("ABCD")))
        estop_row.addWidget(self.btn_enable_all,  stretch=1)
        estop_row.addWidget(self.btn_disable_all, stretch=1)
        layout.addLayout(estop_row)

        # ── Single-axis enable row ───────────────────────────────────────────
        single_box = QGroupBox("Enable single axis (SH)")
        single_row = QHBoxLayout(single_box)
        single_row.addWidget(QLabel("Enable:"))
        self._btn_enable_single: dict[str, QPushButton] = {}
        for axis in SC.AXIS_LETTERS:
            btn = QPushButton(f"SH {axis}  ({SC.AXIS_NAMES[axis]})")
            btn.setEnabled(False)
            btn.setStyleSheet(
                "QPushButton { background:#004e8c; color:white; font-weight:bold; }"
                "QPushButton:hover { background:#0063b1; }"
                "QPushButton:disabled { background:#c0c0c0; color:#888; }"
            )
            btn.clicked.connect(lambda checked=False, a=axis: self._do(lambda g, _a=a: g.enable(_a)))
            single_row.addWidget(btn)
            self._btn_enable_single[axis] = btn
        layout.addWidget(single_box)

        # ── 4 axis panels in 2×2 grid ────────────────────────────────────────
        grid = QGridLayout()
        grid.setSpacing(6)
        self.axes: dict[str, AxisControls] = {}
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
        from PySide6.QtWidgets import QLineEdit
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
            self._log_line(f"# Connecting to {ip}:23 …")
            self.galil.connect(ip)
            self._log_line("# Connected.")
            try:
                model = self.galil.model_info()
                self.lbl_model.setText(model)
                self._log_line(f"> TH\n< {model}")
            except Exception as e:
                self._log_line(f"! TH failed: {e}")
            self._log_line("# Startup: CN, MT, YA, LC, AC/DC, SP, SH ABCD …")
            self.galil.startup_sequence(
                axes="ABCD",
                speed=SC.DEFAULT_SPEED_COUNTS_PER_SEC,
                accel=SC.DEFAULT_ACCEL_COUNTS_PER_SEC2,
            )
            for axis, panel in self.axes.items():
                try:
                    lim = self.galil.get_soft_limits(axis)
                    panel.update_soft_limits(lim["forward_counts"], lim["back_counts"])
                except Exception as e:
                    self._log_line(f"! Soft limits for {axis}: {e}")
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
            "color: #1a7a1a; font-weight: bold;" if on
            else "color: #666666; font-weight: bold;"
        )
        self.btn_estop.setEnabled(on)
        self.btn_enable_all.setEnabled(on)
        self.btn_disable_all.setEnabled(on)
        self.btn_send.setEnabled(on)
        self.manual_cmd.setEnabled(on)
        for btn in self._btn_enable_single.values():
            btn.setEnabled(on)
        for panel in self.axes.values():
            panel.set_enabled(on)

    # ---- Action helpers -------------------------------------------------------

    def _do(self, fn):
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

    # ---- Owner-callable cleanup ----------------------------------------------

    def abort_and_close(self):
        """Called by MainWindow.closeEvent."""
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
