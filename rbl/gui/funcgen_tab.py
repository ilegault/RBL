"""
funcgen_tab.py
PySide6 widget for the "Function Generators" outer tab.

Controls two RIGOL DG1022Z function generators (4 channels total) that
drive an EEL5000 HV amplifier into the NEC ES5 electrostatic XY steerer.

Safety rules enforced here:
  - All voltage spinboxes have MAX_GEN_VOLTS as their hard maximum.
  - Outputs default OFF; voltages default 0 V.
  - The poll timer is READ-ONLY; it never writes to the instruments.
  - close_session() closes only the VISA sessions; outputs are never
    disabled automatically (instrument retains state after app exits).
"""
import json
import logging
import os
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QGroupBox, QLabel, QPushButton, QDoubleSpinBox, QComboBox,
    QTextEdit, QLineEdit, QCheckBox, QMessageBox, QSizePolicy,
    QScrollArea, QApplication,
)

from rbl.hardware.funcgen_driver import DG1022Z, discover, MAX_GEN_VOLTS, MAX_AMP_VPP

# Persistence file — keyed on serial, survives replug
_CONFIG_PATH = Path.home() / ".config" / "rbl" / "funcgen.json"

# EEL5000 gain: 1 V_gen -> 1000 V_plate
_AMP_GAIN = 1000.0

# The EEL5000 input tolerates ±MAX_GEN_VOLTS (5 V). The *instantaneous* voltage
# the amplifier sees is the offset plus half the peak-to-peak amplitude — for an
# AC waveform the signal swings ±amp/2 about the offset, so the worst-case peak
# magnitude is |offset| + amp/2. That combined peak, not either field alone, is
# what must stay within the amplifier's rail:
#   * peak > PEAK_MAX_VOLTS  -> apply is blocked outright.
#   * peak > PEAK_WARN_VOLTS -> apply asks the user to confirm first.
PEAK_MAX_VOLTS  = MAX_GEN_VOLTS   # hard ceiling: amplifier cannot exceed ±5 V
PEAK_WARN_VOLTS = 4.0             # advisory threshold — confirm before applying

# Axis pairs MUST live on the same physical generator: only
# :PHASe:SYNChronize (same-unit) gives deterministic phase alignment.
# Cross-unit alignment is impossible on the DG1022Z.
CHANNEL_ROLE = {
    "A1": "X+", "A2": "X-",
    "B1": "Y+", "B2": "Y-",
}


def channel_peak_volts(shape: str, amp_vpp: float, offset_v: float) -> float:
    """Worst-case instantaneous voltage magnitude the amplifier input sees (V).

    For any AC shape the waveform swings ±amp/2 about the offset, so the peak
    magnitude is |offset| + amp/2. In DC mode the held voltage is the offset,
    so the peak is just |offset|.
    """
    if shape == "DC":
        return abs(offset_v)
    return abs(offset_v) + abs(amp_vpp) / 2.0


def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_config(data: dict):
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_CONFIG_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


# ─── Per-channel panel ────────────────────────────────────────────────────────

