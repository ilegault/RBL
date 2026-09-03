"""
bpm_calibration_panel.py
Self-contained BPM fiducial calibration panel, extracted from profiler_tab.py.

WHY THIS EXISTS
---------------
``profiler_tab.py`` did four jobs in one file: scope connection, FWHM readout,
width-level table, and BPM ms→mm calibration mode.  The calibration mode had its
own state (active BPM name, saved entries, pending fiducial measurement) and its
own set of methods that were tightly grouped but embedded in the 1 900-line tab.

The calibration logic is backed by ``hardware/bpm_calibration.py``, which is
already pure and tested.  Extracting the UI into this panel:

1. Reduces ``profiler_tab.py`` by ~350 lines and removes eight private methods.
2. Makes the BPM state (active name, saved entries, mm/s value) inspectable in
   isolation — useful for integration tests that do not need a scope connection.
3. Keeps a single definition of the "apply and save" path, which is safety-
   adjacent: a duplicated "save calibration" path can produce a tab that shows
   one value while the worker receives another.

COUPLING CONTRACT
-----------------
The panel communicates outward via Qt signals only:

  scale_changed(mm_per_second: float, name: str)
      Emitted whenever the active mm/s scale changes (apply, clear, load on
      start, BPM combo changed).  Host connects this to update its own
      ``_mm_per_second`` and set its dirty flags.

  entering_cal_mode()
      Emitted when the operator presses "Calibrate from fiducials".  Host's
      slot should: save the current analysis spin-box values, disable those
      controls, and update any mode indicator.

  exiting_cal_mode()
      Emitted when the operator presses "Done — back to beam".  Host's slot
      should: restore the saved spin-box values, re-enable them, call
      ``_on_analysis_changed()``, and set ``_plot_dirty = True``.

  take_shot_requested()
      Emitted when the panel needs the host to trigger a scope acquisition.
      The host calls ``_on_take_shot()``; the panel does not care about the
      button state or the shot-pending watchdog.

  cal_state_ready(state)
      Emitted when a calibration ScopeState arrives and is ready for display
      on the wave axes.  Host connects this to ``_redraw_fiducials(state)``.
"""
import logging
import math
from datetime import datetime, timezone

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from rbl.config.scope_config import BPM_FIDUCIAL_SPACING_MM, BPM_NAMES_DEFAULT
from rbl.gui import theme
from rbl.gui.widgets.inputs import NoScrollComboBox, QuietDoubleSpinBox, unit_row
from rbl.hardware.bpm_calibration import (
    CAL_ACTIVE_KEY,
    CAL_STORE_KEY,
    calibration_entry,
    load_calibrations,
)

log = logging.getLogger(__name__)


