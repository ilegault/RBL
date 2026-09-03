"""
calibration_tab.py
PySide6 widget for the "HV Calibration" outer tab.

WHAT THIS FEATURE IS AND IS NOT
--------------------------------
This sweeps each deflection channel through a bipolar DC ladder and records
what the EEL5000 VOLTAGE/CURRENT monitors report back via the LabJack T7,
against what was commanded. The voltage monitor is itself a link in the
chain being measured (command -> Rigol output -> amplifier gain -> monitor
divider -> T7 ADC), so this data CANNOT tell "the amplifier under-produces"
apart from "the monitor under-reads" — both look identical in the CSV.

This tab therefore only ever DISPLAYS gain/offset/residual. There is no
"Apply correction" control anywhere on it, and none should ever be added —
see rbl/config/calibration_config.py's module docstring and docs/calibration.md.

Follows amp_tab.py's "stage the selection, commit on Apply" idiom for the
one place it applies here (the load-condition + operator checklist gate
before Run), and its lj_panel embedding for the shared LabJackPanel.
"""
import logging
import time

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from rbl.config import hardware_config as SC
from rbl.config.calibration_config import (
    CAL_AC_FREQ_HZ,
    CAL_AC_FREQ_PRESETS,
    CAL_DRIFT_PROFILE,
    CAL_MAX_KV,
    CAL_PASSES,
    CAL_STEP_KV,
    CAL_SWEEP_PROFILE,
    CAL_UNCERTAINTY_V,
    CAL_ZERO_DWELL_S,
    DRIFT_DEFAULT_KV,
    DRIFT_MAX_ATTENDED_H,
    DRIFT_MAX_UNATTENDED_H,
    LoadCondition,
    ac_sweep_points,
    sweep_points,
)
from rbl.config.labjack_stream_config import is_pair_channel
from rbl.gui import theme
from rbl.gui.widgets.connection_bar import LabJackPanel
from rbl.gui.widgets.inputs import NoScrollComboBox, QuietDoubleSpinBox, unit_row
from rbl.services.calibration_runner import CalibrationRunner
from rbl.services.calibration_writer import CalibrationWriter

log = logging.getLogger(__name__)

# The EEL5000 manual's own words on load connections — put verbatim in the
# pre-run checklist so nobody swaps the load mid-session.
_MANUAL_LOAD_WARNING = (
    "The EEL5000 manual is explicit that load connections must be made "
    "with the unit off and unplugged."
)


# ─── Pre-run checklist dialog ─────────────────────────────────────────────────

