"""
motor_tab.py
PySide6 widget for the "Stepper Motors" outer tab.

Features:
  - Per-axis jog/move/stop/zero controls.
  - Jog speed and target position in cps OR mm/s / mm (unit toggle).
  - Per-axis Enable (SH) / Disable (MO) buttons; also single-axis and all-axis.
  - Automated homing with progressive speed retry (225 → 112 → 58 cps), per
    axis and for the whole set.
  - Big red EMERGENCY STOP always visible.

HOMING, AND THE EIGHT CLICKS IT USED TO TAKE
--------------------------------------------
HM is a search at the speed it is given, and the last of the three passes runs
at 58 cps precisely because a slow final approach is what makes the zero
repeatable. From the far end of the travel that pass would take minutes and
time out — so the working procedure was always to jog the axis down onto its
limit by hand FIRST, then press Home. Two gestures per axis, eight before the
slits were usable, at the start of every session.

Three buttons now cover that, all of them running the same routine
(rbl/hardware/galil_workers.AxisHomeRoutine — seek the limit, then the three
HM passes, then DP=0):

  - "Seek + Home {axis}" on each panel: that axis's two gestures, one click.
  - "Auto-Home All — One at a Time": all four, sequentially, on one thread.
    One axis in motion and one command on the wire at any moment; a failure
    stops the run with the untried axes untouched. This is the one to press.
  - "Auto-Home All — Together": the same four runs started at once, on four
    threads sharing the socket. Deliberately the second button — it is the
    experiment, offered because the only way to learn whether this controller
    tolerates it is to try it.

Any homing run locks its axis's jog/move/zero controls (a second command on a
homing axis is the thing to prevent) but never its Stop, its Home buttons —
which read Cancel while running — or the EMERGENCY STOP, which additionally
cancels every run in progress, because an abort that the next pass undoes a
second later is not a stop.
"""
import time

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QPushButton, QFormLayout,
    QMessageBox, QLineEdit,
)

from rbl.hardware.galil_driver import GalilController, GalilError
from rbl.hardware.galil_workers import (
    AutoHomeAllWorker, GalilPollWorker, HomingWorker,
)
from rbl.config import hardware_config as SC
from rbl.gui import theme
from rbl.gui.widgets.command_console import HistoryLineEdit, LogPane
from rbl.gui.widgets.inputs import NoScrollComboBox, QuietDoubleSpinBox


# ─── Per-axis control groupbox ────────────────────────────────────────────────

