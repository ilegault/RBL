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
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QPushButton, QDoubleSpinBox, QTextEdit, QFormLayout, QComboBox,
    QMessageBox, QLineEdit,
)

from rbl.hardware.galil_driver import GalilController, GalilError
from rbl.config import hardware_config as SC


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
                        "enabled":  not self.galil.is_motor_off(axis),
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
    """Multi-pass homing for accuracy: coarse → medium → fine speed, always all passes.

    Each pass backs off a small amount then re-homes at a slower speed.
    define_zero is only called after the final (slowest) pass.
    """
    progress = Signal(str)
    done     = Signal(bool, str)   # success, message

    # Speeds and matching back-off distances for each successive pass (coarse → fine)
    _SPEEDS   = [225, 112, 58]
    _BACKOFFS = [1000, 500, 250]   # counts to back off before each pass

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
        n    = len(self._SPEEDS)
        try:
            # If already on home switch, back off using the coarse distance first
            sw = g.get_switch_states(axis)
            if sw["home_switch"]:
                self.progress.emit(f"{axis}: on home switch — backing off {self._BACKOFFS[0]} counts…")
                g.move_relative(axis, self._BACKOFFS[0])
                if not self._wait_idle(timeout=15.0):
                    self.done.emit(False, "Timeout while backing off home switch")
                    return

            for pass_num, (speed, backoff) in enumerate(zip(self._SPEEDS, self._BACKOFFS)):
                if self._cancelled:
                    self.done.emit(False, "Homing cancelled by user")
                    return

                # Back off before every pass using this pass's distance
                if pass_num > 0:
                    self.progress.emit(
                        f"{axis}: pass {pass_num+1}/{n} — backing off {backoff} counts…"
                    )
                    g.move_relative(axis, backoff)
                    if not self._wait_idle(timeout=15.0):
                        self.done.emit(False, f"Timeout on back-off before pass {pass_num+1}")
                        return

                self.progress.emit(
                    f"{axis}: pass {pass_num+1}/{n} — "
                    f"HM at {speed} cps "
                    f"({SC.cps_to_mm_per_sec(axis, speed):.2f} mm/s)…"
                )
                g.begin_home(axis, speed)
                time.sleep(0.5)   # let motion start

                if not self._wait_idle(timeout=60.0):
                    self.progress.emit(f"{axis}: HM timeout on pass {pass_num+1}")
                    g.stop(axis)
                    time.sleep(0.3)
                    # Restore speed and report failure — don't continue further passes
                    try:
                        g.set_speed(axis, SC.DEFAULT_SPEED_COUNTS_PER_SEC)
                    except Exception:
                        pass
                    self.done.emit(False, f"{axis}: homing timed out on pass {pass_num+1}/{n}")
                    return

                self.progress.emit(f"{axis}: pass {pass_num+1}/{n} complete")

            # All passes done — define zero on the final fine-speed position
            g.define_zero(axis)
            g.set_speed(axis, SC.DEFAULT_SPEED_COUNTS_PER_SEC)
            self.done.emit(True, f"{axis}: homed ({n} passes), DP=0, SP restored")

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

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(2)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # --- Live readouts (always visible) ------------------------------
        status_form = QFormLayout()
        status_form.setSpacing(2)
        self.lbl_pos    = QLabel("— cts  /  — mm")
        self.lbl_status = QLabel("Disconnected")
        self.lbl_status.setStyleSheet("color: #888;")
        self.lbl_info   = QLabel("F:— R:— H:—  |  BL:— FL:—")
        status_form.addRow("Position:", self.lbl_pos)
        status_form.addRow("Status:",   self.lbl_status)
        status_form.addRow("Sw/Lim:",   self.lbl_info)
        main_layout.addLayout(status_form)

        # --- Full-mode-only controls (hidden in simple mode) -------------
        self._full_widget = QWidget()
        full_form = QFormLayout(self._full_widget)
        full_form.setSpacing(2)
        full_form.setContentsMargins(0, 0, 0, 0)

        # Jog row
        jog_row = QHBoxLayout()
        self.btn_jog_neg = QPushButton("Jog −")
        self.btn_jog_pos = QPushButton("Jog +")
        self.btn_jog_neg.setMinimumHeight(30)
        self.btn_jog_pos.setMinimumHeight(30)
        self.btn_jog_neg.pressed.connect(lambda: self._jog(-1))
        self.btn_jog_neg.released.connect(self._stop)
        self.btn_jog_pos.pressed.connect(lambda: self._jog(+1))
        self.btn_jog_pos.released.connect(self._stop)
        jog_row.addWidget(self.btn_jog_neg)
        jog_row.addWidget(self.btn_jog_pos)
        full_form.addRow("Jog:", jog_row)

        # Jog speed with unit toggle
        speed_row = QHBoxLayout()
        self.spn_speed = QDoubleSpinBox()
        self.spn_speed.setDecimals(2)
        self.spn_speed.setMaximumWidth(100)
        self.cbo_speed_unit = QComboBox()
        self.cbo_speed_unit.addItems(["cps", "mm/s"])
        self.cbo_speed_unit.currentIndexChanged.connect(self._on_speed_unit_changed)
        speed_row.addWidget(self.spn_speed, stretch=1)
        speed_row.addWidget(self.cbo_speed_unit)
        full_form.addRow("Speed:", speed_row)
        self._set_speed_unit_range("cps")
        self.spn_speed.setValue(SC.DEFAULT_JOG_SPEED)

        # Stop / Zero row
        ctrl_row1 = QHBoxLayout()
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setMinimumHeight(30)
        self.btn_stop.clicked.connect(self._stop)
        self.btn_zero = QPushButton("Zero here")
        self.btn_zero.setMinimumHeight(30)
        self.btn_zero.clicked.connect(self._define_zero)
        ctrl_row1.addWidget(self.btn_stop)
        ctrl_row1.addWidget(self.btn_zero)
        full_form.addRow(ctrl_row1)

        # Enable / Disable per-axis row
        ctrl_row2 = QHBoxLayout()
        self.btn_enable_axis  = QPushButton(f"Enable {axis_letter}")
        self.btn_disable_axis = QPushButton(f"Disable {axis_letter}")
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
        self.btn_enable_axis.clicked.connect(self._enable_axis)
        self.btn_disable_axis.clicked.connect(self._disable_axis)
        ctrl_row2.addWidget(self.btn_enable_axis)
        ctrl_row2.addWidget(self.btn_disable_axis)
        full_form.addRow(ctrl_row2)

        main_layout.addWidget(self._full_widget)

        # --- Target move (always visible) --------------------------------
        target_form = QFormLayout()
        target_form.setSpacing(2)
        move_row = QHBoxLayout()
        self.spn_target = QDoubleSpinBox()
        self.spn_target.setDecimals(3)
        self.spn_target.setMaximumWidth(100)
        self.cbo_target_unit = QComboBox()
        self.cbo_target_unit.addItems(["counts", "mm"])
        self.cbo_target_unit.setCurrentIndex(1)          # default to mm
        self.cbo_target_unit.currentIndexChanged.connect(self._on_target_unit_changed)
        self.btn_move = QPushButton("Move to")
        self.btn_move.setMinimumHeight(30)
        self.btn_move.clicked.connect(self._move_absolute)
        move_row.addWidget(self.spn_target, stretch=1)
        move_row.addWidget(self.cbo_target_unit)
        move_row.addWidget(self.btn_move)
        target_form.addRow("Target:", move_row)
        main_layout.addLayout(target_form)
        self._set_target_unit_range("mm")

        # --- Home button (always visible) --------------------------------
        self.btn_home = QPushButton(f"Home {axis_letter}")
        self.btn_home.setStyleSheet(
            "QPushButton { background:#004e8c; color:white; font-weight:bold; }"
            "QPushButton:hover { background:#0063b1; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        self.btn_home.clicked.connect(self._start_homing)
        main_layout.addWidget(self.btn_home)

        self.set_enabled(False)

    # ---- Unit helpers -------------------------------------------------------

    def _set_speed_unit_range(self, unit: str):
        if unit == "cps":
            self.spn_speed.setRange(1.0, 100_000.0)
            self.spn_speed.setSingleStep(100.0)
        else:  # mm/s
            max_mms = SC.cps_to_mm_per_sec(self.axis, 100_000)
            self.spn_speed.setRange(0.01, max_mms)
            self.spn_speed.setSingleStep(0.1)

    def _set_target_unit_range(self, unit: str):
        if unit == "counts":
            self.spn_target.setRange(-10_000_000.0, 10_000_000.0)
            self.spn_target.setSingleStep(100.0)
        else:  # mm
            max_mm = SC.counts_to_mm(self.axis, 10_000_000)
            self.spn_target.setRange(-max_mm, max_mm)
            self.spn_target.setSingleStep(0.1)

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

    def set_simple_mode(self, simple: bool):
        """Toggle between full and simple (compact) mode."""
        self._full_widget.setVisible(not simple)

        if simple:
            label_font  = QFont(); label_font.setPointSize(13)
            status_font = QFont(); status_font.setPointSize(13); status_font.setBold(True)
            btn_font    = QFont(); btn_font.setPointSize(13);    btn_font.setBold(True)
            spn_font    = QFont(); spn_font.setPointSize(13)
            btn_h       = 52
            spn_h       = 44
        else:
            label_font  = QFont()
            status_font = QFont()
            btn_font    = QFont()
            spn_font    = QFont()
            btn_h       = 0
            spn_h       = 0

        for lbl in (self.lbl_pos, self.lbl_info):
            lbl.setFont(label_font)
        self.lbl_status.setFont(status_font)

        for btn in (self.btn_move, self.btn_home):
            btn.setFont(btn_font)
            btn.setMinimumHeight(btn_h)

        self.spn_target.setFont(spn_font)
        self.cbo_target_unit.setFont(spn_font)
        self.spn_target.setMinimumHeight(spn_h)
        self.cbo_target_unit.setMinimumHeight(spn_h)


    def _jog(self, direction: int):
        g = self.get_galil()
        if g is None or not g.connected:
            return
        try:
            speed = self._speed_in_cps() * (1 if direction > 0 else -1)
            prefix = "," * "ABCD".index(self.axis)
            self.log(f"> JG {prefix}{speed} ; BG {self.axis}")
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
        self.lbl_pos.setText(f"{pos:,} cts  /  {SC.counts_to_mm(self.axis, pos):+.4f} mm")

        # Don't overwrite "Homing…" while worker is running
        if self._homing_worker is None or not self._homing_worker.isRunning():
            enabled = axis_state.get("enabled", True)
            sw      = axis_state["switches"]
            if axis_state["moving"]:
                self.lbl_status.setText("Moving  [enabled]")
                self.lbl_status.setStyleSheet("color: #c05000; font-weight: bold;")
            elif not enabled:
                self.lbl_status.setText("Disabled")
                self.lbl_status.setStyleSheet("color: #888888; font-weight: bold;")
            elif sw["forward_switch"]:
                self.lbl_status.setText("FWD LIMIT active")
                self.lbl_status.setStyleSheet("color: #cc0000; font-weight: bold;")
            elif sw["reverse_switch"] or sw["home_switch"]:
                self.lbl_status.setText("REV/HOME LIMIT active")
                self.lbl_status.setStyleSheet("color: #cc6600; font-weight: bold;")
            else:
                self.lbl_status.setText("Idle  [enabled]")
                self.lbl_status.setStyleSheet("color: #1a7a1a; font-weight: bold;")

        sw = axis_state["switches"]
        def fmt(b): return "●" if b else "○"
        sw_text = (f"F:{fmt(sw['forward_switch'])} "
                   f"R:{fmt(sw['reverse_switch'])} "
                   f"H:{fmt(sw['home_switch'])}")
        current = self.lbl_info.text()
        lim_part = current.split("|", 1)[1].strip() if "|" in current else "BL:— FL:—"
        self.lbl_info.setText(f"{sw_text}  |  {lim_part}")

    def update_soft_limits(self, fl_counts: int, bl_counts: int):
        current = self.lbl_info.text()
        sw_part = current.split("|", 1)[0].strip() if "|" in current else "F:— R:— H:—"
        self.lbl_info.setText(f"{sw_part}  |  BL:{bl_counts:,}   FL:{fl_counts:,}")


# ─── History-aware command line edit ─────────────────────────────────────────

class HistoryLineEdit(QLineEdit):
    """QLineEdit with Up/Down arrow key command history."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._history: list[str] = []
        self._history_idx: int = -1   # -1 = not browsing history
        self._current_draft: str = ""

    def add_to_history(self, cmd: str):
        if cmd and (not self._history or self._history[-1] != cmd):
            self._history.append(cmd)
        self._history_idx = -1
        self._current_draft = ""

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Up:
            if not self._history:
                return
            if self._history_idx == -1:
                self._current_draft = self.text()
                self._history_idx = len(self._history) - 1
            elif self._history_idx > 0:
                self._history_idx -= 1
            self.setText(self._history[self._history_idx])
            self.end(False)
        elif event.key() == Qt.Key.Key_Down:
            if self._history_idx == -1:
                return
            if self._history_idx < len(self._history) - 1:
                self._history_idx += 1
                self.setText(self._history[self._history_idx])
            else:
                self._history_idx = -1
                self.setText(self._current_draft)
            self.end(False)
        else:
            if self._history_idx != -1:
                # any other key resets browsing
                self._history_idx = -1
            super().keyPressEvent(event)


# ─── Top-level tab widget ─────────────────────────────────────────────────────

class MotorTab(QWidget):
    """The "Stepper Motors" outer tab."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.galil  = GalilController()
        self.worker = None

        outer_layout = QHBoxLayout(self)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        outer_layout.setSpacing(8)
        left_layout = QVBoxLayout()
        left_layout.setSpacing(8)

        # ── Connection bar ──────────────────────────────────────────────────
        conn_box = QGroupBox("Galil DMC-4103 Connection")
        conn = QHBoxLayout(conn_box)
        conn.addWidget(QLabel("IP:"))
        self.ip_edit = QLineEdit("192.168.42.1")
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
        left_layout.addWidget(conn_box)

        # ── Slit jaw offset notice ──────────────────────────────────────────
        offset_box = QGroupBox("Slit Position Reference — Absolute Distance from Beam Centre")
        offset_outer = QVBoxLayout(offset_box)
        offset_outer.setSpacing(4)

        notice_lbl = QLabel(
            "All position inputs and readouts for each slit jaw are in absolute distance "
            "from the beam centre (mm). When a jaw is at the homed / zeroed position "
            "(counts = 0), it physically sits 0.2 mm from centre — giving a 0.4 mm total "
            "gap between opposing jaws. The software automatically accounts for this 0.2 mm "
            "hardware offset in every conversion.\n\n"
            "➡  Type the true absolute distance you want, NOT a pre-corrected number. "
            "The 0.2 mm offset is applied for you inside the conversion. "
            "Example: to place a jaw 1.5 mm from beam centre, enter 1.5 mm in the Target "
            "box (not 1.3 mm). The software subtracts the 0.2 mm offset internally, moves "
            "the jaw so it ends up exactly 1.5 mm from centre, and the Position readout "
            "then shows 1.5 mm. (Under the hood the extra travel past the homed spot is "
            "1.5 − 0.2 = 1.3 mm, but you never type that corrected value yourself.)\n\n"
            "⚠  The 0.2 mm offset is only valid after each axis has been properly zeroed "
            "(homed). Always zero all axes before operating the slits. Failure to do so "
            "will cause all absolute position values to be incorrect."
        )
        notice_lbl.setWordWrap(True)
        notice_lbl.setStyleSheet(
            "color: #333; font-size: 9pt; padding: 2px;"
        )
        offset_outer.addWidget(notice_lbl)
        left_layout.addWidget(offset_box)

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

        self.btn_simple_mode = QPushButton("Simple Mode")
        self.btn_simple_mode.setCheckable(True)
        self.btn_simple_mode.setMinimumHeight(44)
        self.btn_simple_mode.setStyleSheet(
            "QPushButton { background:#2b2b6e; color:white; font-weight:bold; }"
            "QPushButton:hover { background:#38388a; }"
            "QPushButton:checked { background:#5a5aaa; color:white; font-weight:bold; }"
        )
        self.btn_simple_mode.toggled.connect(self._toggle_simple_mode)
        estop_row.addWidget(self.btn_simple_mode, stretch=1)
        left_layout.addLayout(estop_row)

        # ── 4 axis panels in 2×2 grid ────────────────────────────────────────
        grid = QGridLayout()
        grid.setSpacing(6)
        self.axes: dict[str, AxisControls] = {}
        for i, axis in enumerate(SC.AXIS_LETTERS):
            panel = AxisControls(axis, lambda: self.galil, self._log_line, self)
            self.axes[axis] = panel
            grid.addWidget(panel, i // 2, i % 2)
        left_layout.addLayout(grid, stretch=1)

        # ── Console (right panel — full height) ────────────────────────────
        cons_box = QGroupBox("Command Console")
        cons = QVBoxLayout(cons_box)
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setFont(QFont("Menlo", 9))
        cons.addWidget(self.console, stretch=1)
        manual_row = QHBoxLayout()
        self.manual_cmd = HistoryLineEdit()
        self.manual_cmd.setPlaceholderText("Manual DMC command (e.g. MG _RPA, TH, LS)")
        self.manual_cmd.returnPressed.connect(self._send_manual)
        self.btn_send = QPushButton("Send")
        self.btn_send.clicked.connect(self._send_manual)
        manual_row.addWidget(self.manual_cmd, stretch=1)
        manual_row.addWidget(self.btn_send)
        cons.addLayout(manual_row)

        # ── Assemble outer layout (left 2/3 controls, right 1/3 console) ──
        outer_layout.addLayout(left_layout, stretch=2)
        outer_layout.addWidget(cons_box, stretch=1)

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
                # TH returns multi-line; collapse to one horizontal line for the label
                model_oneline = "  ·  ".join(
                    l.strip() for l in model.splitlines() if l.strip()
                )
                self.lbl_model.setText(model_oneline)
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

    def _toggle_simple_mode(self, simple: bool):
        for panel in self.axes.values():
            panel.set_simple_mode(simple)
        self.btn_simple_mode.setText("Full Mode" if simple else "Simple Mode")

    def _emergency_stop(self):
        if not self.galil.connected:
            return
        self._log_line("> AB  (EMERGENCY STOP)")
        self.galil.abort()

    def _send_manual(self):
        cmd = self.manual_cmd.text().strip().upper()
        if not cmd or not self.galil.connected:
            return
        self.manual_cmd.add_to_history(cmd)
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