class _PreRunChecklistDialog(QDialog):
    """Modal, explicit-acknowledgement checklist. The app cannot verify any
    of these in software, so it makes a human assert them instead."""

    def __init__(self, load_condition: LoadCondition, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Calibration pre-run checklist")
        self.setModal(True)
        layout = QVBoxLayout(self)

        intro = QLabel(
            f"About to start a calibration run with load condition: "
            f"<b>{load_condition.value}</b>."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._checks = []
        items = [
            "HIGH VOLTAGE ENABLE shorting cap is installed on all four amplifiers.",
            f"Load condition matches what was selected in the GUI "
            f"({load_condition.value}).",
        ]
        if load_condition is LoadCondition.ON_PLATES:
            items.append("Nobody is at the beamline and the area is clear.")
        for text in items:
            cb = QCheckBox(text)
            cb.setWordWrap(True) if hasattr(cb, "setWordWrap") else None
            cb.toggled.connect(self._refresh_ok_enabled)
            self._checks.append(cb)
            layout.addWidget(cb)

        if load_condition is LoadCondition.DISCONNECTED:
            warn = QLabel(_MANUAL_LOAD_WARNING)
            warn.setWordWrap(True)
            warn.setStyleSheet(
                f"color: {theme.WARN}; font-weight: bold; padding: 6px; "
                "background: #fff6e6; border: 1px solid #e0c080; border-radius: 3px;"
            )
            layout.addWidget(warn)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Start Run")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._refresh_ok_enabled()

    def _refresh_ok_enabled(self, *_):
        ok = all(cb.isChecked() for cb in self._checks)
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)


# ─── Top-level tab widget ─────────────────────────────────────────────────────

class CalibrationTab(QWidget):
    """The 'HV Calibration' outer tab: DC sweep, AC sweep and drift."""

    # Mirrors AmpTab signals — MainWindow connects these to beamline.
    profile_change_requested = Signal(str)
    pair_change_requested    = Signal(str)   # amp label for AMP_PAIR sweeps
    # (profile_name, amp_label) — acquire both in ONE stream restart. Used at
    # run start, where doing it as two separate requests was unreliable from
    # some prior stream states (see Beamline.set_stream_pair_profile).
    pair_profile_requested   = Signal(str, str)
    # No channel_change_requested: single-channel retargeting existed only for
    # the amp test matrix, which this tab no longer hosts.  Sweeps and drift
    # use multi-channel and pair profiles exclusively.

    # True while a run (sweep or drift) is active.
    run_state_changed = Signal(bool)
    # (amp_label, measured_ma, limit_ma) — re-emitted from the runner so
    # MainWindow can freeze the amp tab's plot at the moment of the trip.
    overcurrent_tripped = Signal(str, float, float)

    def __init__(self, beamline, parent=None):
        super().__init__(parent)
        self.beamline         = beamline
        self._runner:  CalibrationRunner = None
        self._writer:  CalibrationWriter = None
        self._current_profile = None      # tracked via on_profile_changed
        self._prior_profile   = None      # stashed at run start, restored after
        self._prior_pair      = None      # ditto, for pair profiles
        self._connected       = False
        self._dwell_start_t:  float = None
        self._dwell_total_s:  float = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        left_col = QVBoxLayout()
        left_col.setSpacing(8)

        # ── Connection panel (shared T7, same as AmpTab / CurrentTab) ─────
        self.lj_panel = LabJackPanel()
        left_col.addWidget(self.lj_panel)

        left_col.addWidget(self._build_run_config_box())

        unc_lbl = QLabel(
            f"Combined amp + monitor + DAQ uncertainty budget: "
            f"±{CAL_UNCERTAINTY_V:.0f} V. Deviations inside this band are "
            f"noise, not findings. This tool never applies a correction — "
            f"see docs/calibration.md."
        )
        unc_lbl.setWordWrap(True)
        unc_lbl.setStyleSheet(
            "color: #7a2000; font-size: 9pt; padding: 4px; "
            "background: #fff6e6; border: 1px solid #e0c080; border-radius: 3px;"
        )
        left_col.addWidget(unc_lbl)

        self._build_run_controls(left_col)
        left_col.addStretch()

        top_row.addLayout(left_col, stretch=1)
        layout.addLayout(top_row, stretch=1)

        self._on_mode_toggled()
        self._on_step_mode_changed()   # initialise dwell row visibility
        self._refresh_run_enabled()
        self.lj_panel.set_enabled(True)

    # ── Section builders ──────────────────────────────────────────────────────

    def _build_run_config_box(self) -> QGroupBox:
        """Run Configuration group: mode, channels, load, drift, AC controls."""
        cfg_box = QGroupBox("Run Configuration")
        cfg_form = QFormLayout(cfg_box)

        mode_row = QHBoxLayout()
        self.rb_sweep = QRadioButton("DC Sweep")
        self.rb_ac = QRadioButton("AC Sweep")
        self.rb_drift = QRadioButton("Drift")
        self.rb_sweep.setChecked(True)
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self.rb_sweep)
        self._mode_group.addButton(self.rb_ac)
        self._mode_group.addButton(self.rb_drift)
        self.rb_sweep.toggled.connect(self._on_mode_toggled)
        self.rb_ac.toggled.connect(self._on_mode_toggled)
        mode_row.addWidget(self.rb_sweep)
        mode_row.addWidget(self.rb_ac)
        mode_row.addWidget(self.rb_drift)
        mode_row.addStretch()
        cfg_form.addRow("Mode:", mode_row)

        # ── Channel selection (DC/AC sweep only) ───────────────────────────
        self._chan_widget = QWidget()
        _chan_lay = QHBoxLayout(self._chan_widget)
        _chan_lay.setContentsMargins(0, 0, 0, 0)
        _chan_lay.setSpacing(8)
        self._chan_all = QCheckBox("All")
        self._chan_all.setChecked(True)
        self._chan_all.setToolTip("Check to sweep all four channels; "
                                  "uncheck to pick individual channels.")
        _chan_lay.addWidget(self._chan_all)
        self._chan_checks: dict[str, QCheckBox] = {}
        for _amp in SC.AMP_LABELS:
            _cb = QCheckBox(_amp)
            _cb.setChecked(True)
            _cb.setEnabled(False)   # individual picks disabled while "All" is on
            _cb.toggled.connect(self._refresh_run_enabled)
            self._chan_checks[_amp] = _cb
            _chan_lay.addWidget(_cb)
        _chan_lay.addStretch()
        self._chan_all.toggled.connect(self._on_chan_all_toggled)
        self._chan_label = QLabel("Channels:")
        cfg_form.addRow(self._chan_label, self._chan_widget)

        self.cbo_load = NoScrollComboBox()
        self.cbo_load.addItem("— select load condition —", userData=None)
        for cond in LoadCondition:
            self.cbo_load.addItem(cond.value, userData=cond)
        self.cbo_load.currentIndexChanged.connect(self._refresh_run_enabled)
        cfg_form.addRow("Load condition:", self.cbo_load)

        self.le_note = QLineEdit()
        self.le_note.setPlaceholderText("Operator note (recorded in the CSV sidecar)")
        cfg_form.addRow("Note:", self.le_note)

        # ── Keep-awake ─────────────────────────────────────────────────────
        self.chk_keep_awake = QCheckBox("Keep PC awake")
        self.chk_keep_awake.setToolTip(
            "Calls SetThreadExecutionState(ES_DISPLAY_REQUIRED | "
            "ES_SYSTEM_REQUIRED) so Windows does not lock the screen or "
            "sleep during a long calibration run.  No cursor movement."
        )
        cfg_form.addRow("", self.chk_keep_awake)

        self.chk_keep_awake.toggled.connect(self._on_keep_awake_toggled)

        # Sweep info — display-only, derived from calibration_config.
        n_pts = len(sweep_points("up"))
        n_total = n_pts * len(CAL_PASSES) * len(SC.AMP_LABELS)
        self.lbl_sweep_info = QLabel(
            f"{n_pts} points/pass (±{CAL_MAX_KV:.1f} kV, {CAL_STEP_KV:.2f} kV step, "
            f"ramps from 0) × {len(CAL_PASSES)} passes × {len(SC.AMP_LABELS)} "
            f"channels = {n_total} setpoints"
        )
        self.lbl_sweep_info.setWordWrap(True)
        self.lbl_sweep_info.setStyleSheet(f"color: {theme.NEUTRAL}; font-size: 10px;")
        cfg_form.addRow("DC Sweep:", self.lbl_sweep_info)

        n_ac_pts = len(ac_sweep_points())
        self.lbl_ac_info = QLabel(
            f"{n_ac_pts} points (0 → {CAL_MAX_KV:.1f} kV peak → 0, "
            f"{CAL_STEP_KV:.2f} kV step) × {len(SC.AMP_LABELS)} channels"
        )
        self.lbl_ac_info.setWordWrap(True)
        self.lbl_ac_info.setStyleSheet(f"color: {theme.NEUTRAL}; font-size: 10px;")
        cfg_form.addRow("AC Sweep:", self.lbl_ac_info)

        # ── Step approach ─────────────────────────────────────────────────
        # How each rung is reached. Applies to both sweeps; drift holds one
        # setpoint and ignores it.
        self.cbo_step_mode = NoScrollComboBox()
        self.cbo_step_mode.addItem("Ascending — step rung to rung", userData=False)
        self.cbo_step_mode.addItem(
            f"Return to zero — 0 V for {CAL_ZERO_DWELL_S:.1f} s between rungs",
            userData=True)
        self.cbo_step_mode.setToolTip(
            "How each amplitude is approached.\n\n"
            "ASCENDING steps straight from one rung to the next, so the "
            "amplifier changes internal operating range while the load is "
            "already charged. That is the harsher event, and it is why trips "
            "cluster at particular amplitudes rather than at the top of the "
            "ladder.\n\n"
            "RETURN TO ZERO drops to 0 V and dwells before each rung, so every "
            "amplitude is reached from rest. Slower, but it measures the "
            "amplitude in the condition the application actually sees — a "
            "steady hold, never a step between amplitudes. A limit found by "
            "stepping may be a limit on stepping, not on the amplitude."
        )
        self.cbo_step_mode.currentIndexChanged.connect(self._on_step_mode_changed)
        cfg_form.addRow("Step approach:", self.cbo_step_mode)

        # Zero-dwell duration — only meaningful when return-to-zero is selected.
        self._dwell_widget = QWidget()
        _dwell_lay = QHBoxLayout(self._dwell_widget)
        _dwell_lay.setContentsMargins(0, 0, 0, 0)
        _dwell_lay.setSpacing(4)
        self.spn_zero_dwell = QuietDoubleSpinBox()
        self.spn_zero_dwell.setRange(0.1, 60.0)
        self.spn_zero_dwell.setValue(CAL_ZERO_DWELL_S)
        self.spn_zero_dwell.setDecimals(1)
        self.spn_zero_dwell.setSingleStep(0.5)
        self.spn_zero_dwell.setToolTip(
            "How long the output is held at 0 V before each rung in "
            "return-to-zero mode."
        )
        self.spn_zero_dwell.valueChanged.connect(self._on_step_mode_changed)
        _dwell_lay.addWidget(self.spn_zero_dwell)
        _dwell_lay.addWidget(QLabel("s"))
        _dwell_lay.addStretch()
        self._dwell_label = QLabel("Zero dwell:")
        cfg_form.addRow(self._dwell_label, self._dwell_widget)

        self.chk_dwell_output_off = QCheckBox(
            "Disable funcgen output during dwell (instead of commanding 0 V)"
        )
        self.chk_dwell_output_off.setToolTip(
            "When checked, the function generator output is turned OFF for the "
            "dwell period rather than being set to 0 V DC.\n\n"
            "0 V DC leaves the amplifier enabled and presenting a low-impedance "
            "path, which still draws quiescent current. Disabling the output "
            "removes the drive signal entirely, so the amplifier goes fully idle "
            "between rungs. The output is automatically re-enabled when the next "
            "rung is commanded."
        )
        self._dwell_output_off_label = QLabel("")
        cfg_form.addRow(self._dwell_output_off_label, self.chk_dwell_output_off)

        self.lbl_step_cost = QLabel("")
        self.lbl_step_cost.setWordWrap(True)
        self.lbl_step_cost.setStyleSheet(f"color: {theme.NEUTRAL}; font-size: 10px;")
        cfg_form.addRow("", self.lbl_step_cost)

        # AC sweep frequency selector.
        self._ac_freq_widget = QWidget()
        _af_lay = QHBoxLayout(self._ac_freq_widget)
        _af_lay.setContentsMargins(0, 0, 0, 0)
        _af_lay.setSpacing(6)
        self.spn_ac_freq = QuietDoubleSpinBox()
        self.spn_ac_freq.setRange(1.0, 10000.0)
        self.spn_ac_freq.setValue(CAL_AC_FREQ_HZ)
        self.spn_ac_freq.setDecimals(1)
        self.spn_ac_freq.setToolTip("Sine frequency for the AC sweep")
        _af_lay.addWidget(self.spn_ac_freq)
        _af_lay.addWidget(QLabel("Hz"))
        _af_preset = NoScrollComboBox()
        _af_preset.setToolTip("Quick-select a common frequency")
        for _f in CAL_AC_FREQ_PRESETS:
            _af_preset.addItem(f"{_f:.0f} Hz", userData=_f)
        # Set combo to default freq if it's in the preset list.
        _default_idx = next(
            (i for i, f in enumerate(CAL_AC_FREQ_PRESETS) if f == CAL_AC_FREQ_HZ), -1
        )
        if _default_idx >= 0:
            _af_preset.setCurrentIndex(_default_idx)
        _af_preset.currentIndexChanged.connect(
            lambda: self.spn_ac_freq.setValue(_af_preset.currentData())
        )
        _af_lay.addWidget(_af_preset)
        _af_lay.addStretch()
        self._ac_freq_label = QLabel("AC Frequency:")
        cfg_form.addRow(self._ac_freq_label, self._ac_freq_widget)

        # Drift controls.
        self.spn_drift_kv = QuietDoubleSpinBox()
        self.spn_drift_kv.setRange(-CAL_MAX_KV, CAL_MAX_KV)
        self.spn_drift_kv.setValue(DRIFT_DEFAULT_KV)
        self.spn_drift_kv.setDecimals(3)
        self._drift_setpoint_label = QLabel("Drift setpoint:")
        self._drift_setpoint_row = QWidget()
        self._drift_setpoint_row.setLayout(unit_row(self.spn_drift_kv, "kV"))
        cfg_form.addRow(self._drift_setpoint_label, self._drift_setpoint_row)

        self.spn_drift_h = QuietDoubleSpinBox()
        self.spn_drift_h.setRange(0.0, DRIFT_MAX_UNATTENDED_H)
        self.spn_drift_h.setValue(1.0)
        self.spn_drift_h.setDecimals(2)
        self.spn_drift_h.setToolTip(
            f"Above {DRIFT_MAX_ATTENDED_H:.1f} h is only permitted with the "
            f"amplifier DISCONNECTED from the steerer (unattended cap "
            f"{DRIFT_MAX_UNATTENDED_H:.1f} h)."
        )
        self._drift_duration_label = QLabel("Drift duration:")
        self._drift_duration_row = QWidget()
        self._drift_duration_row.setLayout(unit_row(self.spn_drift_h, "h"))
        cfg_form.addRow(self._drift_duration_label, self._drift_duration_row)

        # ── Drift AC mode ──────────────────────────────────────────────────
        self.chk_drift_ac = QCheckBox("AC sine (per-channel frequency below)")
        self.chk_drift_ac.setToolTip(
            "When checked, channels set to AC below are driven with a sine wave "
            "at the chosen frequency; the Drift setpoint becomes the peak amplitude. "
            "Channels left on DC are held at the DC setpoint."
        )
        self.chk_drift_ac.toggled.connect(self._on_drift_ac_toggled)
        self._drift_ac_label = QLabel("Drift waveform:")
        cfg_form.addRow(self._drift_ac_label, self.chk_drift_ac)

        # Waveform shape selector (shared by all drift AC channels).
        self._drift_ac_shape_widget = QWidget()
        _shape_lay = QHBoxLayout(self._drift_ac_shape_widget)
        _shape_lay.setContentsMargins(0, 0, 0, 0)
        _shape_lay.setSpacing(6)
        self.cbo_drift_ac_shape = NoScrollComboBox()
        for _shape in ("Triangle", "Sine", "Square"):
            self.cbo_drift_ac_shape.addItem(_shape, userData=_shape)
        self.cbo_drift_ac_shape.setCurrentIndex(0)   # Triangle default
        self.cbo_drift_ac_shape.setToolTip(
            "Waveform shape applied to all AC drift channels"
        )
        _shape_lay.addWidget(self.cbo_drift_ac_shape)
        _shape_lay.addStretch()
        self._drift_ac_shape_label = QLabel("AC waveform:")
        cfg_form.addRow(self._drift_ac_shape_label, self._drift_ac_shape_widget)

        # Per-channel AC config grid (visible when chk_drift_ac is checked).
        # Columns: [✓ axis] | Amp (kV) | Freq (Hz) | [preset] | Phase (°)
        self._drift_ac_widget = QWidget()
        _dac_grid = QGridLayout(self._drift_ac_widget)
        _dac_grid.setContentsMargins(0, 0, 0, 0)
        _dac_grid.setSpacing(4)

        # Header row.
        for _col, _hdr in enumerate(("", "Amp (kV)", "Freq (Hz)", "", "Phase (°)"),
                                    start=0):
            _lbl = QLabel(_hdr)
            _lbl.setStyleSheet(f"color: {theme.NEUTRAL}; font-size: 9px;")
            _dac_grid.addWidget(_lbl, 0, _col)

        self._drift_ac_checks:  dict[str, QCheckBox] = {}
        self._drift_ac_amp_spins:  dict[str, QuietDoubleSpinBox] = {}
        self._drift_ac_freq_spins: dict[str, QuietDoubleSpinBox] = {}
        self._drift_ac_presets:    dict[str, NoScrollComboBox] = {}
        self._drift_ac_phase_spins: dict[str, QuietDoubleSpinBox] = {}

        for _row_i, _amp in enumerate(SC.AMP_LABELS, start=1):
            # Enable checkbox + axis label.
            _cb = QCheckBox(_amp)
            _cb.setChecked(True)

            # Amplitude spinbox.
            _amp_spn = QuietDoubleSpinBox()
            _amp_spn.setRange(0.0, CAL_MAX_KV)
            _amp_spn.setValue(DRIFT_DEFAULT_KV)
            _amp_spn.setDecimals(3)
            _amp_spn.setToolTip(f"Peak sine amplitude for {_amp} (kV)")

            # Frequency spinbox + preset combo.
            _freq_spn = QuietDoubleSpinBox()
            _freq_spn.setRange(1.0, 10000.0)
            _freq_spn.setValue(517.0)
            _freq_spn.setDecimals(1)
            _freq_spn.setToolTip(f"Sine frequency for {_amp}")
            _pre = NoScrollComboBox()
            for _f in CAL_AC_FREQ_PRESETS:
                _pre.addItem(f"{_f:.0f} Hz", userData=_f)
            _pre.setCurrentIndex(1)   # 517 Hz default
            _pre.currentIndexChanged.connect(
                lambda _idx, s=_freq_spn, p=_pre: s.setValue(p.currentData())
            )

            # Phase spinbox — 0 or 180 covers all common use cases;
            # full 0-360 range allows arbitrary phase if needed.
            _ph_spn = QuietDoubleSpinBox()
            _ph_spn.setRange(0.0, 360.0)
            _ph_spn.setValue(0.0)
            _ph_spn.setDecimals(1)
            _ph_spn.setSingleStep(90.0)
            _ph_spn.setToolTip(
                f"Phase offset for {_amp} in degrees (0 = normal, 180 = inverted). "
                "Use 180° on one axis of a pair to replicate the 180° phase "
                "difference you set on the function generator."
            )

            # Dim controls when the channel is disabled.
            def _on_ac_toggle(checked, widgets=(_amp_spn, _freq_spn, _pre, _ph_spn)):
                for w in widgets:
                    w.setEnabled(checked)
            _cb.toggled.connect(_on_ac_toggle)

            _dac_grid.addWidget(_cb,       _row_i, 0)
            _dac_grid.addWidget(_amp_spn,  _row_i, 1)
            _dac_grid.addWidget(_freq_spn, _row_i, 2)
            _dac_grid.addWidget(_pre,      _row_i, 3)
            _dac_grid.addWidget(_ph_spn,   _row_i, 4)

            self._drift_ac_checks[_amp]      = _cb
            self._drift_ac_amp_spins[_amp]   = _amp_spn
            self._drift_ac_freq_spins[_amp]  = _freq_spn
            self._drift_ac_presets[_amp]     = _pre
            self._drift_ac_phase_spins[_amp] = _ph_spn

        self._drift_ac_chan_label = QLabel("AC channels:")
        cfg_form.addRow(self._drift_ac_chan_label, self._drift_ac_widget)
        return cfg_box

    def _build_run_controls(self, col: QVBoxLayout) -> None:
        """Run / Abort buttons, progress bars, state label, and dwell timer.

        Adds widgets directly to *col* (the left column layout) so callers
        don't need to handle a heterogeneous mix of layouts and widgets.
        """
        # ── Run / Abort ────────────────────────────────────────────────────
        run_row = QHBoxLayout()
        self.btn_run = QPushButton("Run")
        self.btn_run.setMinimumHeight(32)
        self.btn_run.setStyleSheet(
            "QPushButton { background:#1a7000; color:white; font-weight:bold; }"
            "QPushButton:hover { background:#228a00; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        self.btn_run.clicked.connect(self._on_run_clicked)
        self.btn_abort = QPushButton("Abort")
        self.btn_abort.setMinimumHeight(32)
        self.btn_abort.setEnabled(False)
        self.btn_abort.setStyleSheet(
            "QPushButton { background:#8c0000; color:white; font-weight:bold; }"
            "QPushButton:hover { background:#a00000; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        self.btn_abort.clicked.connect(self._on_abort_clicked)
        run_row.addWidget(self.btn_run)
        run_row.addWidget(self.btn_abort)
        col.addLayout(run_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        col.addWidget(self.progress)

        self._dwell_bar = QProgressBar()
        self._dwell_bar.setRange(0, 1000)
        self._dwell_bar.setValue(0)
        self._dwell_bar.setTextVisible(True)
        self._dwell_bar.setFormat("Zero dwell — 0.0 s remaining")
        self._dwell_bar.setVisible(False)
        col.addWidget(self._dwell_bar)

        self.lbl_state = QLabel("Idle")
        self.lbl_state.setStyleSheet(f"color: {theme.NEUTRAL}; font-size: 10px;")
        col.addWidget(self.lbl_state)

        # The dwell tick drives the countdown bar; its start/total state
        # (_dwell_start_t, _dwell_total_s) are initialised in __init__.
        self._dwell_tick = QTimer(self)
        self._dwell_tick.setInterval(50)   # 50 ms → smooth countdown
        self._dwell_tick.timeout.connect(self._update_dwell_bar)

    # ------------------------------------------------------------------
    # Channel selection helpers
    # ------------------------------------------------------------------

    def _on_chan_all_toggled(self, checked: bool):
        for cb in self._chan_checks.values():
            cb.setEnabled(not checked)
            if checked:
                cb.setChecked(True)
        self._refresh_run_enabled()

    # ------------------------------------------------------------------
    # Keep-awake
    # ------------------------------------------------------------------

    @staticmethod
    def _set_execution_state(active: bool) -> None:
        """Set or clear the Windows thread execution state.

        ES_CONTINUOUS (0x80000000) — make the new state persist until changed.
        ES_SYSTEM_REQUIRED (0x00000001) — prevent system sleep.
        ES_DISPLAY_REQUIRED (0x00000002) — prevent display sleep/lock.

        Silently no-ops on non-Windows platforms.
        """
        try:
            import ctypes
            ES_CONTINUOUS       = 0x80000000
            ES_SYSTEM_REQUIRED  = 0x00000001
            ES_DISPLAY_REQUIRED = 0x00000002
            flags = (ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
                     if active else ES_CONTINUOUS)
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
        except Exception:
            pass

    def _on_keep_awake_toggled(self, checked: bool) -> None:
        self._set_execution_state(checked)

    # ------------------------------------------------------------------
    # Mode toggling
    # ------------------------------------------------------------------

    def _on_mode_toggled(self, *_):
        is_drift = self.rb_drift.isChecked()
        is_ac    = self.rb_ac.isChecked()

        # Channel selector only applies to DC/AC sweep; drift always drives all.
        self._chan_label.setVisible(not is_drift)
        self._chan_widget.setVisible(not is_drift)

        # AC sweep frequency — visible only in AC sweep mode.
        self._ac_freq_label.setVisible(is_ac)
        self._ac_freq_widget.setVisible(is_ac)

        # Drift-only rows.
        self._drift_setpoint_label.setVisible(is_drift)
        self._drift_setpoint_row.setVisible(is_drift)
        self._drift_duration_label.setVisible(is_drift)
        self._drift_duration_row.setVisible(is_drift)
        self._drift_ac_label.setVisible(is_drift)
        self.chk_drift_ac.setVisible(is_drift)
        drift_ac_on = is_drift and self.chk_drift_ac.isChecked()
        self._drift_ac_shape_label.setVisible(drift_ac_on)
        self._drift_ac_shape_widget.setVisible(drift_ac_on)
        self._drift_ac_chan_label.setVisible(drift_ac_on)
        self._drift_ac_widget.setVisible(drift_ac_on)

        self._refresh_run_enabled()

    def _on_drift_ac_toggled(self, checked: bool):
        self._drift_ac_shape_label.setVisible(checked)
        self._drift_ac_shape_widget.setVisible(checked)
        self._drift_ac_chan_label.setVisible(checked)
        self._drift_ac_widget.setVisible(checked)

    def _get_selected_channels(self) -> list[str]:
        """Return the list of amp labels selected for the next sweep.

        Returns all four labels when "All" is checked; otherwise only those
        whose individual checkbox is checked.  Never returns an empty list —
        falls back to all labels so the Run button is always pressable unless
        ALL individual boxes are unchecked while "All" is off.
        """
        if self._chan_all.isChecked():
            return list(SC.AMP_LABELS)
        selected = [amp for amp, cb in self._chan_checks.items() if cb.isChecked()]
        return selected  # may be empty — caller must handle

    def _refresh_run_enabled(self, *_):
        has_load = self.cbo_load.currentData() is not None
        # For sweep/AC modes, at least one channel must be selected.
        is_drift = self.rb_drift.isChecked()
        has_channels = is_drift or bool(self._get_selected_channels())
        self.btn_run.setEnabled(
            has_load and has_channels and self._connected and self._runner is None
        )

    # ------------------------------------------------------------------
    # Run / Abort
    # ------------------------------------------------------------------

    def _on_run_clicked(self):
        load_condition = self.cbo_load.currentData()
        if load_condition is None:
            QMessageBox.warning(self, "Load condition required",
                                 "Select a load condition before running.")
            return
        if self.rb_drift.isChecked() and load_condition is LoadCondition.ON_PLATES \
                and self.spn_drift_h.value() > DRIFT_MAX_ATTENDED_H:
            QMessageBox.warning(
                self, "Drift duration refused",
                f"Runs longer than {DRIFT_MAX_ATTENDED_H:.1f} h are only "
                f"permitted with the amplifier DISCONNECTED from the steerer.",
            )
            return

        dialog = _PreRunChecklistDialog(load_condition, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # Resolved before the stream is reconfigured: the pair profile has to
        # be pointed at the first amplifier the sequence will drive, and that
        # is only knowable from the channel selection.
        channels = self._get_selected_channels()

        funcgen_map = self.beamline.build_funcgen_map()
        self._writer = CalibrationWriter(metadata={
            "load_condition": load_condition.value,
        })
        self._runner = CalibrationRunner(
            funcgen_map, load_condition, writer=self._writer,
        )
        self._runner.operator_note = self.le_note.text().strip()
        self._runner._zero_dwell_s = self.spn_zero_dwell.value()
        self._runner._dwell_output_off = self.chk_dwell_output_off.isChecked()
        self._runner.progress.connect(self._on_progress)
        self._runner.dwell_started.connect(self._on_dwell_started)
        # row_recorded is intentionally not connected. The writer already
        # persists every row to CSV; the only GUI consumer was the live
        # scatter/fit, and rendering 50+ points per pass bought nothing the
        # CSV does not give you afterwards.
        self._runner.finished.connect(self._on_finished)
        self._runner.error.connect(self._on_runner_error)
        self._runner.pair_change_requested.connect(self.pair_change_requested)
        self._runner.overcurrent.connect(self._on_overcurrent)

        # Force the calibration stream profile; restore whatever was live
        # once the run ends or aborts (see _on_finished).
        #
        # Sweeps use the 2-channel pair profile (4x the sample density on the
        # only two monitors a single-channel sweep measures).  Drift drives all
        # four amplifiers at once, so it must keep all eight monitors in the
        # scan list and stays on WAVEFORM.
        # Capture the prior stream state from the BEAMLINE, not from the
        # cached signal value. profile_changed is only emitted on an actual
        # change, so at startup nothing has ever emitted it and the cache is
        # still None — which made the restore below a silent no-op and left the
        # stream parked on the calibration profile after every run.
        self._prior_profile = getattr(self.beamline, "active_profile", None) \
            or self._current_profile
        self._prior_pair = getattr(self.beamline, "active_pair", None)

        is_drift = self.rb_drift.isChecked()
        self._runner._use_pair_profile = not is_drift
        if is_drift:
            # Drift drives all four amplifiers, so it needs every monitor in
            # the scan list; there is no pair to target.
            self.profile_change_requested.emit(CAL_DRIFT_PROFILE)
        else:
            # Acquire profile AND pair in one restart, unconditionally. Doing
            # it as two requests broke whenever the stream was already on
            # AMP_PAIR (both requests early-returned) and cost two back-to-back
            # stream restarts when it was not.
            first = (channels or list(SC.AMP_LABELS))[0]
            self.pair_profile_requested.emit(CAL_SWEEP_PROFILE, first)

        self.btn_run.setEnabled(False)
        self.btn_abort.setEnabled(True)
        self.run_state_changed.emit(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        if self.rb_sweep.isChecked():
            mode_label = "DC sweep"
        elif self.rb_ac.isChecked():
            mode_label = "AC sweep"
        else:
            mode_label = "drift"
        self.lbl_state.setText(f"Running ({mode_label})…")

        rtz = bool(self.cbo_step_mode.currentData())
        if self.rb_sweep.isChecked():
            self._runner.start_sweep(channels=channels, return_to_zero=rtz)
        elif self.rb_ac.isChecked():
            self._runner.start_ac_sweep(
                channels=channels,
                freq_hz=self.spn_ac_freq.value(),
                return_to_zero=rtz,
            )
        else:
            ac_channels = None
            if self.chk_drift_ac.isChecked():
                _shape = self.cbo_drift_ac_shape.currentData()
                built = {
                    amp: {
                        "waveform":     _shape,
                        "freq_hz":      self._drift_ac_freq_spins[amp].value(),
                        "amplitude_kv": self._drift_ac_amp_spins[amp].value(),
                        "phase_deg":    self._drift_ac_phase_spins[amp].value(),
                    }
                    for amp in SC.AMP_LABELS
                    if self._drift_ac_checks[amp].isChecked()
                }
                ac_channels = built or None  # None if every channel was unchecked → pure DC
            self._runner.start_drift(
                self.spn_drift_kv.value(),
                self.spn_drift_h.value(),
                ac_channels=ac_channels,
            )

    def _on_step_mode_changed(self, *_):
        """Show what return-to-zero costs in wall-clock time.

        Stated up front because it is the whole trade: the mode exists to make
        the measurement match the application, and it pays for that in run
        length. Better to see the number before starting than to discover it
        two hours in.
        """
        rtz = bool(self.cbo_step_mode.currentData())
        self._dwell_label.setVisible(rtz)
        self._dwell_widget.setVisible(rtz)
        self._dwell_output_off_label.setVisible(rtz)
        self.chk_dwell_output_off.setVisible(rtz)
        if not rtz:
            self.lbl_step_cost.setText("")
            return
        dwell = self.spn_zero_dwell.value()
        n_dc = len(sweep_points("up")) * len(CAL_PASSES) * len(SC.AMP_LABELS)
        n_ac = len(ac_sweep_points()) * len(SC.AMP_LABELS)
        self.lbl_step_cost.setText(
            f"Adds ~{dwell:.1f} s per setpoint: "
            f"+{n_dc * dwell / 60:.0f} min on a full DC sweep, "
            f"+{n_ac * dwell / 60:.0f} min on a full AC sweep."
        )

    def _on_abort_clicked(self):
        if self._runner is not None:
            self._runner.abort()

    def _on_dwell_started(self, total_s: float):
        self._dwell_start_t = time.monotonic()
        self._dwell_total_s = total_s
        self._dwell_bar.setRange(0, 1000)
        self._dwell_bar.setValue(0)
        self._dwell_bar.setFormat(f"Zero dwell — {total_s:.1f} s remaining")
        self._dwell_bar.setVisible(True)
        self._dwell_tick.start()

    def _update_dwell_bar(self):
        if self._dwell_start_t is None:
            return
        elapsed = time.monotonic() - self._dwell_start_t
        total = max(self._dwell_total_s, 0.001)
        frac = min(elapsed / total, 1.0)
        remaining = max(total - elapsed, 0.0)
        self._dwell_bar.setValue(int(frac * 1000))
        self._dwell_bar.setFormat(f"Zero dwell — {remaining:.1f} s remaining")

    def _stop_dwell_bar(self):
        self._dwell_tick.stop()
        self._dwell_bar.setVisible(False)
        self._dwell_start_t = None

    def _on_progress(self, done: int, total: int, label: str):
        self._stop_dwell_bar()
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        self.lbl_state.setText(label)

    def _on_runner_error(self, msg: str):
        log.error("calibration run error: %s", msg)

    def _on_overcurrent(self, amp_label: str, measured_ma: float,
                        limit_ma: float):
        """The over-current interlock fired and the runner has already aborted.

        Presented as a safety event rather than a malfunction. Probing a
        capacitive-drive envelope at increasing frequency is EXPECTED to find
        the current wall — that is the measurement — so the wording says what
        happened and what it implies, without implying the operator did
        something wrong.

        The dialog is opened from a deferred call, never inline.  A modal
        dialog runs a nested Qt event loop, so opening one directly inside a
        signal handler resumes timers and delivers queued windows while the
        handler's caller is still mid-abort.  The runner now zeroes the
        outputs before it emits, so this is belt-and-braces — but a warning
        about an over-current is the last place to leave a re-entrancy hazard
        lying around.
        """
        log.error("over-current on %s: %.1f mA (limit %.1f mA)",
                  amp_label, measured_ma, limit_ma)
        # Freeze the amp tab FIRST, before the label and long before the
        # dialog. Its trend buffers and waveform ring are still being
        # overwritten by the live stream, and the excursion that caused this
        # is already several windows old — every redraw between here and the
        # operator clicking OK is one more chance for it to scroll away.
        self.overcurrent_tripped.emit(amp_label, measured_ma, limit_ma)
        self.lbl_state.setText(
            f"STOPPED — {amp_label} drew {measured_ma:.1f} mA "
            f"(limit {limit_ma:.0f} mA). Outputs zeroed and off."
        )
        QTimer.singleShot(0, lambda: QMessageBox.warning(
            self, "Over-current — run stopped",
            f"<b>{amp_label} drew {measured_ma:.1f} mA peak</b>, over the "
            f"{limit_ma:.0f} mA limit.<br><br>"
            f"The run was aborted, every channel commanded to 0 V and all "
            f"outputs turned off <i>before</i> this message appeared.<br><br>"
            f"On a capacitive load the peak current is "
            f"<tt>2&pi;&middot;f&middot;C&middot;V_pk</tt>, so it rises with "
            f"both frequency and amplitude. If this happened while raising "
            f"frequency, the amplitude ladder for that frequency is the thing "
            f"to lower — not a fault in the amplifier."
        ))

    def _on_finished(self, csv_path: str):
        self._stop_dwell_bar()
        self.btn_run.setEnabled(True)
        self.btn_abort.setEnabled(False)
        self.run_state_changed.emit(False)
        # Set progress to 100% on completion.
        maximum = max(self.progress.maximum(), 1)
        self.progress.setValue(maximum)
        self.lbl_state.setText(
            f"Finished — {csv_path}" if csv_path else "Stopped"
        )
        self._runner = None
        self._writer = None
        self._refresh_run_enabled()

        # Restore whatever stream configuration was live before this run took
        # it over. Profile AND pair: a sweep re-points AMP_PAIR at each
        # amplifier as it goes, so restoring only the profile would leave the
        # stream on the last-swept pair rather than the one the operator chose.
        if self._prior_profile:
            if self._prior_pair and is_pair_channel(self._prior_profile):
                self.pair_profile_requested.emit(self._prior_profile,
                                                 self._prior_pair)
            else:
                self.profile_change_requested.emit(self._prior_profile)
        self._prior_profile = None
        self._prior_pair = None

    # ------------------------------------------------------------------
    # _lj_tabs contract (see rbl/gui/app.py) — same shape as AmpTab/CurrentTab.
    # ------------------------------------------------------------------

    def on_labjack_connected(self, serial: str):
        self._connected = True
        self.lj_panel.set_connected(True, serial)
        self._refresh_run_enabled()

    def on_labjack_disconnected(self):
        self._connected = False
        self.lj_panel.set_connected(False)
        self._refresh_run_enabled()
        if self._runner is not None:
            self._runner.abort()

    def on_labjack_error(self, msg: str):
        QMessageBox.warning(self, "LabJack poll error", msg)

    def on_profile_changed(self, profile_name: str):
        self._current_profile = profile_name

    def on_window(self, payload: dict):
        """Connected to Beamline.raw_window_ready (see rbl/gui/app.py)."""
        if self._runner is not None:
            self._runner.on_window(payload)

    # ------------------------------------------------------------------

    def shutdown(self):
        self._set_execution_state(False)   # always release the sleep lock on exit
        if self._runner is not None:
            self._runner.abort()