class AxisControls(QGroupBox):
    """One self-contained panel for one slit."""

    # This axis finished a homing run started FROM THIS PANEL. MotorTab listens
    # so the "all together" button knows when its four parallel runs are done
    # — that button starts the panels' own workers rather than a fifth one, so
    # each axis keeps its own Cancel and its own status line.
    homing_finished = Signal(str, bool, str)   # axis letter, success, message

    def __init__(self, axis_letter: str, get_galil_fn, log_fn, beamline,
                 parent=None):
        super().__init__(f"{SC.AXIS_NAMES[axis_letter]}  (axis {axis_letter})", parent)
        self.axis      = axis_letter
        self.slit      = SC.AXIS_NAMES[axis_letter]
        self.get_galil = get_galil_fn
        self.log       = log_fn
        # Motion is logged and published through Beamline rather than straight
        # to this tab's console, so a move made here also reaches the Overview
        # — and one made there also reaches this panel. See Beamline.move_slit.
        self.beamline  = beamline
        self._homing_worker: HomingWorker | None = None
        # A status line held by something outside this panel — the all-axes
        # auto-home sequencer, which drives this axis without owning one of
        # this panel's workers. While it is set, the 5 Hz poll must not paint
        # over it with "Idle", which is what update_state would otherwise do
        # between two of the sequencer's commands.
        self._external_status: str | None = None
        # Set while the tab's simultaneous button owns this panel's run, so a
        # failure is reported once by the tab rather than as four modal boxes.
        self._quiet_homing = False
        # Has this axis had its zero established since the app started?  The
        # controller does not remember, and every mm figure downstream is wrong
        # without it, so consumers get told rather than left to assume.
        self.zeroed = False

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
        self.spn_speed = QuietDoubleSpinBox()
        self.spn_speed.setDecimals(2)
        self.spn_speed.setMaximumWidth(100)
        self.cbo_speed_unit = NoScrollComboBox()
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
        self.spn_target = QuietDoubleSpinBox()
        self.spn_target.setDecimals(3)
        self.spn_target.setMaximumWidth(100)
        self.cbo_target_unit = NoScrollComboBox()
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

        # --- Home buttons (always visible) -------------------------------
        #
        # Two, because they are two different starting assumptions, not two
        # levels of a setting. "Home" is HM from wherever the axis already is,
        # which is right when it is already near the switch. "Seek + Home"
        # jogs it down onto the limit first, which is the pair of gestures the
        # procedure has always required from cold — and doing it in one click
        # is the whole reason this panel grew a second button.
        home_row = QHBoxLayout()
        home_row.setSpacing(4)

        self.btn_home = QPushButton(f"Home {axis_letter}")
        self.btn_home.setStyleSheet(
            "QPushButton { background:#004e8c; color:white; font-weight:bold; }"
            "QPushButton:hover { background:#0063b1; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        self.btn_home.setToolTip(
            f"HM from wherever axis {axis_letter} is now: three passes "
            "(225 / 112 / 58 cps), then DP=0.\n"
            "Use this when the axis is already near its home switch — from "
            "the far end of the travel the 58 cps pass will time out."
        )
        self.btn_home.clicked.connect(lambda: self._start_homing(seek_first=False))
        home_row.addWidget(self.btn_home)

        self.btn_seek_home = QPushButton(f"Seek + Home {axis_letter}")
        self.btn_seek_home.setStyleSheet(
            "QPushButton { background:#00607a; color:white; font-weight:bold; }"
            "QPushButton:hover { background:#00758f; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        self.btn_seek_home.setToolTip(
            f"The two-step procedure in one click: jog {axis_letter} negative "
            f"at {SC.HOME_SEEK_SPEED_COUNTS_PER_SEC} cps until the home limit "
            "trips, then the same three-pass HM and DP=0 as Home.\n"
            "This is what to press from cold. One command at a time, and the "
            "button becomes Cancel while it runs."
        )
        self.btn_seek_home.clicked.connect(lambda: self._start_homing(seek_first=True))
        home_row.addWidget(self.btn_seek_home)

        main_layout.addLayout(home_row)

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

    def set_target_mm(self, mm: float):
        """Show a target commanded from anywhere — here, or the Overview tab.

        Converted into whatever unit this panel is currently displaying, so an
        Overview move made in mm still lands correctly in a box switched to
        counts. Written through sync_value() so it defers rather than
        overwriting a target somebody is part-way through typing.
        """
        if self.cbo_target_unit.currentText() == "counts":
            value = float(SC.mm_to_counts(self.axis, mm))
        else:
            value = float(mm)
        if abs(self.spn_target.value() - value) < 1e-9:
            return
        self.spn_target.sync_value(value)

    # ---- Enable state -------------------------------------------------------

    def set_enabled(self, on: bool):
        for w in (self.btn_jog_neg, self.btn_jog_pos, self.btn_stop,
                  self.btn_move, self.btn_zero, self.spn_target,
                  self.spn_speed, self.btn_enable_axis,
                  self.btn_disable_axis, self.btn_home, self.btn_seek_home):
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

        for btn in (self.btn_move, self.btn_home, self.btn_seek_home):
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
        # The driver call stays here — this panel owns the counts/mm unit
        # toggle and the soft-limit dialog below, neither of which the
        # Overview has. Only the log line and the commanded target go through
        # Beamline, which is what puts them on both screens.
        self.beamline.log_motor(
            f"> PA {self.axis}={target} ({target_mm:+.3f} mm) ; "
            f"BG {self.axis}   [{self.slit}]")
        self.beamline.note_slit_target(self.slit, target_mm)
        try:
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
            self.zeroed = True
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

    def homing_active(self) -> bool:
        """Is this axis inside a homing run — its own, or the tab's sequencer?"""
        if self._external_status is not None:
            return True
        return self._homing_worker is not None and self._homing_worker.isRunning()

    def cancel_homing(self):
        """Stop this panel's own homing run, if one is going.

        Cooperative: the routine checks between commands, so this stops the
        NEXT command rather than the motion already underway. That is what
        makes an EMERGENCY STOP stick instead of being undone by the next pass.
        """
        if self._homing_worker is not None and self._homing_worker.isRunning():
            self._homing_worker.cancel()

    def set_motion_controls_enabled(self, on: bool):
        """Everything that could put a SECOND command on this axis.

        Deliberately not the home buttons: while a run is going they read
        Cancel, and locking the operator out of their own way to stop it is
        the opposite of safe. Stop stays live for the same reason.
        """
        for w in (self.btn_jog_neg, self.btn_jog_pos, self.btn_move,
                  self.btn_zero, self.spn_target, self.spn_speed,
                  self.btn_enable_axis, self.btn_disable_axis):
            w.setEnabled(on)

    def _start_homing(self, seek_first: bool = False, quiet: bool = False):
        """Start (or cancel) this axis's homing run.

        `quiet` is set when the all-axes simultaneous button started this run:
        the failure dialog then belongs to the tab, which can report all four
        outcomes at once instead of stacking up to four modal boxes.
        """
        g = self.get_galil()
        if g is None or not g.connected:
            return
        if self._homing_worker is not None and self._homing_worker.isRunning():
            self._homing_worker.cancel()
            self._reset_home_buttons()
            return
        if self._external_status is not None:
            return   # the tab's sequencer owns this axis right now

        what = "seek + home" if seek_first else "home"
        self.log(f"# Starting auto-{what} for axis {self.axis}…")
        self._quiet_homing = quiet
        (self.btn_seek_home if seek_first else self.btn_home).setText("Cancel")
        self.lbl_status.setText("Seeking limit…" if seek_first else "Homing…")
        self.lbl_status.setStyleSheet("color: #c07000; font-weight: bold;")

        # A jog or a Move on this axis mid-run would collide with the routine's
        # own motion. The home buttons stay live — they are the Cancel.
        self.set_motion_controls_enabled(False)

        self._homing_worker = HomingWorker(g, self.axis, self,
                                           seek_first=seek_first)
        self._homing_worker.progress.connect(self.log)
        self._homing_worker.done.connect(self._on_homing_done)
        self._homing_worker.start()

    # Public entry point for the tab-level "all together" button. Named rather
    # than reaching into _start_homing so the tab is not calling a private.
    def start_auto_home(self, seek_first: bool = True, quiet: bool = True):
        self._start_homing(seek_first=seek_first, quiet=quiet)

    def _reset_home_buttons(self):
        self.btn_home.setText(f"Home {self.axis}")
        self.btn_seek_home.setText(f"Seek + Home {self.axis}")

    def _on_homing_done(self, success: bool, msg: str):
        self._reset_home_buttons()
        g = self.get_galil()
        # Only hand the controls back if there is still something to command:
        # the link may have dropped underneath the run, which is one of the
        # ways it ends.
        self.set_motion_controls_enabled(bool(g is not None and g.connected))
        if success:
            # Homing ends on DP=0, so the axis is now referenced.
            self.zeroed = True
        self.log(f"{'✓' if success else '✗'} {msg}")
        self.homing_finished.emit(self.axis, success, msg)
        if not success and not getattr(self, "_quiet_homing", False):
            QMessageBox.warning(self, "Homing failed", msg)

    # ---- Status held from outside -------------------------------------------

    def set_external_status(self, text: str | None, role: str = None):
        """Hold this panel's status line while the tab's sequencer drives it.

        `None` releases it, and the next poll repaints the real state. Without
        this the 5 Hz poll would overwrite "Queued for auto-home" with "Idle"
        200 ms later, and an axis waiting its turn would look identical to one
        nobody intends to touch.
        """
        self._external_status = text
        if text is None:
            return
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet(
            theme.status_label(role) if role else "color: #c07000; font-weight: bold;")

    # ---- State update slot --------------------------------------------------

    def update_state(self, axis_state: dict):
        pos = axis_state["pos"]
        self.lbl_pos.setText(f"{pos:,} cts  /  {SC.counts_to_mm(self.axis, pos):+.4f} mm")

        # Don't overwrite "Homing…" while a homing run owns this axis — its
        # own worker, or the tab's all-axes sequencer.
        if not self.homing_active():
            enabled = axis_state.get("enabled", True)
            sw      = axis_state["switches"]
            if axis_state["moving"]:
                self.lbl_status.setText("Moving  [enabled]")
                self.lbl_status.setStyleSheet("color: #c05000; font-weight: bold;")
            elif not enabled:
                self.lbl_status.setText("Disabled")
                self.lbl_status.setStyleSheet(theme.status_label(theme.MUTED))
            elif sw["forward_switch"]:
                self.lbl_status.setText("FWD LIMIT active")
                self.lbl_status.setStyleSheet(theme.status_label(theme.FAULT))
            elif sw["reverse_switch"] or sw["home_switch"]:
                self.lbl_status.setText("REV/HOME LIMIT active")
                self.lbl_status.setStyleSheet("color: #cc6600; font-weight: bold;")
            else:
                self.lbl_status.setText("Idle  [enabled]")
                self.lbl_status.setStyleSheet(theme.status_label(theme.OK))

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