class BpmCalibrationPanel(QGroupBox):
    """BPM fiducial calibration UI — state, persistence, and scale management.

    Create with ``BpmCalibrationPanel(beamline=beamline)`` and connect its
    signals to the host tab.  ``beamline`` may be ``None`` for test/offline
    construction; update it later via ``set_beamline()``.
    """

    scale_changed      = Signal(float, str)   # (mm_per_second, bpm_name)
    entering_cal_mode  = Signal()
    exiting_cal_mode   = Signal()
    take_shot_requested = Signal()
    cal_state_ready    = Signal(object)       # ScopeState

    def __init__(self, beamline=None, parent=None):
        super().__init__("BPM Scope Calibration  (ms → mm)", parent)
        self._beamline    = beamline
        self._cal_mode    = False
        self._last_cal    = None   # last fiducial ScopeState
        self._cal_entries = {}
        self._cal_active  = ""
        self._mm_per_second = math.nan  # currently active scale (panel's own copy)

        outer = QVBoxLayout(self)
        row   = QHBoxLayout()

        row.addWidget(QLabel("BPM:"))
        self._cb_bpm = NoScrollComboBox()
        self._cb_bpm.setEditable(True)
        self._cb_bpm.setMinimumWidth(110)
        self._cb_bpm.setToolTip(
            "Which BPM the controller's selector is on. Each BPM sweeps at "
            "its own speed, so each needs its own calibration — a scale "
            "measured on one BPM is wrong on another.\n\n"
            "Type a name to add one.")
        # Deliberately NOT currentTextChanged: on an editable combo that fires
        # on every keystroke, typing 'BPM 2' swings the active scale through
        # 'B', 'BP', 'BPM'… each an uncalibrated name. Choice is committed
        # when an item is selected or editing finished.
        self._cb_bpm.activated.connect(
            lambda *_: self._on_bpm_changed(self._cb_bpm.currentText()))
        self._cb_bpm.lineEdit().editingFinished.connect(
            lambda: self._on_bpm_changed(self._cb_bpm.currentText()))
        row.addWidget(self._cb_bpm)

        row.addSpacing(12)
        row.addWidget(QLabel("Fiducial spacing:"))
        self._sp_spacing = QuietDoubleSpinBox()
        self._sp_spacing.setRange(1.0, 1000.0)
        self._sp_spacing.setDecimals(1)
        self._sp_spacing.setSingleStep(1.0)
        self._sp_spacing.setValue(BPM_FIDUCIAL_SPACING_MM)
        self._sp_spacing.setMaximumWidth(80)
        self._sp_spacing.setToolTip(
            "Real-space distance between the X and Y calibration peaks.\n\n"
            "60 mm (6 cm) on an NEC BPM80, which is every BPM on this beam "
            "line — from the head's manual, not something measured. Change "
            "it only for a different model of head.")
        self._sp_spacing.valueChanged.connect(self._on_spacing_changed)
        row.addLayout(unit_row(self._sp_spacing, "mm"))

        row.addSpacing(12)
        self._btn_cal_mode = QPushButton("Calibrate from fiducials")
        self._btn_cal_mode.setCheckable(True)
        self._btn_cal_mode.setToolTip(
            "Set the BPM controller's output selector to FIDUCIAL MARKS "
            "first, then press this.\n\n"
            "The tab switches to looking for calibration peaks instead of a "
            "beam, takes a shot, and reports the mm/ms it implies. Your beam "
            "analysis settings are put back when you leave the mode.")
        self._btn_cal_mode.toggled.connect(self._on_cal_mode_toggled)
        row.addWidget(self._btn_cal_mode)

        row.addSpacing(12)
        row.addWidget(QLabel("Fiducials:"))
        self._cb_fiducials = NoScrollComboBox()
        self._cb_fiducials.addItem("auto (drop the tallest)", None)
        self._cb_fiducials.setMinimumWidth(150)
        self._cb_fiducials.setEnabled(False)
        self._cb_fiducials.setToolTip(
            "Which two peaks are the calibration marks. The trace also "
            "carries the TRIGGER peak, which the procedure warns not to "
            "confuse with them — normally it is the tallest, and that is "
            "what 'auto' drops.\n\n"
            "The plot marks whichever two were used. Override here if it "
            "picked wrong.")
        self._cb_fiducials.currentIndexChanged.connect(self._on_fiducial_pick)
        row.addWidget(self._cb_fiducials)

        row.addSpacing(12)
        self._btn_cal_apply = QPushButton("Save && apply")
        self._btn_cal_apply.setEnabled(False)
        self._btn_cal_apply.setToolTip(
            "Store this scale against the named BPM and start reporting "
            "every beam width in millimetres as well as milliseconds.")
        self._btn_cal_apply.clicked.connect(self._on_cal_apply)
        row.addWidget(self._btn_cal_apply)

        self._btn_cal_clear = QPushButton("Uncalibrate")
        self._btn_cal_clear.setToolTip(
            "Stop scaling to millimetres. Nothing saved is deleted; the tab "
            "goes back to reporting milliseconds only.")
        self._btn_cal_clear.clicked.connect(self._on_cal_clear)
        row.addWidget(self._btn_cal_clear)

        row.addStretch()
        outer.addLayout(row)

        # The measurement this mode produced, before it is saved.
        self._lbl_cal_result = QLabel("—")
        self._lbl_cal_result.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
        self._lbl_cal_result.setWordWrap(True)
        outer.addWidget(self._lbl_cal_result)

        # What is in force right now. Always visible: a scale applied
        # invisibly is how a reading in the wrong BPM's millimetres gets
        # written down as fact.
        self._lbl_cal_active = QLabel("")
        outer.addWidget(self._lbl_cal_active)

        self._reload_calibrations()

    # ---- Public interface -------------------------------------------------

    def set_beamline(self, beamline) -> None:
        """Update the beamline reference (called from profiler_tab on connect)."""
        self._beamline = beamline

    def on_scope_state(self, state) -> None:
        """Feed a calibration ScopeState into the panel.

        Called from profiler_tab.on_scope_state when ``_cal_mode`` is active.
        Separating the routing decision (which stays in the tab, where ``_cal_mode``
        also lives) from the processing (here) avoids duplicating the mode check.
        """
        self._on_calibration_state(state)

    @property
    def mm_per_second(self) -> float:
        """Currently active scale (NaN when uncalibrated)."""
        return self._mm_per_second

    @property
    def in_cal_mode(self) -> bool:
        return self._cal_mode

    # ---- Calibration loading / persistence --------------------------------

    def _reload_calibrations(self) -> None:
        """Read the saved calibrations and put the active one into force."""
        try:
            from rbl.config.persistence import load_config
            cfg = load_config()
        except Exception:
            cfg = {}
        self._cal_entries, self._cal_active = load_calibrations(cfg)

        names = list(BPM_NAMES_DEFAULT)
        for name in self._cal_entries:
            if name not in names:
                names.append(name)
        self._cb_bpm.blockSignals(True)
        self._cb_bpm.clear()
        self._cb_bpm.addItems(names)
        if self._cal_active:
            idx = self._cb_bpm.findText(self._cal_active)
            if idx >= 0:
                self._cb_bpm.setCurrentIndex(idx)
        self._cb_bpm.blockSignals(False)

        entry = self._cal_entries.get(self._cal_active) or {}
        self._apply_scale(entry.get("mm_per_second", math.nan), self._cal_active)

    def _apply_scale(self, mm_per_second, name: str) -> None:
        """Activate a scale: update internal state, push to beamline, emit signal."""
        try:
            value = float(mm_per_second)
        except (TypeError, ValueError):
            value = math.nan
        if not (value == value) or value <= 0:
            value = math.nan
        self._mm_per_second = value
        if self._beamline is not None:
            try:
                self._beamline.set_scope_mm_scale(value, name or "")
            except Exception:
                log.exception("BpmCalibrationPanel: set_scope_mm_scale failed")
        self._lbl_cal_active.setText(self._fmt_active())
        self._lbl_cal_active.setStyleSheet(
            theme.status_label(theme.OK if value == value else theme.NEUTRAL,
                               bold=False)
            + f"font-size: {theme.FS_CAPTION}px;")
        self.scale_changed.emit(value, name or "")

    def _fmt_active(self) -> str:
        if not (self._mm_per_second == self._mm_per_second):
            return ("Not calibrated — widths are in milliseconds only. Set "
                    "the BPM controller to fiducial marks and press "
                    "Calibrate.")
        entry   = self._cal_entries.get(self._cal_active) or {}
        mm_per_ms = self._mm_per_second * 1e-3
        sep     = entry.get("separation_seconds", math.nan)
        saved   = str(entry.get("saved_iso", ""))[:19].replace("T", " ")
        bits    = [f"Active: {self._cal_active or '(unsaved)'} — "
                   f"{mm_per_ms:.4g} mm/ms  ({1.0 / mm_per_ms:.4g} ms/mm)"]
        try:
            if float(sep) == float(sep):
                bits.append(f"{float(sep) * 1e3:.4g} ms between marks")
        except (TypeError, ValueError):
            pass
        if entry.get("spacing_mm"):
            bits.append(f"{entry['spacing_mm']:g} mm apart")
        if saved:
            bits.append(f"saved {saved} UTC")
        if entry and not entry.get("confident", True):
            bits.append("PEAK CHOICE UNCONFIRMED")
        return "   ·   ".join(bits)

    # ---- BPM / spacing controls -------------------------------------------

    def _on_bpm_changed(self, name: str) -> None:
        """Switching BPM activates its saved scale (or none if unknown)."""
        name  = name.strip()
        entry = self._cal_entries.get(name)
        self._cal_active = name if entry else ""
        self._apply_scale((entry or {}).get("mm_per_second", math.nan), name)

    def _on_spacing_changed(self, *_) -> None:
        if self._cal_mode and self._beamline is not None:
            self._beamline.set_scope_calibration_mode(
                True, spacing_mm=self._sp_spacing.value(),
                override=self._current_override())

    def _current_override(self):
        return self._cb_fiducials.currentData()

    # ---- Calibration mode -------------------------------------------------

    def _on_cal_mode_toggled(self, on: bool) -> None:
        """Enter or leave fiducial mode.

        The analysis controls (peaks, smooth, envelope) are in the HOST tab,
        not this panel.  The host must connect ``entering_cal_mode`` to
        save+disable them, and ``exiting_cal_mode`` to restore+re-enable
        them and call ``_on_analysis_changed()``.
        """
        self._cal_mode = bool(on)
        if on:
            self._btn_cal_mode.setText("Done — back to beam")
            self._cb_fiducials.setEnabled(True)
            self.entering_cal_mode.emit()
            if self._beamline is not None:
                self._beamline.set_scope_calibration_mode(
                    True, spacing_mm=self._sp_spacing.value(),
                    override=self._current_override())
            self._lbl_cal_result.setText(
                "Set the BPM controller's output selector to FIDUCIAL MARKS, "
                "then press Take shot (F5).")
            self._lbl_cal_result.setStyleSheet(
                f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
            self.take_shot_requested.emit()
        else:
            self._btn_cal_mode.setText("Calibrate from fiducials")
            self._cb_fiducials.setEnabled(False)
            if self._beamline is not None:
                self._beamline.set_scope_calibration_mode(False)
            self.exiting_cal_mode.emit()

    def _on_fiducial_pick(self, *_) -> None:
        """Re-measure with the operator's choice of peaks."""
        if not self._cal_mode:
            return
        if self._beamline is not None:
            self._beamline.set_scope_calibration_mode(
                True, spacing_mm=self._sp_spacing.value(),
                override=self._current_override())
        self.take_shot_requested.emit()

    def _sync_fiducial_choices(self, state) -> None:
        """Offer every pair of peaks the last fiducial trace actually had."""
        n      = int(state.n_peaks or 0)
        wanted = [("auto (drop the tallest)", None)]
        for i in range(n):
            for j in range(i + 1, n):
                wanted.append((f"peaks {i + 1} & {j + 1}", (i, j)))
        have = [(self._cb_fiducials.itemText(k), self._cb_fiducials.itemData(k))
                for k in range(self._cb_fiducials.count())]
        if have == wanted:
            return
        keep = self._cb_fiducials.currentData()
        self._cb_fiducials.blockSignals(True)
        self._cb_fiducials.clear()
        for label, data in wanted:
            self._cb_fiducials.addItem(label, data)
        idx = self._cb_fiducials.findData(keep)
        self._cb_fiducials.setCurrentIndex(idx if idx >= 0 else 0)
        self._cb_fiducials.blockSignals(False)

    # ---- Calibration state ------------------------------------------------

    def _on_calibration_state(self, state) -> None:
        """A fiducial snapshot arrived: show what it implies, do not apply yet."""
        self._last_cal = state
        self._sync_fiducial_choices(state)
        self.cal_state_ready.emit(state)

        mmps = state.cal_mm_per_second
        if not (mmps == mmps) or mmps <= 0:
            self._btn_cal_apply.setEnabled(False)
            msg = state.error or state.cal_note or "no calibration from this trace"
            self._lbl_cal_result.setText(f"No scale from this trace — {msg}")
            self._lbl_cal_result.setStyleSheet(
                theme.status_label(theme.WARN, bold=False)
                + f"font-size: {theme.FS_CAPTION}px;")
            return

        mm_per_ms = mmps * 1e-3
        text = (f"{state.cal_spacing_mm:g} mm across "
                f"{state.cal_separation_seconds * 1e3:.4f} ms  →  "
                f"{mm_per_ms:.4g} mm/ms   ({1.0 / mm_per_ms:.4g} ms/mm, "
                f"{mmps:.4g} mm/s)")
        if state.cal_confident:
            text  += f"   ·   {state.cal_note}"
            colour = theme.OK
        else:
            text  += f"   ·   CHECK THE TRACE: {state.cal_note}"
            colour = theme.WARN
        self._lbl_cal_result.setText(text)
        self._lbl_cal_result.setStyleSheet(
            theme.status_label(colour, bold=False)
            + f"font-size: {theme.FS_CAPTION}px;")
        self._btn_cal_apply.setEnabled(True)
        self._btn_cal_apply.setText(
            "Save && apply" if state.cal_confident else "Save anyway && apply")

    def _on_cal_apply(self) -> None:
        """Store the last fiducial measurement against the named BPM."""
        state = self._last_cal
        if state is None or not (state.cal_mm_per_second == state.cal_mm_per_second):
            return
        name  = self._cb_bpm.currentText().strip() or "BPM"
        entry = calibration_entry(
            name,
            {
                "mm_per_second":      state.cal_mm_per_second,
                "mm_per_ms":          state.cal_mm_per_second * 1e-3,
                "separation_seconds": state.cal_separation_seconds,
                "spacing_mm":         state.cal_spacing_mm,
                "fiducial_indices":   [p["index"] for p in state.cal_peaks
                                       if p.get("role") == "fiducial"],
                "trigger_index":      next((p["index"] for p in state.cal_peaks
                                            if p.get("role") == "trigger"), None),
                "confident":          state.cal_confident,
                "source":             state.cal_source,
                "note":               state.cal_note,
            },
            channel   = state.channel,
            xincr     = state.xincr,
            saved_iso = datetime.now(timezone.utc).isoformat(),
        )
        self._cal_entries[name] = entry
        self._cal_active        = name
        try:
            from rbl.config.persistence import load_config, save_config
            cfg = load_config()
            cfg[CAL_STORE_KEY]  = self._cal_entries
            cfg[CAL_ACTIVE_KEY] = name
            save_config(cfg)
            log.info("BpmCalibrationPanel: saved %s: %.6g mm/s",
                     name, entry["mm_per_second"])
        except Exception:
            log.exception("BpmCalibrationPanel: failed to save calibration")

        if self._cb_bpm.findText(name) < 0:
            self._cb_bpm.blockSignals(True)
            self._cb_bpm.addItem(name)
            self._cb_bpm.setCurrentText(name)
            self._cb_bpm.blockSignals(False)
        self._apply_scale(entry["mm_per_second"], name)
        self._btn_cal_mode.setChecked(False)   # back to beam mode

    def _on_cal_clear(self) -> None:
        """Stop scaling. Deletes nothing — the saved entries stay put."""
        self._cal_active = ""
        try:
            from rbl.config.persistence import load_config, save_config
            cfg = load_config()
            cfg[CAL_ACTIVE_KEY] = ""
            save_config(cfg)
        except Exception:
            log.exception("BpmCalibrationPanel: failed to clear active calibration")
        self._apply_scale(math.nan, "")
