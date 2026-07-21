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
import os
import sys
import time
from pathlib import Path

# When run as a script (python funcgen_tab.py), __package__ is None and
# Python sets sys.path[0] to this file's directory.  Add the project root
# so the rbl namespace package is importable.
if __package__ is None:
    _here = os.path.dirname(os.path.abspath(__file__))
    _root = os.path.abspath(os.path.join(_here, "..", ".."))
    if _root not in sys.path:
        sys.path.insert(0, _root)

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QGroupBox, QLabel, QPushButton, QDoubleSpinBox, QComboBox,
    QTextEdit, QLineEdit, QCheckBox, QMessageBox, QSizePolicy,
    QScrollArea,
)

from rbl.hardware.funcgen_driver import DG1022Z, discover, MAX_GEN_VOLTS

# Persistence file — keyed on serial, survives replug
_CONFIG_PATH = Path.home() / ".config" / "rbl" / "funcgen.json"

# EEL5000 gain: 1 V_gen -> 1000 V_plate
_AMP_GAIN = 1000.0


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

    def __init__(self, label: str, parent=None):
        super().__init__(label, parent)
        self._label = label
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(6, 6, 6, 6)

        form = QFormLayout()
        form.setSpacing(3)

        # Shape
        self.cbo_shape = QComboBox()
        self.cbo_shape.addItems(self.SHAPES)
        form.addRow("Shape:", self.cbo_shape)

        # Frequency
        self.spn_freq = QDoubleSpinBox()
        self.spn_freq.setRange(0.0001, 25_000_000.0)
        self.spn_freq.setValue(1000.0)
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

        # Amplitude
        self.spn_amp = QDoubleSpinBox()
        self.spn_amp.setRange(0.0, MAX_GEN_VOLTS)
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

        # Phase
        self.spn_phase = QDoubleSpinBox()
        self.spn_phase.setRange(-360.0, 360.0)
        self.spn_phase.setValue(0.0)
        self.spn_phase.setDecimals(2)
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
            "font-family: Menlo, monospace; font-size: 10px; color: #444;"
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
        shape = self.cbo_shape.currentText()
        if shape == "DC":
            v = self.spn_offset.value()
        else:
            v = self.spn_amp.value()
        kv_per_plate = abs(v) * _AMP_GAIN / 1000.0
        kv_p2p       = 2.0 * kv_per_plate
        self.lbl_hv.setText(
            f"→ {kv_per_plate:.4f} kV/plate  ({kv_p2p:.4f} kV p-p)"
        )

    def set_connected(self, on: bool):
        for w in (self.btn_apply, self.btn_output, self.spn_freq,
                  self.spn_amp, self.spn_offset, self.spn_phase,
                  self.le_load, self.cbo_shape):
            w.setEnabled(on)

    def get_params(self) -> dict:
        return {
            "shape":    self.cbo_shape.currentText(),
            "freq":     self.spn_freq.value(),
            "amp":      self.spn_amp.value(),
            "offset":   self.spn_offset.value(),
            "phase":    self.spn_phase.value(),
            "load":     self.le_load.text().strip() or "INFinity",
            "output":   self.btn_output.isChecked(),
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

        left_layout.addWidget(disc_box)

        # ── 4 channel panels in 2×2 grid ─────────────────────────────────
        grid = QGridLayout()
        grid.setSpacing(8)
        self.panels: dict[str, ChannelPanel] = {}
        panel_labels = {
            "A1": "Gen A — Ch 1",
            "A2": "Gen A — Ch 2",
            "B1": "Gen B — Ch 1",
            "B2": "Gen B — Ch 2",
        }
        positions = {"A1": (0, 0), "A2": (0, 1), "B1": (1, 0), "B2": (1, 1)}
        for key, title in panel_labels.items():
            p = ChannelPanel(title, self)
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
        self.scpi_log.setFont(QFont("Menlo", 9))
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
        self.btn_connect.setText("Disconnect" if any_connected else "Connect")
        self.btn_apply_all.setEnabled(any_connected)

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

    # ---- Apply helpers -------------------------------------------------------

    def _apply_channel(self, gen_letter: str, channel: int):
        g = self._gen[gen_letter]
        if g is None:
            return
        key    = f"{gen_letter}{channel}"
        panel  = self.panels[key]
        params = panel.get_params()
        try:
            warn = g.set_waveform(
                channel,
                params["shape"],
                params["freq"],
                params["amp"],
                params["offset"],
                params["phase"],
            )
            g.set_output_load(channel, params["load"])
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
        for key, panel in self.panels.items():
            gen_letter = key[0]
            channel    = int(key[1])
            if self._gen[gen_letter] is not None:
                self._apply_channel(gen_letter, channel)

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