class ChannelPanel(QGroupBox):
    """One self-contained panel for one generator channel."""

    SHAPES = ["Sine", "Triangle", "Square", "Pulse", "DC"]

    def __init__(self, label: str, parent=None, phase_default: float = 0.0):
        super().__init__(label, parent)
        self._label = label
        self._phase_default = phase_default
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(6, 6, 6, 6)

        form = QFormLayout()
        form.setSpacing(3)

        # Shape — default to Triangle
        self.cbo_shape = QComboBox()
        self.cbo_shape.addItems(self.SHAPES)
        self.cbo_shape.setCurrentText("Triangle")
        form.addRow("Shape:", self.cbo_shape)

        # Frequency — default 10 Hz
        self.spn_freq = QDoubleSpinBox()
        self.spn_freq.setRange(0.0001, 25_000_000.0)
        self.spn_freq.setValue(10.0)
        self.spn_freq.setDecimals(4)
        self.spn_freq.setMinimumWidth(80)
        self.spn_freq.setMaximumWidth(110)
        self.lbl_freq = QLabel("Frequency:")
        freq_row = QHBoxLayout()
        freq_row.setContentsMargins(0, 0, 0, 0)
        freq_row.setSpacing(4)
        freq_row.addWidget(self.spn_freq, stretch=1)
        freq_row.addWidget(QLabel("Hz"))
        form.addRow(self.lbl_freq, freq_row)

        # Amplitude — peak-to-peak (RIGOL native). A centred 10 Vpp sine reaches
        # the full ±5 V (±5 kV) rail, so amplitude alone is allowed up to 10 Vpp;
        # the combined-peak interlock (below) still limits |offset| + amp/2 ≤ 5 V.
        self.spn_amp = QDoubleSpinBox()
        self.spn_amp.setRange(0.0, MAX_AMP_VPP)
        self.spn_amp.setValue(0.0)
        self.spn_amp.setDecimals(4)
        self.spn_amp.setMinimumWidth(80)
        self.spn_amp.setMaximumWidth(110)
        self.lbl_amp = QLabel("Amplitude:")
        amp_row = QHBoxLayout()
        amp_row.setContentsMargins(0, 0, 0, 0)
        amp_row.setSpacing(4)
        amp_row.addWidget(self.spn_amp, stretch=1)
        amp_row.addWidget(QLabel("Vpp"))
        form.addRow(self.lbl_amp, amp_row)

        # HV consequence label (updates live)
        self.lbl_hv = QLabel("→ 0.0000 kV/plate  (0.0000 kV p-p)")
        self.lbl_hv.setStyleSheet("color: #7a2000; font-size: 10px;")
        form.addRow("", self.lbl_hv)

        # Offset
        self.spn_offset = QDoubleSpinBox()
        self.spn_offset.setRange(-MAX_GEN_VOLTS, MAX_GEN_VOLTS)
        self.spn_offset.setValue(0.0)
        self.spn_offset.setDecimals(4)
        self.spn_offset.setMinimumWidth(80)
        self.spn_offset.setMaximumWidth(110)
        self.lbl_offset = QLabel("Offset:")
        offset_row = QHBoxLayout()
        offset_row.setContentsMargins(0, 0, 0, 0)
        offset_row.setSpacing(4)
        offset_row.addWidget(self.spn_offset, stretch=1)
        offset_row.addWidget(QLabel("V"))
        form.addRow(self.lbl_offset, offset_row)

        # Phase — 0° for X+/Y+, 180° for X-/Y- (push-pull differential drive).
        # This value is sent via both the :APPLy command and :PHASe:SYNChronize.
        self.spn_phase = QDoubleSpinBox()
        self.spn_phase.setRange(-360.0, 360.0)
        self.spn_phase.setValue(self._phase_default)
        self.spn_phase.setDecimals(1)
        self.spn_phase.setMinimumWidth(80)
        self.spn_phase.setMaximumWidth(110)
        self.lbl_phase = QLabel("Phase:")
        phase_row = QHBoxLayout()
        phase_row.setContentsMargins(0, 0, 0, 0)
        phase_row.setSpacing(4)
        phase_row.addWidget(self.spn_phase, stretch=1)
        phase_row.addWidget(QLabel("°"))
        form.addRow(self.lbl_phase, phase_row)

        # Load
        self.le_load = QLineEdit("INFinity")
        self.le_load.setMaximumWidth(100)
        self.le_load.setToolTip(
            "Output load impedance. Use INFinity (high-Z) for the EEL5000 "
            "amplifier input. Wrong load halves the real delivered voltage."
        )
        form.addRow("Load:", self.le_load)

        layout.addLayout(form)

        # Output toggle
        btn_row = QHBoxLayout()
        self.btn_output = QPushButton("Output OFF")
        self.btn_output.setCheckable(True)
        self.btn_output.setChecked(False)
        self.btn_output.setMinimumHeight(32)
        self.btn_output.setStyleSheet(
            "QPushButton { background:#8c0000; color:white; font-weight:bold; }"
            "QPushButton:checked { background:#1a7000; color:white; font-weight:bold; }"
            "QPushButton:hover { background:#a00000; }"
            "QPushButton:checked:hover { background:#228a00; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        self.btn_output.toggled.connect(self._on_output_toggled)
        btn_row.addWidget(self.btn_output)

        self.btn_apply = QPushButton("Apply")
        self.btn_apply.setMinimumHeight(32)
        self.btn_apply.setStyleSheet(
            "QPushButton { background:#004e8c; color:white; font-weight:bold; }"
            "QPushButton:hover { background:#0063b1; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        btn_row.addWidget(self.btn_apply)
        layout.addLayout(btn_row)

        # Read-back
        self.lbl_readback = QLabel("—")
        self.lbl_readback.setWordWrap(True)
        self.lbl_readback.setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace; font-size: 10px; color: #444;"
        )
        layout.addWidget(self.lbl_readback)
        layout.addStretch()

        # Wire shape change
        self.cbo_shape.currentTextChanged.connect(self._on_shape_changed)
        self.spn_amp.valueChanged.connect(self._update_hv_label)
        self.spn_offset.valueChanged.connect(self._update_hv_label)
        self._on_shape_changed(self.cbo_shape.currentText())

        self.set_connected(False)

    def _on_output_toggled(self, checked: bool):
        self.btn_output.setText("Output ON" if checked else "Output OFF")

    def _on_shape_changed(self, shape: str):
        dc = (shape == "DC")
        # In DC mode: freq, amp, and phase don't apply; offset becomes the hold voltage
        for w in (self.lbl_freq, self.spn_freq,
                  self.lbl_amp, self.spn_amp,
                  self.lbl_phase, self.spn_phase):
            w.setVisible(not dc)
        self.lbl_offset.setText("Hold voltage (V):" if dc else "Offset:")
        self._update_hv_label()

    def _update_hv_label(self):
        # Gain is 1000× (1 V_gen -> 1 kV_plate), so the numeric V value equals kV.
        shape  = self.cbo_shape.currentText()
        amp_vpp = self.spn_amp.value()
        offset  = self.spn_offset.value()

        # Combined peak the amplifier input actually sees (|offset| + amp/2).
        peak = channel_peak_volts(shape, amp_vpp, offset)

        if shape == "DC":
            # DC hold: the plate sits at a fixed offset·gain.
            plate_kv = abs(offset) * _AMP_GAIN / 1000.0
            self.lbl_hv.setText(
                f"→ {plate_kv:.4f} kV/plate (DC)   |   peak {peak:.3f} V"
            )
        else:
            # Amplitude is peak-to-peak: the plate swings ±(amp/2)·gain about the
            # offset, so plate 0-to-peak = amp/2 kV and plate p-p = amp kV.
            plate_peak_kv = (amp_vpp / 2.0) * _AMP_GAIN / 1000.0
            plate_pp_kv   = amp_vpp * _AMP_GAIN / 1000.0
            self.lbl_hv.setText(
                f"→ ±{plate_peak_kv:.4f} kV/plate  ({plate_pp_kv:.4f} kV p-p)"
                f"   |   peak {peak:.3f} V"
            )
        if peak > PEAK_MAX_VOLTS + 1e-9:
            # Over the amplifier's ±5 V rail — apply will be blocked.
            self.lbl_hv.setStyleSheet(
                "color: #c0392b; font-size: 10px; font-weight: bold;"
            )
        elif peak > PEAK_WARN_VOLTS + 1e-9:
            # Above the 4 V advisory — apply will ask to confirm.
            self.lbl_hv.setStyleSheet(
                "color: #b06a00; font-size: 10px; font-weight: bold;"
            )
        else:
            self.lbl_hv.setStyleSheet("color: #7a2000; font-size: 10px;")

    def set_connected(self, on: bool):
        for w in (self.btn_apply, self.btn_output, self.spn_freq,
                  self.spn_amp, self.spn_offset, self.spn_phase,
                  self.le_load, self.cbo_shape):
            w.setEnabled(on)

    def get_params(self) -> dict:
        phase = self.spn_phase.value()
        return {
            "shape":       self.cbo_shape.currentText(),
            "freq":        self.spn_freq.value(),
            "amp":         self.spn_amp.value(),
            "offset":      self.spn_offset.value(),
            "phase":       phase,
            "start_phase": phase,
            "load":        self.le_load.text().strip() or "INFinity",
            "output":      self.btn_output.isChecked(),
        }

    def update_readback(self, state: dict):
        if "error" in state:
            self.lbl_readback.setText(f"Read error: {state['error']}")
            return
        on  = "ON" if state.get("output") else "OFF"
        self.lbl_readback.setText(
            f"shape={state.get('shape','?')}  "
            f"freq={state.get('freq',0):.2f} Hz  "
            f"amp={state.get('amp',0):.4f} Vpp  "
            f"offset={state.get('offset',0):.4f} V  "
            f"phase={state.get('phase',0):.2f}°  "
            f"out={on}  load={state.get('load','?')}"
        )


# ─── Top-level tab widget ─────────────────────────────────────────────────────

class FuncGenTab(QWidget):
    """The 'Function Generators' outer tab."""

    # Poll read-back at 500 ms.  The timer is READ-ONLY — it never writes.
    _POLL_INTERVAL_MS = 500

    def __init__(self, parent=None):
        super().__init__(parent)

        # Driver instances (None until connected)
        self._gen: dict[str, DG1022Z | None] = {"A": None, "B": None}
        self._discovered: list[dict] = []   # last discover() result
        self._config = _load_config()

        outer_layout = QHBoxLayout(self)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        outer_layout.setSpacing(8)
        left_layout = QVBoxLayout()
        left_layout.setSpacing(8)

        # ── Discovery & instrument assignment ─────────────────────────────
        disc_box = QGroupBox("RIGOL DG1022Z — Discover & Assign")
        disc_layout = QVBoxLayout(disc_box)
        disc_layout.setSpacing(4)

        btn_row = QHBoxLayout()
        self.btn_discover = QPushButton("Refresh / Discover")
        self.btn_discover.clicked.connect(self._do_discover)
        btn_row.addWidget(self.btn_discover)
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.setStyleSheet(
            "QPushButton { background:#004e8c; color:white; font-weight:bold; }"
            "QPushButton:hover { background:#0063b1; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        self.btn_connect.clicked.connect(self._toggle_connection)
        btn_row.addWidget(self.btn_connect)
        disc_layout.addLayout(btn_row)

        assign_form = QFormLayout()
        assign_form.setSpacing(4)
        self.cbo_gen_a = QComboBox()
        self.cbo_gen_b = QComboBox()
        self.cbo_gen_a.setMinimumWidth(260)
        self.cbo_gen_b.setMinimumWidth(260)
        self.cbo_gen_a.addItem("— not selected —")
        self.cbo_gen_b.addItem("— not selected —")
        assign_form.addRow("Generator A:", self.cbo_gen_a)
        assign_form.addRow("Generator B:", self.cbo_gen_b)
        disc_layout.addLayout(assign_form)

        status_row = QHBoxLayout()
        self.lbl_status_a = QLabel("● Gen A: not connected")
        self.lbl_status_b = QLabel("● Gen B: not connected")
        self.lbl_status_a.setStyleSheet("color: #666666; font-weight: bold;")
        self.lbl_status_b.setStyleSheet("color: #666666; font-weight: bold;")
        status_row.addWidget(self.lbl_status_a)
        status_row.addStretch()
        status_row.addWidget(self.lbl_status_b)
        disc_layout.addLayout(status_row)

        # ── Cross-unit timebase sharing (for a locked X/Y phase relationship) ─
        self.chk_ext_ref = QCheckBox(
            "Share 10 MHz timebase  (Gen A = master → Gen B = external ref)"
        )
        self.chk_ext_ref.setToolTip(
            "Two separate DG1022Z units drift on their own clocks, so their "
            "X/Y phase relationship will not stay fixed.\n\n"
            "To lock them: connect a BNC cable from Gen A's rear-panel "
            "[10MHz Out] to Gen B's rear-panel [10MHz In], then enable this.\n"
            "Gen A keeps its internal clock (master); Gen B follows the shared "
            "10 MHz reference (external).  Un-checking returns both to internal."
        )
        self.chk_ext_ref.setEnabled(False)
        self.chk_ext_ref.toggled.connect(self._on_ext_ref_toggled)
        disc_layout.addWidget(self.chk_ext_ref)

        self.lbl_ref_status = QLabel("")
        self.lbl_ref_status.setStyleSheet(
            "color: #555; font-style: italic; font-size: 10px;"
        )
        self.lbl_ref_status.setWordWrap(True)
        disc_layout.addWidget(self.lbl_ref_status)

        clk_row = QHBoxLayout()
        self.lbl_clk_a = QLabel("● Gen A: timebase —")
        self.lbl_clk_b = QLabel("● Gen B: timebase —")
        self.lbl_clk_a.setStyleSheet("color: #555; font-size: 10px;")
        self.lbl_clk_b.setStyleSheet("color: #555; font-size: 10px;")
        clk_row.addWidget(self.lbl_clk_a)
        clk_row.addStretch()
        clk_row.addWidget(self.lbl_clk_b)
        disc_layout.addLayout(clk_row)

        # Persistent combined timebase indicator — refreshed on connect and
        # after every Apply All.  Only shows "B: EXT" when the readback
        # confirmed EXT; never shows a locked state on a failed lock.
        self.lbl_timebase = QLabel("Timebase:  A: —   B: —")
        self.lbl_timebase.setStyleSheet(
            "color: #333; font-weight: bold; font-size: 10px; padding: 2px;"
        )
        self.lbl_timebase.setAlignment(Qt.AlignmentFlag.AlignCenter)
        disc_layout.addWidget(self.lbl_timebase)

        left_layout.addWidget(disc_box)

        # ── Amplifier input-limit note ────────────────────────────────────
        note_lbl = QLabel(
            "⚠  Amplifier input ceiling — the EEL5000 accepts ±5 V max on its "
            "input (= ±5 kV/plate). Amplitude is peak-to-peak (Vpp): an AC wave "
            "swings ±½·amplitude about the offset, so the peak the amplifier "
            "actually sees is |offset| + ½·amplitude, and THAT combined peak must "
            "stay ≤ 5 V — not each field on its own. So 0 V offset + 10 Vpp "
            "reaches the full ±5 kV rail (peak = 5 V), but e.g. 4 V offset + "
            "4 Vpp is blocked (peak = 6 V). Amplitude allows up to 10 Vpp, offset "
            "up to ±5 V. Apply is blocked above 5 V peak and asks you to confirm "
            "above 4 V. Each channel's live 'peak' readout turns amber past 4 V "
            "and red past 5 V."
        )
        note_lbl.setWordWrap(True)
        note_lbl.setStyleSheet(
            "color: #7a2000; font-size: 9pt; padding: 4px; "
            "background: #fff6e6; border: 1px solid #e0c080; border-radius: 3px;"
        )
        left_layout.addWidget(note_lbl)

        # ── 4 channel panels in 2×2 grid ─────────────────────────────────
        grid = QGridLayout()
        grid.setSpacing(8)
        self.panels: dict[str, ChannelPanel] = {}
        panel_labels = {
            "A1": f"Gen A — Ch 1  ({CHANNEL_ROLE['A1']})",
            "A2": f"Gen A — Ch 2  ({CHANNEL_ROLE['A2']})",
            "B1": f"Gen B — Ch 1  ({CHANNEL_ROLE['B1']})",
            "B2": f"Gen B — Ch 2  ({CHANNEL_ROLE['B2']})",
        }
        # X- and Y- channels use 180° start-phase for push-pull (differential)
        # drive.  With 0° on X+ and 180° on X-, when the X+ plate is at its
        # positive peak the X- plate is at its negative peak, giving the full
        # differential swing without a DC offset on either plate.
        _START_PHASE_DEFAULTS = {"A1": 0.0, "A2": 180.0, "B1": 0.0, "B2": 180.0}
        positions = {"A1": (0, 0), "A2": (0, 1), "B1": (1, 0), "B2": (1, 1)}
        for key, title in panel_labels.items():
            p = ChannelPanel(title, self, phase_default=_START_PHASE_DEFAULTS[key])
            gen_letter = key[0]
            ch_num     = int(key[1])
            p.btn_apply.clicked.connect(
                lambda _, g=gen_letter, c=ch_num: self._apply_channel(g, c)
            )
            self.panels[key] = p
            r, col = positions[key]
            grid.addWidget(p, r, col)
        left_layout.addLayout(grid, stretch=1)

        # ── Apply All ─────────────────────────────────────────────────────
        apply_all_row = QHBoxLayout()
        self.btn_apply_all = QPushButton("Apply All 4 Channels")
        self.btn_apply_all.setEnabled(False)
        self.btn_apply_all.setMinimumHeight(36)
        self.btn_apply_all.setStyleSheet(
            "QPushButton { background:#004e8c; color:white; font-weight:bold;"
            " font-size:13px; }"
            "QPushButton:hover { background:#0063b1; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        self.btn_apply_all.clicked.connect(self._apply_all)
        apply_all_row.addWidget(self.btn_apply_all)
        left_layout.addLayout(apply_all_row)

        # ── SCPI console (right panel — full height) ──────────────────────
        scpi_box = QGroupBox("SCPI Console  (direct instrument access)")
        scpi_vbox = QVBoxLayout(scpi_box)
        scpi_vbox.setSpacing(4)

        tgt_row = QHBoxLayout()
        tgt_row.addWidget(QLabel("Target:"))
        self.cbo_scpi_target = QComboBox()
        self.cbo_scpi_target.addItems(["Gen A", "Gen B"])
        tgt_row.addWidget(self.cbo_scpi_target)
        tgt_row.addStretch()
        scpi_vbox.addLayout(tgt_row)

        cmd_row = QHBoxLayout()
        self.le_scpi_cmd = QLineEdit()
        self.le_scpi_cmd.setPlaceholderText("SCPI command  (e.g. :SOURce1:APPLy?)")
        self.le_scpi_cmd.returnPressed.connect(self._scpi_send)
        self.btn_scpi_send  = QPushButton("Send")
        self.btn_scpi_query = QPushButton("Query")
        self.btn_scpi_err   = QPushButton("Read Errors")
        self.btn_scpi_send.clicked.connect(self._scpi_send)
        self.btn_scpi_query.clicked.connect(self._scpi_query)
        self.btn_scpi_err.clicked.connect(self._scpi_read_errors)
        cmd_row.addWidget(self.le_scpi_cmd, stretch=1)
        cmd_row.addWidget(self.btn_scpi_send)
        cmd_row.addWidget(self.btn_scpi_query)
        cmd_row.addWidget(self.btn_scpi_err)
        scpi_vbox.addLayout(cmd_row)

        self.scpi_log = QTextEdit()
        self.scpi_log.setReadOnly(True)
        self.scpi_log.setFont(QFont("Consolas", 9))
        scpi_vbox.addWidget(self.scpi_log, stretch=1)

        self._set_scpi_enabled(False)

        # ── Assemble outer layout (left 2/3 controls, right 1/3 console) ──
        outer_layout.addLayout(left_layout, stretch=2)
        outer_layout.addWidget(scpi_box, stretch=1)

        # ── Poll timer (read-only) ─────────────────────────────────────────
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self._POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_readback)

        # Auto-populate dropdowns from saved config
        self._populate_dropdowns([])

    # ---- Discovery & connection ---------------------------------------------

    def _do_discover(self):
        self._log_scpi("# Discovering RIGOL DG1022Z instruments…")
        self._discovered = discover()
        self._populate_dropdowns(self._discovered)
        self._log_scpi(f"# Found {len(self._discovered)} instrument(s)")
        for d in self._discovered:
            self._log_scpi(f"#   {d['idn']}  (serial={d['serial']})")

    def _populate_dropdowns(self, instruments: list):
        saved_a = self._config.get("serial_a", "")
        saved_b = self._config.get("serial_b", "")

        for cbo in (self.cbo_gen_a, self.cbo_gen_b):
            cbo.blockSignals(True)
            cbo.clear()
            cbo.addItem("— not selected —")
            for d in instruments:
                cbo.addItem(f"{d['idn']}  [{d['serial']}]", userData=d["serial"])
            cbo.blockSignals(False)

        # Restore saved serial assignments
        for cbo, saved in ((self.cbo_gen_a, saved_a), (self.cbo_gen_b, saved_b)):
            if saved:
                for i in range(cbo.count()):
                    if cbo.itemData(i) == saved:
                        cbo.setCurrentIndex(i)
                        break

    def _toggle_connection(self):
        if self._gen["A"] is not None or self._gen["B"] is not None:
            self._do_disconnect()
        else:
            self._do_connect()

    def _do_connect(self):
        self._poll_timer.stop()
        errors = []

        # Build serial -> resource map from last discover
        serial_map = {d["serial"]: d["resource"] for d in self._discovered}

        for gen_letter, cbo in (("A", self.cbo_gen_a), ("B", self.cbo_gen_b)):
            serial = cbo.currentData()
            if not serial:
                continue
            resource = serial_map.get(serial)
            if not resource:
                errors.append(f"Gen {gen_letter}: serial {serial} not found — run Discover first")
                continue
            try:
                g = DG1022Z(resource)
                # Set high-Z load on both channels (EEL5000 input is high-impedance)
                for ch in (1, 2):
                    g.set_output_load(ch, "INFinity")
                self._gen[gen_letter] = g
                self._log_scpi(f"# Gen {gen_letter} connected: {g.idn()}")
            except Exception as e:
                errors.append(f"Gen {gen_letter}: {e}")

        if errors:
            QMessageBox.warning(self, "Connection errors", "\n".join(errors))

        self._save_serial_assignment()
        self._refresh_connection_ui()
        if any(v is not None for v in self._gen.values()):
            self._poll_timer.start()
            self._refresh_clock_status()

    def _do_disconnect(self):
        self._poll_timer.stop()
        for letter in ("A", "B"):
            if self._gen[letter] is not None:
                # Close VISA session only; do NOT disable outputs.
                # The instrument retains its state after the session closes.
                try:
                    self._gen[letter].close()
                except Exception:
                    pass
                self._gen[letter] = None
        self._refresh_connection_ui()
        self._log_scpi("# Disconnected (outputs unchanged on instruments)")

    def _save_serial_assignment(self):
        self._config["serial_a"] = self.cbo_gen_a.currentData() or ""
        self._config["serial_b"] = self.cbo_gen_b.currentData() or ""
        _save_config(self._config)

    def _refresh_connection_ui(self):
        any_connected = any(v is not None for v in self._gen.values())
        both_connected = all(v is not None for v in self._gen.values())
        self.btn_connect.setText("Disconnect" if any_connected else "Connect")
        self.btn_apply_all.setEnabled(any_connected)

        # Sharing a timebase only makes sense with BOTH units connected.
        self.chk_ext_ref.setEnabled(both_connected)
        if not both_connected and self.chk_ext_ref.isChecked():
            self.chk_ext_ref.blockSignals(True)
            self.chk_ext_ref.setChecked(False)
            self.chk_ext_ref.blockSignals(False)
            self.lbl_ref_status.setText("")

        for gen_letter, lbl in (("A", self.lbl_status_a), ("B", self.lbl_status_b)):
            g = self._gen[gen_letter]
            if g is not None:
                serial = self.cbo_gen_a.currentData() if gen_letter == "A" \
                         else self.cbo_gen_b.currentData()
                lbl.setText(f"● Gen {gen_letter}: connected  [{serial}]")
                lbl.setStyleSheet("color: #1a7a1a; font-weight: bold;")
            else:
                lbl.setText(f"● Gen {gen_letter}: not connected")
                lbl.setStyleSheet("color: #666666; font-weight: bold;")

        for key, panel in self.panels.items():
            gen_letter = key[0]
            panel.set_connected(self._gen[gen_letter] is not None)

        self._set_scpi_enabled(any_connected)

    # ---- Cross-unit timebase (10 MHz reference) ------------------------------

    def _on_ext_ref_toggled(self, checked: bool):
        """Lock/unlock the two units to a shared 10 MHz reference.

        Checked  → Gen A stays INT (master / clock source), Gen B is set to
                   EXT and verify_external_lock() confirms the PLL actually
                   locked.  Gen B is ONLY set to EXT — Gen A is never set EXT
                   here because the [10MHz In/Out] connector is bidirectional:
                   setting both units to EXT with a cable between them makes
                   both drive the connector simultaneously, which damages the
                   instruments.
        Unchecked → both back to their own internal clocks.
        """
        gen_a = self._gen["A"]
        gen_b = self._gen["B"]
        if gen_a is None or gen_b is None:
            self.lbl_ref_status.setText(
                "Both generators must be connected to share a timebase."
            )
            return
        try:
            if checked:
                # Safety guard: Gen A must not already be set to EXT (e.g. via
                # the SCPI console).  If it is, BOTH ends would be driving the
                # 10 MHz line against each other — refuse and explain.
                a_clk = gen_a.get_reference_clock()
                if a_clk == "EXT":
                    msg = (
                        "Gen A is currently set to EXTernal reference.\n\n"
                        "One unit must drive the 10 MHz reference (INT) and the "
                        "other must follow it (EXT).  Setting both to EXT causes "
                        "both instruments to drive the rear-panel [10MHz In/Out] "
                        "connector simultaneously — this will damage the instruments.\n\n"
                        "Return Gen A to its internal clock first (send "
                        ":SYSTem:ROSCillator:SOURce INTernal to Gen A via the "
                        "SCPI console), then enable sharing."
                    )
                    log.warning("_on_ext_ref_toggled: Gen A is EXT — refusing to set Gen B EXT too")
                    self._log_scpi("! Timebase: refused — Gen A is already EXT; both EXT would collide")
                    self.lbl_ref_status.setText(
                        "REFUSED: Gen A is already EXT. Return Gen A to INT first."
                    )
                    self.lbl_ref_status.setStyleSheet(
                        "color: #c0392b; font-style: italic; font-size: 10px;"
                    )
                    self.chk_ext_ref.blockSignals(True)
                    self.chk_ext_ref.setChecked(False)
                    self.chk_ext_ref.blockSignals(False)
                    QMessageBox.warning(self, "Reference clock — both-EXT refused", msg)
                    return

                # Gen A stays INT; set Gen B to EXT and verify the PLL locked.
                gen_a.set_reference_clock("INTernal")
                self._log_scpi(
                    "# Timebase: Gen A → INT (master), verifying Gen B EXT lock "
                    f"(settling {3.0:.1f} s) …"
                )
                QApplication.processEvents()
                locked, actual = gen_b.verify_external_lock(settle_s=3.0)
                if not locked:
                    self._log_scpi(
                        f"! Gen B: EXTernal set but instrument reports {actual!r} — "
                        "check 10 MHz cable and reference level"
                    )
                    self.lbl_ref_status.setText(
                        f"Lock FAILED: Gen B reports {actual}. Check cable and level."
                    )
                    self.lbl_ref_status.setStyleSheet(
                        "color: #c0392b; font-style: italic; font-size: 10px;"
                    )
                    self.chk_ext_ref.blockSignals(True)
                    self.chk_ext_ref.setChecked(False)
                    self.chk_ext_ref.blockSignals(False)
                    QMessageBox.warning(
                        self, "Reference clock lock failed",
                        "Gen B was set to external 10 MHz reference but its "
                        f"readback is {actual!r} — the DG1022Z silently falls "
                        "back to INT when no valid signal is present.\n\n"
                        "Checklist:\n"
                        "  • BNC cable from Gen A [10MHz Out] → Gen B [10MHz In]\n"
                        "  • Reference level must be 250 mVpp – 5 Vpp\n"
                        "  • The [10MHz In/Out] connector is BIDIRECTIONAL — its "
                        "direction is set by the clock source selection.  "
                        "Both units set to INT will each try to drive the connector "
                        "simultaneously, which can damage the instruments."
                    )
                else:
                    self._log_scpi("# Gen B: EXTernal reference confirmed (PLL locked)")
                    self.lbl_ref_status.setText(
                        "Locked: Gen B follows Gen A's 10 MHz reference."
                    )
                    self.lbl_ref_status.setStyleSheet(
                        "color: #555; font-style: italic; font-size: 10px;"
                    )
            else:
                gen_a.set_reference_clock("INTernal")
                gen_b.set_reference_clock("INTernal")
                self._log_scpi("# Timebase: both generators on internal clocks")
                self.lbl_ref_status.setText(
                    "Independent internal clocks — X/Y phase will drift across "
                    "the two units."
                )
                self.lbl_ref_status.setStyleSheet(
                    "color: #555; font-style: italic; font-size: 10px;"
                )
            self._refresh_clock_status()
        except Exception as e:
            log.exception("_on_ext_ref_toggled failed")
            self._log_scpi(f"! Timebase set failed: {e}")
            QMessageBox.warning(self, "Reference clock", str(e))

    def _refresh_clock_status(self):
        """Query each connected unit's active timebase and update the status labels.

        Also refreshes the combined lbl_timebase indicator.  Never displays a
        locked state when the readback returned INT.
        """
        clk: dict[str, str] = {}
        for gen_letter, lbl in (("A", self.lbl_clk_a), ("B", self.lbl_clk_b)):
            g = self._gen[gen_letter]
            if g is None:
                lbl.setText(f"● Gen {gen_letter}: timebase —")
                lbl.setStyleSheet("color: #555; font-size: 10px;")
                clk[gen_letter] = "—"
            else:
                try:
                    src = g.get_reference_clock()
                    lbl.setText(f"● Gen {gen_letter}: timebase {src}")
                    if src == "EXT":
                        lbl.setStyleSheet("color: #1a7a1a; font-size: 10px;")
                    else:
                        lbl.setStyleSheet("color: #555; font-size: 10px;")
                    clk[gen_letter] = src
                except Exception:
                    log.exception("_refresh_clock_status: Gen %s query failed", gen_letter)
                    lbl.setText(f"● Gen {gen_letter}: timebase ?")
                    lbl.setStyleSheet("color: #888; font-size: 10px;")
                    clk[gen_letter] = "?"
        # Combined indicator — green only when A=INT and B=EXT (locked config).
        clk_a = clk.get("A", "—")
        clk_b = clk.get("B", "—")
        self.lbl_timebase.setText(f"Timebase:  A: {clk_a}   B: {clk_b}")
        if clk_a == "INT" and clk_b == "EXT":
            self.lbl_timebase.setStyleSheet(
                "color: #1a7a1a; font-weight: bold; font-size: 10px; padding: 2px;"
            )
        else:
            self.lbl_timebase.setStyleSheet(
                "color: #333; font-weight: bold; font-size: 10px; padding: 2px;"
            )

    # ---- Apply helpers -------------------------------------------------------

    @staticmethod
    def _peak_status(params: dict):
        """Classify a channel's combined-peak interlock result.

        The amplifier input must stay within ±5 V, and the peak it sees is
        |offset| + amp/2 — not either field alone.  Returns ("ok"|"warn"|
        "block", peak_volts).
        """
        peak = channel_peak_volts(params["shape"], params["amp"], params["offset"])
        if peak > PEAK_MAX_VOLTS + 1e-9:
            return "block", peak
        if peak > PEAK_WARN_VOLTS + 1e-9:
            return "warn", peak
        return "ok", peak

    def _configure_channel(self, gen_letter: str, channel: int, params: dict) -> str:
        """Push waveform + load to one channel WITHOUT touching its output gate.

        Returns any clamp-warning string from set_waveform ("" if none).  The
        output on/off is deliberately NOT sent here so callers can enable the
        outputs separately (see _apply_all's synchronized burst).
        """
        g = self._gen[gen_letter]
        warn = g.set_waveform(
            channel,
            params["shape"],
            params["freq"],
            params["amp"],
            params["offset"],
            params["phase"],
        )
        g.set_output_load(channel, params["load"])
        # Phase 4 trace: log the start-phase command so its channel number and
        # value are visible in the SCPI console even without DEBUG logging.
        self._log_scpi(
            f"# {gen_letter}{channel}: start_phase → "
            f":SOURce{channel}:PHASe {params['start_phase']:.1f}"
        )
        g.set_start_phase(channel, params["start_phase"])
        return warn

    def _apply_channel(self, gen_letter: str, channel: int):
        g = self._gen[gen_letter]
        if g is None:
            return
        key    = f"{gen_letter}{channel}"
        panel  = self.panels[key]
        params = panel.get_params()

        # Combined-peak interlock: block outright above 5 V; confirm above 4 V.
        status, peak = self._peak_status(params)
        if status == "block":
            msg = (f"combined peak {peak:.4g} V exceeds the "
                   f"{PEAK_MAX_VOLTS:.0f} V amplifier input limit")
            self._log_scpi(f"! {key}: blocked — {msg}")
            QMessageBox.critical(
                self, "Amplifier limit exceeded",
                f"Channel {key}: |offset| + ½·amplitude = {peak:.4g} V, which "
                f"exceeds the {PEAK_MAX_VOLTS:.0f} V amplifier input ceiling.\n\n"
                f"Even for an AC waveform the peak (offset plus half the "
                f"peak-to-peak swing) must stay ≤ {PEAK_MAX_VOLTS:.0f} V. "
                f"Reduce the offset or the amplitude, then apply again."
            )
            return
        if status == "warn":
            resp = QMessageBox.question(
                self, "High peak voltage",
                f"Channel {key}: combined peak = {peak:.4g} V, above the "
                f"{PEAK_WARN_VOLTS:.0f} V advisory threshold "
                f"(hard ceiling {PEAK_MAX_VOLTS:.0f} V).\n\n"
                f"Apply anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if resp != QMessageBox.StandardButton.Yes:
                self._log_scpi(f"# {key}: apply cancelled at high-peak confirmation")
                return

        try:
            warn = self._configure_channel(gen_letter, channel, params)
            if params["output"]:
                g.output_on(channel)
            else:
                g.output_off(channel)
            if warn:
                self._log_scpi(f"! {key}: {warn}")
                QMessageBox.warning(self, "Safety clamp", f"Channel {key}: {warn}")
            else:
                self._log_scpi(f"# {key}: applied")
        except Exception as e:
            self._log_scpi(f"! {key}: {e}")
            QMessageBox.critical(self, "Apply failed", str(e))

    def _apply_all(self):
        """Apply all four channels so their outputs come up together.

        The old version applied each channel fully — waveform, load AND output —
        one at a time, so the four outputs enabled tens to hundreds of ms apart
        (a whole reconfigure between each), which threw the raster off.

        This does it in three ordered phases instead:
          1. configure every channel (waveform + load + start phase) with
             outputs untouched;
          2. fire every "output ON" back-to-back so the inter-channel skew
             shrinks to just the gap between consecutive enable commands.
             NOTE: :OUTPut ON closes an output relay only — it does NOT start
             or reset the waveform. The DDS phase accumulator is only reset by
             :PHASe:SYNChronize, so align must run AFTER outputs are enabled.
          3. after a short relay-settle delay, run each connected unit's
             Align-Phase (:PHASe:SYNChronize) as the very last operation so
             both channels of each unit restart phase-coherent from the
             start-phases set in step 1.

        NOTE ON CROSS-UNIT SYNC: steps 2–3 lock the channels WITHIN each Rigol
        and start all four close together, but two SEPARATE DG1022Z units drift
        on their independent clocks.  A stable X/Y phase relationship ACROSS the
        two units also needs a shared timebase — the 10 MHz reference cable and
        the "Share 10 MHz timebase" option below.
        """
        # Gather only the channels whose generator is connected.
        active = []   # (key, gen_letter, channel, panel, params)
        for key, panel in self.panels.items():
            gen_letter = key[0]
            channel    = int(key[1])
            if self._gen[gen_letter] is not None:
                active.append((key, gen_letter, channel, panel, panel.get_params()))
        if not active:
            return

        # ── Phase 0: validate the interlock for EVERY channel first ──────────
        # Applying a partial raster is worse than applying none, so if any
        # channel is over the hard ceiling nothing is sent at all.
        blocked, warn_ch = [], []
        for key, _, _, _, params in active:
            status, peak = self._peak_status(params)
            if status == "block":
                blocked.append((key, peak))
            elif status == "warn":
                warn_ch.append((key, peak))
        if blocked:
            lines = "\n".join(f"  {k}: peak {p:.4g} V" for k, p in blocked)
            self._log_scpi("! Apply All blocked — channel(s) over the limit:")
            for k, p in blocked:
                self._log_scpi(f"!   {k}: peak {p:.4g} V")
            QMessageBox.critical(
                self, "Amplifier limit exceeded",
                f"These channels exceed the {PEAK_MAX_VOLTS:.0f} V amplifier "
                f"input ceiling (|offset| + ½·amplitude):\n\n{lines}\n\n"
                f"Nothing was applied. Reduce the offending channels, then "
                f"Apply All again."
            )
            return
        if warn_ch:
            lines = "\n".join(f"  {k}: peak {p:.4g} V" for k, p in warn_ch)
            resp = QMessageBox.question(
                self, "High peak voltage",
                f"These channels are above the {PEAK_WARN_VOLTS:.0f} V advisory "
                f"threshold (hard ceiling {PEAK_MAX_VOLTS:.0f} V):\n\n{lines}\n\n"
                f"Apply all anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if resp != QMessageBox.StandardButton.Yes:
                self._log_scpi("# Apply All cancelled at high-peak confirmation")
                return

        # ── Phase 1: configure every channel (outputs left as they are) ──────
        warnings = []
        try:
            for key, gen_letter, channel, _, params in active:
                w = self._configure_channel(gen_letter, channel, params)
                if w:
                    warnings.append((key, w))
        except Exception as e:
            self._log_scpi(f"! Apply All failed during configure: {e}")
            QMessageBox.critical(self, "Apply All failed", str(e))
            return

        # ── Phase 2: enable outputs — OFF ones first, then all ON together ───
        # :OUTPut ON closes an output relay; it does not start or reset the
        # waveform. The DDS phase accumulator is only reset by
        # :PHASe:SYNChronize, so align must be the LAST operation, once all
        # relays are closed. Keep every "output ON" consecutive so the
        # inter-channel skew shrinks to just the gap between USB-TMC writes.
        try:
            for key, gen_letter, channel, _, params in active:
                if not params["output"]:
                    self._gen[gen_letter].output_off(channel)
            for key, gen_letter, channel, _, params in active:
                if params["output"]:
                    self._gen[gen_letter].output_on(channel)
        except Exception as e:
            self._log_scpi(f"! Apply All failed during output enable: {e}")
            QMessageBox.critical(self, "Apply All failed", str(e))
            return

        # ── Phase 3: align each connected unit's two channels ────────────────
        # Sleep briefly so the output relays have physically settled before
        # resetting the DDS phase accumulators via :PHASe:SYNChronize.
        time.sleep(0.05)
        for gen_letter in ("A", "B"):
            g = self._gen[gen_letter]
            if g is not None:
                try:
                    g.align_phase(1)
                except Exception as e:
                    self._log_scpi(f"! Gen {gen_letter}: align phase failed: {e}")

        if warnings:
            for key, w in warnings:
                self._log_scpi(f"! {key}: {w}")
            QMessageBox.warning(
                self, "Safety clamp",
                "\n".join(f"{key}: {w}" for key, w in warnings)
            )
        self._log_scpi(
            f"# Apply All: configured {len(active)} channel(s), "
            f"enabled outputs, aligned phase"
        )
        self._refresh_clock_status()

    # ---- Poll (read-only) ----------------------------------------------------

    def _poll_readback(self):
        for key, panel in self.panels.items():
            gen_letter = key[0]
            channel    = int(key[1])
            g = self._gen[gen_letter]
            if g is None:
                continue
            try:
                state = g.get_state(channel)
                panel.update_readback(state)
            except Exception as e:
                panel.update_readback({"error": str(e)})

    # ---- SCPI console --------------------------------------------------------

    def _scpi_target(self) -> DG1022Z | None:
        target = self.cbo_scpi_target.currentText()
        gen_letter = "A" if "A" in target else "B"
        return self._gen[gen_letter]

    def _scpi_send(self):
        g = self._scpi_target()
        if g is None:
            self._log_scpi("! Not connected")
            return
        cmd = self.le_scpi_cmd.text().strip()
        if not cmd:
            return
        try:
            g.write(cmd)
            self._log_scpi(f"> {cmd}")
        except Exception as e:
            self._log_scpi(f"! {e}")

    def _scpi_query(self):
        g = self._scpi_target()
        if g is None:
            self._log_scpi("! Not connected")
            return
        cmd = self.le_scpi_cmd.text().strip()
        if not cmd:
            return
        try:
            resp = g.query(cmd)
            self._log_scpi(f"> {cmd}")
            self._log_scpi(f"< {resp}")
        except Exception as e:
            self._log_scpi(f"! {e}")

    def _scpi_read_errors(self):
        g = self._scpi_target()
        if g is None:
            self._log_scpi("! Not connected")
            return
        self._log_scpi("# Reading error queue…")
        seen = set()
        for _ in range(20):   # max 20 errors before giving up
            try:
                err = g.get_error()
                if err in seen:
                    break
                seen.add(err)
                self._log_scpi(f"  ERR: {err}")
                if err.startswith("0") or "No error" in err:
                    break
            except Exception as e:
                self._log_scpi(f"! {e}")
                break

    def _set_scpi_enabled(self, on: bool):
        for w in (self.le_scpi_cmd, self.btn_scpi_send,
                  self.btn_scpi_query, self.btn_scpi_err):
            w.setEnabled(on)

    def _log_scpi(self, line: str):
        ts = time.strftime("%H:%M:%S")
        self.scpi_log.append(f"[{ts}] {line}")
        if line.startswith("!"):
            log.error("SCPI %s", line)
        else:
            log.info("SCPI %s", line)

    # ---- Owner-callable cleanup ---------------------------------------------

    def close_session(self):
        """Called by MainWindow.closeEvent.

        Closes VISA sessions only.  Does NOT disable outputs or send *RST.
        The instruments retain their output state after the app exits.
        """
        self._poll_timer.stop()
        for letter in ("A", "B"):
            if self._gen[letter] is not None:
                try:
                    self._gen[letter].close()
                except Exception:
                    pass
                self._gen[letter] = None


# ---- Standalone smoke test ---------------------------------------------------

if __name__ == "__main__":
    # Use offscreen platform when no display is available (CI / headless).
    # Must be set before QApplication is created (Qt reads it at startup).
    if "DISPLAY" not in os.environ and "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    w = FuncGenTab()
    # Print [OK] right after construction so headless runs capture it.
    print("[OK] funcgen_tab: constructed")
    w.resize(1000, 800)
    w.show()
    sys.exit(app.exec())