# ─── Top-level tab widget ─────────────────────────────────────────────────────

class MotorTab(QWidget):
    """The "Stepper Motors" outer tab."""

    # Slit geometry for anything that needs to know where the slits are.  The
    # beam-position indicator on the log-amp tab reconstructs the beam from
    # slit positions plus currents, so it needs this at poll rate.
    #   {"connected": bool,
    #    "zeroed":    bool,                      # all four axes referenced
    #    "positions": {"X+": mm, ...}}           # UNSIGNED distance from centre
    slit_state = Signal(dict)

    # Raw per-axis poll snapshot (axis letter -> {pos, moving, switches,
    # enabled}, exactly GalilPollWorker.state's shape) plus the all-axes-zeroed
    # flag. Feeds Beamline.ingest_motor_poll, which has richer per-axis fields
    # (pos_counts, moving, enabled, switches) than slit_state carries.
    raw_state_changed = Signal(dict, bool)

    # Emitted when the Galil link drops. Feeds Beamline.motors_disconnected.
    motors_disconnected = Signal()

    @property
    def galil(self) -> GalilController:
        return self.beamline.galil

    def __init__(self, beamline, parent=None):
        super().__init__(parent)
        # Beamline owns the GalilController; this tab reaches it through a
        # delegating property (self.galil) so the many existing call sites
        # below don't need touching. MotorTab does not construct or own it.
        self.beamline = beamline
        self.worker = None

        # The all-axes sequencer, while one is running (see _start_auto_home).
        self._auto_worker: AutoHomeAllWorker | None = None
        # Axes still running under the simultaneous button. Tracked as a set
        # rather than a counter so a late duplicate `done` cannot end the run
        # early, and so the log line can name what is still moving.
        self._parallel_axes: set[str] = set()
        self._parallel_failures: list[str] = []

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
        self.lbl_conn_status.setStyleSheet(theme.pill(False))
        conn.addWidget(self.lbl_conn_status)
        self.lbl_model = QLabel("")
        self.lbl_model.setStyleSheet(f"color: {theme.NEUTRAL}; font-style: italic;")
        conn.addWidget(self.lbl_model, stretch=1)
        left_layout.addWidget(conn_box)

        # ── Slit offset notice ──────────────────────────────────────────
        offset_box = QGroupBox("Slit Position Reference — Absolute Distance from Beam Centre")
        offset_outer = QVBoxLayout(offset_box)
        offset_outer.setSpacing(4)

        notice_lbl = QLabel(
            "All position inputs and readouts for each slit are in absolute distance "
            "from the beam centre (mm). When a slit is at the homed / zeroed position "
            "(counts = 0), it physically sits 0.2 mm from centre — giving a 0.4 mm total "
            "gap between opposing slits. The software automatically accounts for this 0.2 mm "
            "hardware offset in every conversion.\n\n"
            "➡  Type the true absolute distance you want, NOT a pre-corrected number. "
            "The 0.2 mm offset is applied for you inside the conversion. "
            "Example: to place a slit 1.5 mm from beam centre, enter 1.5 mm in the Target "
            "box (not 1.3 mm). The software subtracts the 0.2 mm offset internally, moves "
            "the slit so it ends up exactly 1.5 mm from centre, and the Position readout "
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
            f" font-weight:bold; border:2px solid {theme.FAULT}; }}"
            f"QPushButton:hover {{ background:{theme.FAULT}; }}"
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

        # ── Automatic homing (all four axes) ────────────────────────────────
        left_layout.addWidget(self._build_auto_home_box())

        # ── 4 axis panels in 2×2 grid ────────────────────────────────────────
        grid = QGridLayout()
        grid.setSpacing(6)
        self.axes: dict[str, AxisControls] = {}
        for i, axis in enumerate(SC.AXIS_LETTERS):
            panel = AxisControls(axis, lambda: self.galil, self._log_line,
                                 beamline, self)
            panel.homing_finished.connect(self._on_panel_homing_finished)
            self.axes[axis] = panel
            grid.addWidget(panel, i // 2, i % 2)
        left_layout.addLayout(grid, stretch=1)

        # ── Console (right panel — full height) ────────────────────────────
        cons_box = QGroupBox("Command Console")
        cons = QVBoxLayout(cons_box)
        self.console = LogPane()
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

        # ── Cross-screen motion (see Beamline.move_slit) ──────────────────
        #
        # A move commanded on the Overview tab reaches the Galil through
        # Beamline, not through this tab, so without these two connections it
        # was invisible here: nothing in the console, and a Target box still
        # showing whatever was in it before. Both screens now render every
        # move, whichever one issued it.
        beamline.motor_logged.connect(self._log_line)
        beamline.slit_target_changed.connect(self._on_slit_target_changed)

        self._set_buttons_connected(False)

    # ---- Automatic homing, all four axes -------------------------------------
    #
    # Bringing the slits up used to be eight deliberate gestures — jog each
    # axis onto its limit, then press its Home button — before any other
    # calibration could start. Both buttons here do that whole set from one
    # click; they differ only in whether the axes take turns.

    _AUTO_HOME_STANDING = (
        "Homes all four slits from cold: jog each axis onto its home limit, "
        "then the three-pass HM and DP=0. Sequential is the one to use — start "
        "it and work on another tab."
    )

    def _build_auto_home_box(self) -> QGroupBox:
        box = QGroupBox("Automatic Homing — All Four Slits")
        lay = QVBoxLayout(box)
        lay.setSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(6)

        self.btn_auto_home_seq = QPushButton("Auto-Home All — One at a Time")
        self.btn_auto_home_seq.setMinimumHeight(40)
        self.btn_auto_home_seq.setStyleSheet(
            "QPushButton { background:#004e8c; color:white; font-weight:bold;"
            " font-size:14px; }"
            "QPushButton:hover { background:#0063b1; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        self.btn_auto_home_seq.setToolTip(
            "A, B, C, D in order. Each axis is jogged onto its home limit, "
            "homed in three passes and zeroed before the next one is touched, "
            "so only one axis is ever moving and only one command is ever on "
            "the wire.\n\n"
            "If an axis fails, the sequence stops there and the remaining axes "
            "are left untouched — you get one clear failure instead of four "
            "half-homed slits.\n\n"
            "Safe to leave running while you work on another tab. Press again "
            "to cancel."
        )
        self.btn_auto_home_seq.clicked.connect(self._start_auto_home)
        row.addWidget(self.btn_auto_home_seq, stretch=2)

        self.btn_auto_home_par = QPushButton("Auto-Home All — Together")
        self.btn_auto_home_par.setMinimumHeight(40)
        self.btn_auto_home_par.setStyleSheet(
            "QPushButton { background:#7a4a00; color:white; font-weight:bold;"
            " font-size:14px; }"
            "QPushButton:hover { background:#8f5800; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        self.btn_auto_home_par.setToolTip(
            "The same routine on all four axes AT ONCE — four moving slits and "
            "four threads interleaving commands on one socket.\n\n"
            "Faster if the controller keeps up, and this is the way to find "
            "out whether it does. Untested against this hardware, which is why "
            "it is the second button and not the first: if anything looks "
            "wrong, EMERGENCY STOP and use the sequential one.\n\n"
            "Press again to cancel all four."
        )
        self.btn_auto_home_par.clicked.connect(self._start_auto_home_parallel)
        row.addWidget(self.btn_auto_home_par, stretch=1)
        lay.addLayout(row)

        self.lbl_auto_home = QLabel(self._AUTO_HOME_STANDING)
        self.lbl_auto_home.setWordWrap(True)
        self.lbl_auto_home.setStyleSheet(
            theme.status_label(theme.NEUTRAL, bold=False) + "font-size: 12px;")
        lay.addWidget(self.lbl_auto_home)
        return box

    def _set_auto_home_note(self, text: str, role: str = theme.NEUTRAL):
        self.lbl_auto_home.setText(text)
        self.lbl_auto_home.setStyleSheet(
            theme.status_label(role, bold=False) + "font-size: 12px;")

    def _confirm_auto_home(self, title: str, body: str) -> bool:
        """One overridable confirmation for both buttons.

        Homing drives every slit into its limit switch, so it is not something
        to start by brushing a button — but it is also the routine an operator
        runs at the top of every session, so it gets one Yes/No, not a form.
        Its own method so tests can drive both answers without a live dialog.
        """
        return QMessageBox.question(
            self, title, body,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        ) == QMessageBox.StandardButton.Yes

    def _start_auto_home(self):
        """Sequential: one axis, start to finish, then the next."""
        if self._auto_worker is not None and self._auto_worker.isRunning():
            self._auto_worker.cancel()
            self._set_auto_home_note("Cancelling after the current step…",
                                     theme.WARN)
            return
        if not self.galil.connected or self._parallel_axes:
            return
        if not self._confirm_auto_home(
                "Auto-home all four slits",
                "Each axis will be jogged onto its home limit, homed in three "
                "passes and zeroed — A, then B, then C, then D.\n\n"
                "Only one axis moves at a time. Make sure the slits are clear "
                "to travel.\n\nStart?"):
            return

        self._lock_axes_for_auto_home(True, "Queued for auto-home")
        self.btn_auto_home_seq.setText("Cancel Auto-Home")
        self.btn_auto_home_par.setEnabled(False)
        self._set_auto_home_note("Auto-homing all four axes, one at a time…",
                                 theme.WARN)
        self._log_line("# Auto-home ALL — sequential, one command at a time.")

        self._auto_worker = AutoHomeAllWorker(self.galil, SC.AXIS_LETTERS, self)
        self._auto_worker.progress.connect(self._log_line)
        self._auto_worker.axis_started.connect(self._on_auto_axis_started)
        self._auto_worker.axis_finished.connect(self._on_auto_axis_finished)
        self._auto_worker.done.connect(self._on_auto_home_done)
        self._auto_worker.start()

    def _on_auto_axis_started(self, axis: str):
        panel = self.axes.get(axis)
        if panel is not None:
            panel.set_external_status("Auto-homing…")
        self._set_auto_home_note(
            f"Auto-homing axis {axis} [{SC.AXIS_NAMES.get(axis, axis)}]…",
            theme.WARN)

    def _on_auto_axis_finished(self, axis: str, ok: bool, msg: str):
        panel = self.axes.get(axis)
        if panel is None:
            return
        if ok:
            # The routine ends on DP=0, so this axis is now referenced — the
            # same flag the panel's own Home button sets, and what the rest of
            # the app reads as "mm figures mean something on this axis".
            panel.zeroed = True
        panel.set_external_status("Homed ✓" if ok else "Home FAILED",
                                  theme.OK if ok else theme.FAULT)

    def _on_auto_home_done(self, ok: bool, summary: str):
        self._lock_axes_for_auto_home(False)
        self.btn_auto_home_seq.setText("Auto-Home All — One at a Time")
        self.btn_auto_home_par.setEnabled(self.galil.connected)
        self._log_line(f"{'✓' if ok else '✗'} {summary}")
        self._set_auto_home_note(summary, theme.OK if ok else theme.FAULT)
        if not ok:
            QMessageBox.warning(self, "Auto-home did not complete", summary)

    def _lock_axes_for_auto_home(self, locked: bool, status: str = None):
        """Take the per-axis controls out of reach while the sequencer runs.

        A jog or a Move issued mid-sequence would put a second command on an
        axis the sequencer believes it has to itself. EMERGENCY STOP stays
        live — it is the one control that must never be locked out — and so
        does the Cancel on the button that started this.
        """
        for panel in self.axes.values():
            panel.set_enabled(not locked and self.galil.connected)
            panel.set_external_status(status if locked else None)

    def _start_auto_home_parallel(self):
        """Simultaneous: all four axes at once, each on its own worker."""
        if self._parallel_axes:
            self._log_line("# Cancelling all four homing runs…")
            for axis in list(self._parallel_axes):
                self.axes[axis].cancel_homing()
            self._set_auto_home_note("Cancelling all four…", theme.WARN)
            return
        if not self.galil.connected or self._auto_worker_running():
            return
        if not self._confirm_auto_home(
                "Auto-home all four slits TOGETHER",
                "All four axes will jog onto their home limits and home AT "
                "THE SAME TIME, with four threads sharing one connection to "
                "the controller.\n\n"
                "This is the experimental path — it has not been proven "
                "against this controller. If anything looks wrong, hit "
                "EMERGENCY STOP and use the sequential button instead.\n\n"
                "Start all four?"):
            return

        self.btn_auto_home_par.setText("Cancel All Homing")
        self.btn_auto_home_seq.setEnabled(False)
        self._parallel_failures = []
        self._parallel_axes = set(SC.AXIS_LETTERS)
        self._set_auto_home_note("Homing all four axes simultaneously…",
                                 theme.WARN)
        self._log_line("# Auto-home ALL — simultaneous. Four axes, one socket.")
        for axis in SC.AXIS_LETTERS:
            self.axes[axis].start_auto_home(seek_first=True, quiet=True)

    def _auto_worker_running(self) -> bool:
        return self._auto_worker is not None and self._auto_worker.isRunning()

    def _on_panel_homing_finished(self, axis: str, ok: bool, msg: str):
        """One axis of a simultaneous run reported in.

        Panels also emit this for a run the operator started on that panel
        alone; the guard is what keeps those out of the parallel tally.
        """
        if axis not in self._parallel_axes:
            return
        self._parallel_axes.discard(axis)
        if not ok:
            self._parallel_failures.append(axis)
        if self._parallel_axes:
            self._set_auto_home_note(
                "Homing simultaneously — still running: "
                + ", ".join(sorted(self._parallel_axes)), theme.WARN)
            return


        self.btn_auto_home_par.setText("Auto-Home All — Together")
        self.btn_auto_home_seq.setEnabled(self.galil.connected)
        if self._parallel_failures:
            summary = ("Simultaneous auto-home finished with failures on: "
                       + ", ".join(sorted(self._parallel_failures)))
            self._set_auto_home_note(summary, theme.FAULT)
            self._log_line(f"✗ {summary}")
            QMessageBox.warning(self, "Auto-home did not complete", summary)
        else:
            summary = "Simultaneous auto-home complete — all four axes homed."
            self._set_auto_home_note(summary, theme.OK)
            self._log_line(f"✓ {summary}")

    def _on_slit_target_changed(self, slit: str, mm: float):
        """Some screen commanded *slit* to *mm* — show it on that axis panel."""
        axis_letter = self.beamline.axis_letter_for(slit)
        panel = self.axes.get(axis_letter)
        if panel is not None:
            panel.set_target_mm(mm)

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
        # A homing run outlives the socket it was talking to: dropping the link
        # under one leaves it raising ConnectionError on every poll until it
        # gives up. Tell it to stop before taking the socket away.
        self._cancel_all_homing()
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(2000)
            self.worker = None
        self.galil.disconnect()
        self._set_buttons_connected(False)
        self.lbl_model.setText("")
        self._log_line("# Disconnected.")
        # Positions go stale the moment the link drops; say so rather than
        # letting consumers keep drawing the last known geometry as if live.
        self.slit_state.emit({"connected": False, "zeroed": False, "positions": {}})
        self.motors_disconnected.emit()

    def _set_buttons_connected(self, on: bool):
        self.btn_connect.setText("Disconnect" if on else "Connect")
        self.lbl_conn_status.setText("● Connected" if on else "● Disconnected")
        self.lbl_conn_status.setStyleSheet(theme.pill(on))
        self.btn_estop.setEnabled(on)
        self.btn_enable_all.setEnabled(on)
        self.btn_disable_all.setEnabled(on)
        self.btn_send.setEnabled(on)
        self.manual_cmd.setEnabled(on)
        self.btn_auto_home_seq.setEnabled(on)
        self.btn_auto_home_par.setEnabled(on)
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
        # Cancel FIRST, and unconditionally. An abort with a homing run still
        # going is not a stop — the worker's next pass would begin the motion
        # again a second later, and the operator would be watching a slit they
        # just E-stopped start moving on its own.
        self._cancel_all_homing()
        if not self.galil.connected:
            return
        self._log_line("> AB  (EMERGENCY STOP)")
        self.galil.abort()

    def _cancel_all_homing(self):
        """Tell every homing run in progress to stop — sequencer and panels.

        Cancellation is cooperative: each routine checks between commands, so
        this does not itself stop motion. It is what stops the NEXT command,
        and it is why the abort above is worth anything.
        """
        if self._auto_worker is not None and self._auto_worker.isRunning():
            self._auto_worker.cancel()
        for panel in self.axes.values():
            panel.cancel_homing()

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

        zeroed = all(p.zeroed for p in self.axes.values())
        positions = {SC.AXIS_NAMES[axis]: SC.counts_to_mm(axis, st["pos"])
                     for axis, st in snapshot.items()}
        self.slit_state.emit({
            "connected": True,
            "zeroed":    zeroed,
            "positions": positions,
        })
        self.raw_state_changed.emit(snapshot, zeroed)

    def _on_poll_error(self, msg: str):
        self._log_line(f"! Poll thread error: {msg}")
        self._do_disconnect()

    def _log_line(self, line: str):
        self.console.log(line)

    # ---- Owner-callable cleanup ----------------------------------------------

    def abort_and_close(self):
        """Called by MainWindow.closeEvent."""
        try:
            if self.galil.connected:
                self.galil.abort()
        except Exception:
            pass
        self._do_disconnect()          # cancels every homing run on the way
        self._wait_for_homing_threads()

    def _wait_for_homing_threads(self, timeout_ms: int = 3000):
        """Let the cancelled homing runs unwind before the widgets go away.

        A QThread still running when its object is destroyed takes the process
        with it. The cancel has already gone in — this is the join, and it is
        bounded so a wedged socket read cannot hang the close.
        """
        threads = [self._auto_worker] + [p._homing_worker for p in self.axes.values()]
        for thread in threads:
            if thread is not None and thread.isRunning():
                thread.wait(timeout_ms)
