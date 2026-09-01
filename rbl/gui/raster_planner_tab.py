"""
raster_planner_tab.py
PySide6 widget for the "Raster Planner" outer tab — the research deliverable.
Computes the drive each axis needs for uniform dwell over a RECTANGULAR
sample given a measured beam FWHM, shows what every other species would do
at that same commanded voltage, and shows the amplifier envelope so the
operator can see whether the requested point is inside it.

Pure calculator: no hardware, no live run. Every number here comes from
rbl.hardware.raster_model / rbl.hardware.load_model, both pure math, so this
tab is just inputs -> those functions -> plots.

WHERE THE DEFAULTS LIVE
-----------------------
| What                     | Lives in                         |
|--------------------------|----------------------------------|
| Species table rows       | rbl/config/beam_species.py       |
| Steerer geometry         | rbl/config/steerer_geometry.py   |
| Calibration limits       | rbl/config/calibration_config.py |
| FWHM, sample, freq, etc. | rbl/config/raster_defaults.py    |
| Runtime species list     | ~/.config/rbl/funcgen.json       |

WHY X AND Y ARE COMPUTED SEPARATELY
-----------------------------------
Samples are rectangles. A single "sample half-width" and a single required
voltage silently assumed a square, and the fast and slow axes do not even
run at the same frequency, so one number could not have been right for both
in the first place. Each axis now gets its own half-size input, its own
required plate-to-plate voltage, and its own current prediction.

WHY THE SPECIES TABLE IS THERE
------------------------------
The deflection formula depends on q/E, so the SAME commanded voltage sweeps
a 3 MeV proton and a 3 MeV Ni(3+) by very different distances. Setting the
drive up for one species and then changing beam is the practical way to
overrun a sample or under-fill it, and no single-species readout can show
that. So the table takes the lab deflection sheet's shape directly: type in
mass, energy and charge state, read the millimetres each one gets at the
voltage currently commanded.

WHY FULL WIDTH IN, AND AN OFFSET FOR ASYMMETRY
----------------------------------------------
The sample inputs are the FULL width and FULL height, because that is what
a pair of calipers reads; the halving happens inside
`raster_model.required_drive`. Asymmetric rastering - covering one side of
the beam axis more than the other - is asked for as a centre OFFSET in
millimetres, not as two unequal amplitudes, because unequal amplitudes do
not produce it: push-pull plates at +v(t) and -v(t) give a plate-to-plate
waveform of (a1 + a2)*s(t), which has no DC term however you split a1 and
a2, so the sweep stays centred on the beam axis and only changes width.
What moves the centre is a differential DC term (+O/2 on one plate, -O/2 on
the other), and that is what the offset is turned into. It defaults to zero.

WHY FREQUENCY IS NOT IN THE SIZING BLOCK
----------------------------------------
theta = V*l*q/(2*d*E) has no frequency in it. The same sample needs the same
voltage at 6 Hz and at 600 Hz. Frequency decides only what the amplifier has
to source (I = k*f*C*V) and therefore where the operating point sits against
the envelope, so it sits with the amplifier inputs and says so.

WHY ALL FOUR CHANNELS' CAPACITANCE, NOT ONE
-------------------------------------------
This tab used to ask which channel's measured capacitance to use and then
apply it everywhere. The four channels are four separate physical loads —
that is the entire reason `load_calibration_store` is keyed per channel —
and the current prediction is I = k*f*C*V, linear in C, so using X+'s
capacitance for Y- is simply predicting the wrong current for Y-. Each
channel is now predicted from its own measurement, and the envelope check
reports the channel with the least headroom rather than an average that
belongs to nobody.

WHY THE ENVELOPE USES MEASURED CAPACITANCE WHEN AVAILABLE
---------------------------------------------------------
An envelope drawn from the wrong C is worse than no envelope — a 1200 pF
guess and a channel's real 1050 pF measurement move the current wall by
over 10%, enough to make a planned operating point look safely inside the
envelope when it is not. Which capacitance each channel used is displayed,
never assumed.
"""
import math

import numpy as np
import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
    QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView,
)

from rbl.config.beam_species import DEFAULT_SPECIES, DEFAULT_SPECIES_ROW
from rbl.config.calibration_config import (
    CAL_LOAD_CAP_PF, CAL_AC_TRIP_MA, CAL_MAX_KV, ac_peak_current_ma,
)
from rbl.config.hardware_config import AMP_LABELS
from rbl.config.load_calibration_store import capacitance_pf_for
from rbl.config.persistence import load_config, save_config
from rbl.config.raster_defaults import (
    FWHM_MM_DEFAULT, SAMPLE_WIDTH_X_MM, SAMPLE_HEIGHT_Y_MM,
    OFFSET_X_MM_DEFAULT, OFFSET_Y_MM_DEFAULT, TURNAROUND_K_DEFAULT,
    FREQ_X_HZ_DEFAULT, FREQ_Y_HZ_DEFAULT, AMP_MAX_BANDWIDTH_HZ,
)
from rbl.config.steerer_geometry import (
    DRIFT_TO_SAMPLE_CM, PLATE_GAP_CM, PLATE_LENGTH_CM, PLATE_RATING_KV,
    describe as steerer_description,
)
from rbl.gui import theme
from rbl.gui.widgets.inputs import QuietDoubleSpinBox
from rbl.hardware.load_model import envelope_walls
from rbl.hardware.raster_model import (
    required_drive, dwell_uniformity, displacement_mm,
)

_SPECIES_CFG_KEY = "raster_species"

# Which amplifier drives which axis.  X+ and X- are the push-pull pair on the
# X plates and both see the X-axis frequency; likewise Y.
AXIS_OF_CHANNEL = {"X+": "X", "X-": "X", "Y+": "Y", "Y-": "Y"}

SPECIES_COLUMNS = ["Species", "Mass [amu]", "Energy [MeV]", "q",
                   "X deflection [mm]", "Y deflection [mm]"]
N_EDITABLE_COLS = 4


def _spin(minimum, maximum, value, decimals=3, step=0.1):
    """A spin box with NO unit suffix - the unit belongs in the form label.

    House rule, written down in rbl/gui/widgets/inputs.py: a suffix is part
    of the editable text, so the cursor can land behind it and a
    select-all-and-retype takes it with it.
    """
    sb = QuietDoubleSpinBox()
    sb.setRange(minimum, maximum)
    sb.setDecimals(decimals)
    sb.setSingleStep(step)
    sb.setValue(value)
    return sb


class RasterPlannerTab(QWidget):
    """Calculator-only tab: no beamline hardware access, no live stream."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._updating = False       # guards the species table's itemChanged

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        # ── Inputs ────────────────────────────────────────────────────────
        in_box = QGroupBox("Raster Parameters")
        form = QFormLayout(in_box)

        # The steerer is not a choice.  There is one on this beamline, its
        # part number is stamped on the tube, and its plate geometry is a fact
        # (see steerer_geometry.py).  A picker here could only ever sit on the
        # right answer or be left on the wrong one, and a wrong gap silently
        # scales every number this tab produces.  So it is displayed, not
        # selected.
        self.lbl_steerer = QLabel(steerer_description())
        self.lbl_steerer.setWordWrap(True)
        form.addRow("Steerer:", self.lbl_steerer)

        # Opens on the real beamline length (steerer exit to sample, from the
        # lab deflection sheet) rather than a round 100 cm, but stays editable
        # - the sample does not have to sit at the nominal position.
        self.sb_drift_cm = _spin(0.1, 1000.0, DRIFT_TO_SAMPLE_CM,
                                 decimals=1, step=1.0)
        form.addRow("Drift, steerer exit -> sample [cm]:", self.sb_drift_cm)

        self.sb_fwhm_mm = _spin(0.001, 100.0, FWHM_MM_DEFAULT, decimals=3, step=0.1)
        form.addRow("Beam FWHM [mm]:", self.sb_fwhm_mm)

        # Rectangular sample: the two sizes are independent.  FULL width and
        # FULL height, because that is what comes off a pair of calipers -
        # the halving is the math's business, not the operator's, and is
        # done inside raster_model.required_drive.
        self.sb_width_x_mm = _spin(0.02, 1000.0, SAMPLE_WIDTH_X_MM, decimals=2, step=0.5)
        form.addRow("Sample WIDTH  X (full) [mm]:", self.sb_width_x_mm)

        self.sb_height_y_mm = _spin(0.02, 1000.0, SAMPLE_HEIGHT_Y_MM, decimals=2, step=0.5)
        form.addRow("Sample HEIGHT Y (full) [mm]:", self.sb_height_y_mm)

        # Asymmetric rastering.  Zero is the default and the normal case;
        # see the module docstring for why this is an offset and not two
        # unequal amplitudes.
        self.sb_offset_x_mm = _spin(-500.0, 500.0, OFFSET_X_MM_DEFAULT, decimals=2, step=0.5)
        form.addRow("Scan centre offset X [mm]:", self.sb_offset_x_mm)

        self.sb_offset_y_mm = _spin(-500.0, 500.0, OFFSET_Y_MM_DEFAULT, decimals=2, step=0.5)
        form.addRow("Scan centre offset Y [mm]:", self.sb_offset_y_mm)

        self.sb_turnaround_k = _spin(0.0, 10.0, TURNAROUND_K_DEFAULT, decimals=2, step=0.1)
        form.addRow("Turnaround margin (k x FWHM):", self.sb_turnaround_k)

        top_row.addWidget(in_box, stretch=0)

        # ── Amplifier / envelope inputs ───────────────────────────────────
        # Frequency lives HERE, not with the sizing inputs, because it does
        # not appear anywhere in the geometry: theta = V*l*q/(2*d*E) has no
        # f in it, so the swept width and the voltage that produces it are
        # the same at 6 Hz and 600 Hz.  What frequency decides is what the
        # amplifier has to source (I = k*f*C*V) and therefore where the
        # operating point sits against the envelope.  Sitting in the sizing
        # block, it read like an input to the width.
        amp_box = QGroupBox("Amplifier Drive (does not affect the width)")
        amp_form = QFormLayout(amp_box)

        self.sb_freq_x_hz = _spin(0.1, 10_000.0, FREQ_X_HZ_DEFAULT, decimals=1, step=1.0)
        amp_form.addRow("X-axis (fast) frequency [Hz]:", self.sb_freq_x_hz)

        self.sb_freq_y_hz = _spin(0.1, 10_000.0, FREQ_Y_HZ_DEFAULT, decimals=1, step=1.0)
        amp_form.addRow("Y-axis (slow) frequency [Hz]:", self.sb_freq_y_hz)

        freq_note = QLabel(
            "Frequency sets the current draw (I = k·f·C·V) and where the "
            "operating point sits on the envelope. It does not change the "
            "required voltage or the swept width.")
        freq_note.setWordWrap(True)
        freq_note.setStyleSheet(
            f"font-size: {theme.FS_CAPTION}px; color: {theme.MUTED};")
        amp_form.addRow(freq_note)

        # Measured capacitance is re-read on demand and whenever this tab is
        # shown - a characterisation run finishing on another tab used to
        # leave this one displaying whatever it read at construction.
        self.btn_refresh_caps = QPushButton("Re-read measured capacitance")
        self.btn_refresh_caps.setToolTip(
            "Re-read ~/.config/rbl/load_calibration.json.\n\n"
            "Happens automatically when this tab is shown and when a Load "
            "Characterization run finishes; this is for when you want to be "
            "sure.")
        self.btn_refresh_caps.clicked.connect(self.refresh_capacitance)
        amp_form.addRow(self.btn_refresh_caps)

        top_row.addWidget(amp_box, stretch=0)

        for sb in (self.sb_drift_cm, self.sb_fwhm_mm, self.sb_width_x_mm,
                   self.sb_height_y_mm, self.sb_offset_x_mm,
                   self.sb_offset_y_mm, self.sb_turnaround_k,
                   self.sb_freq_x_hz, self.sb_freq_y_hz):
            sb.valueChanged.connect(self._recompute)

        # ── Results ───────────────────────────────────────────────────────
        out_box = QGroupBox("Required Drive")
        out_form = QFormLayout(out_box)

        self.lbl_design_species = QLabel("—")
        self.lbl_design_species.setStyleSheet(f"font-size: {theme.FS_LABEL}px;")
        out_form.addRow("Designed for:", self.lbl_design_species)

        self.lbl_kv_x = QLabel("—")
        self.lbl_kv_x.setStyleSheet(f"font-weight: bold; font-size: {theme.FS_BIG}px;")
        out_form.addRow("X plate-to-plate (differential) kV:", self.lbl_kv_x)

        self.lbl_plate_kv_x = QLabel("—")
        out_form.addRow("X per-plate kV (push-pull, /2):", self.lbl_plate_kv_x)

        self.lbl_sweep_x = QLabel("—")
        self.lbl_sweep_x.setWordWrap(True)
        self.lbl_sweep_x.setStyleSheet(f"font-size: {theme.FS_CAPTION}px;")
        out_form.addRow("X sweep on sample:", self.lbl_sweep_x)

        self.lbl_kv_y = QLabel("—")
        self.lbl_kv_y.setStyleSheet(f"font-weight: bold; font-size: {theme.FS_BIG}px;")
        out_form.addRow("Y plate-to-plate (differential) kV:", self.lbl_kv_y)

        self.lbl_plate_kv_y = QLabel("—")
        out_form.addRow("Y per-plate kV (push-pull, /2):", self.lbl_plate_kv_y)

        self.lbl_sweep_y = QLabel("—")
        self.lbl_sweep_y.setWordWrap(True)
        self.lbl_sweep_y.setStyleSheet(f"font-size: {theme.FS_CAPTION}px;")
        out_form.addRow("Y sweep on sample:", self.lbl_sweep_y)

        self.lbl_c_source = QLabel("—")
        self.lbl_c_source.setWordWrap(True)
        out_form.addRow("Load capacitance, per channel:", self.lbl_c_source)

        self.lbl_currents = QLabel("—")
        self.lbl_currents.setWordWrap(True)
        out_form.addRow("Predicted peak current:", self.lbl_currents)

        self.lbl_envelope = QLabel("—")
        self.lbl_envelope.setWordWrap(True)
        out_form.addRow("Envelope check (worst channel):", self.lbl_envelope)

        self.lbl_uniformity = QLabel("—")
        self.lbl_uniformity.setWordWrap(True)
        out_form.addRow("Dwell uniformity (on sample):", self.lbl_uniformity)

        top_row.addWidget(out_box, stretch=1)
        layout.addLayout(top_row, stretch=0)

        # ── Plots + species table (side by side) ──────────────────────────
        plot_row = QHBoxLayout()
        plot_row.setSpacing(8)

        env_box = QGroupBox("Operating Envelope")
        env_lay = QVBoxLayout(env_box)
        self._fig_env = Figure(figsize=(5, 4))
        self._canvas_env = FigureCanvasQTAgg(self._fig_env)
        self._canvas_env.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._ax_env = self._fig_env.add_subplot(111)
        env_lay.addWidget(self._canvas_env)
        plot_row.addWidget(env_box, stretch=1)

        # ── Species table ─────────────────────────────────────────────────
        sp_box = QGroupBox("Deflection for Various Species (at the commanded voltage)")
        sp_lay = QVBoxLayout(sp_box)

        self.lbl_target = QLabel("—")
        self.lbl_target.setStyleSheet(f"font-size: {theme.FS_CAPTION}px;")
        sp_lay.addWidget(self.lbl_target)

        self.tbl_species = QTableWidget(0, len(SPECIES_COLUMNS))
        self.tbl_species.setHorizontalHeaderLabels(SPECIES_COLUMNS)
        self.tbl_species.verticalHeader().setVisible(False)
        self.tbl_species.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_species.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tbl_species.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.tbl_species.setMinimumHeight(150)
        self._populate_species(self._load_species())
        self.tbl_species.itemChanged.connect(self._on_species_edited)
        self.tbl_species.itemSelectionChanged.connect(self._recompute)
        sp_lay.addWidget(self.tbl_species)

        btn_row = QHBoxLayout()
        self._btn_add = QPushButton("Add Row")
        self._btn_remove = QPushButton("Remove Row")
        self._btn_reset = QPushButton("Reset to Defaults")
        self._btn_add.clicked.connect(self._add_species_row)
        self._btn_remove.clicked.connect(self._remove_species_row)
        self._btn_reset.clicked.connect(self._reset_species)
        btn_row.addWidget(self._btn_add)
        btn_row.addWidget(self._btn_remove)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_reset)
        sp_lay.addLayout(btn_row)

        hint = QLabel("Select a row to design the drive for that species. "
                      "Mass, energy and charge state are editable; deflection "
                      "is what that species would sweep at the voltage "
                      "commanded above.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"font-size: {theme.FS_CAPTION}px; color: {theme.MUTED};")
        sp_lay.addWidget(hint)

        plot_row.addWidget(sp_box, stretch=1)

        layout.addLayout(plot_row, stretch=1)

        default_row = min(DEFAULT_SPECIES_ROW, self.tbl_species.rowCount() - 1)
        if default_row >= 0:
            self.tbl_species.selectRow(default_row)
        self._recompute()

    # ------------------------------------------------------------------
    # Species table
    # ------------------------------------------------------------------

    def _populate_species(self, rows):
        self._updating = True
        try:
            self.tbl_species.setRowCount(len(rows))
            for row, (name, mass, energy, q) in enumerate(rows):
                for col, value in enumerate((name, f"{mass:g}", f"{energy:g}", f"{q:d}")):
                    item = QTableWidgetItem(value)
                    if col > 0:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                              | Qt.AlignmentFlag.AlignVCenter)
                    self.tbl_species.setItem(row, col, item)
                for col in range(N_EDITABLE_COLS, len(SPECIES_COLUMNS)):
                    item = QTableWidgetItem("—")
                    # Computed: shown, never typed into.
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                    self.tbl_species.setItem(row, col, item)
        finally:
            self._updating = False

    def _on_species_edited(self, item):
        if self._updating or item.column() >= N_EDITABLE_COLS:
            return
        self._save_species()
        self._recompute()

    def _species_row(self, row: int):
        """(name, mass, energy_ev, charge_state) or None if the row is unusable.

        A half-typed cell is a normal transient state while someone is
        editing, not an error worth a dialog — the row just stops
        contributing until it parses again.
        """
        try:
            name = self.tbl_species.item(row, 0).text().strip()
            mass = float(self.tbl_species.item(row, 1).text())
            energy_mev = float(self.tbl_species.item(row, 2).text())
            charge = int(float(self.tbl_species.item(row, 3).text()))
        except (AttributeError, ValueError):
            return None
        if energy_mev <= 0 or charge < 1 or mass <= 0:
            return None
        return name, mass, energy_mev * 1e6, charge

    def _selected_species(self):
        rows = self.tbl_species.selectionModel().selectedRows()
        row = rows[0].row() if rows else DEFAULT_SPECIES_ROW
        return row, self._species_row(row)

    def _load_species(self):
        data = load_config().get(_SPECIES_CFG_KEY)
        if isinstance(data, list) and data:
            try:
                rows = [(str(r["name"]), float(r["mass"]),
                         float(r["energy"]), int(float(r["q"])))
                        for r in data]
                if rows:
                    return rows
            except (KeyError, TypeError, ValueError):
                pass
        return list(DEFAULT_SPECIES)

    def _save_species(self):
        rows = []
        for row in range(self.tbl_species.rowCount()):
            parsed = self._species_row(row)
            if parsed is not None:
                name, mass, energy_ev, q = parsed
                rows.append({"name": name, "mass": mass,
                              "energy": energy_ev / 1e6, "q": q})
            else:
                def _cell(c):
                    it = self.tbl_species.item(row, c)
                    return it.text() if it else ""
                rows.append({"name": _cell(0), "mass": _cell(1),
                              "energy": _cell(2), "q": _cell(3)})
        cfg = load_config()
        cfg[_SPECIES_CFG_KEY] = rows
        save_config(cfg)

    def _add_species_row(self):
        self._updating = True
        try:
            r = self.tbl_species.rowCount()
            self.tbl_species.insertRow(r)
            for col, value in enumerate(("New", "1", "1.0", "1")):
                item = QTableWidgetItem(value)
                if col > 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                self.tbl_species.setItem(r, col, item)
            for col in range(N_EDITABLE_COLS, len(SPECIES_COLUMNS)):
                item = QTableWidgetItem("—")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
                self.tbl_species.setItem(r, col, item)
        finally:
            self._updating = False
        self._save_species()
        self._recompute()

    def _remove_species_row(self):
        rows = self.tbl_species.selectionModel().selectedRows()
        if not rows:
            return
        self._updating = True
        try:
            self.tbl_species.removeRow(rows[0].row())
        finally:
            self._updating = False
        self._save_species()
        self._recompute()

    def _reset_species(self):
        self._populate_species(list(DEFAULT_SPECIES))
        self._save_species()
        self.tbl_species.selectRow(DEFAULT_SPECIES_ROW)
        self._recompute()

    # ------------------------------------------------------------------
    # Capacitance
    # ------------------------------------------------------------------

    def _channel_capacitance(self) -> dict:
        """{amp_label: (c_pf, source_str)} for all four channels.

        Each channel is its own load; see the module docstring. A channel
        that has never been characterised falls back to CAL_LOAD_CAP_PF and
        SAYS SO, because a guessed capacitance sitting silently next to
        three measured ones is how a prediction gets trusted more than it
        has earned.

        ON_PLATES WINS
        --------------
        A channel can hold both conditions.  ON_PLATES is the load the
        amplifier actually drives during a run; DISCONNECTED is the
        amplifier and its internal network alone, and is smaller by exactly
        the thing being planned for.  `capacitance_pf_for` with no condition
        returns whichever was measured most RECENTLY, which would mean a
        DISCONNECTED sweep run after an ON_PLATES one silently became the
        number this tab plans from.  So the condition is asked for by name,
        and displayed.
        """
        out = {}
        for label in AMP_LABELS:
            for condition in ("ON_PLATES", "DISCONNECTED"):
                measured = capacitance_pf_for(label, condition)
                if measured is not None:
                    out[label] = (measured, "measured"
                                  if condition == "ON_PLATES"
                                  else "measured, DISCONNECTED")
                    break
            else:
                out[label] = (CAL_LOAD_CAP_PF, "fallback")
        return out

    def refresh_capacitance(self):
        """Re-read the calibration store and recompute.

        Called when this tab is shown, when a Load Characterization run
        finishes (wired in app.py), and from the button.  Before this
        existed the tab read the store once, at construction, so a
        characterisation run finishing while the app was open changed
        nothing here until the next restart - the four channels kept
        showing "fallback" beside a table that had just measured them.
        """
        self._recompute()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_capacitance()

    # ------------------------------------------------------------------

    def _recompute(self):
        try:
            self._do_recompute()
        except Exception as e:
            self.lbl_envelope.setText(f"Error: {e}")

    def _do_recompute(self):
        plate_length_cm = PLATE_LENGTH_CM
        plate_gap_cm    = PLATE_GAP_CM
        drift_cm     = self.sb_drift_cm.value()
        fwhm_mm      = self.sb_fwhm_mm.value()
        width_x_mm   = self.sb_width_x_mm.value()
        height_y_mm  = self.sb_height_y_mm.value()
        offset_x_mm  = self.sb_offset_x_mm.value()
        offset_y_mm  = self.sb_offset_y_mm.value()
        turnaround_k = self.sb_turnaround_k.value()
        f_x = self.sb_freq_x_hz.value()
        f_y = self.sb_freq_y_hz.value()

        sel_row, species = self._selected_species()
        if species is None:
            self.lbl_design_species.setText("selected row is not a valid species")
            return
        name, _mass, beam_energy_ev, charge_state = species
        self.lbl_design_species.setText(
            f"{name} — {beam_energy_ev / 1e6:g} MeV, charge state {charge_state}+")

        # --- Required drive, per axis --------------------------------------
        def _drive(width_mm, offset_mm):
            return required_drive(
                target_width_mm=width_mm, fwhm_mm=fwhm_mm,
                plate_length_cm=plate_length_cm, plate_gap_cm=plate_gap_cm,
                charge_state=charge_state, beam_energy_ev=beam_energy_ev,
                drift_cm=drift_cm, turnaround_k=turnaround_k,
                center_offset_mm=offset_mm,
            )

        drive = {"X": _drive(width_x_mm, offset_x_mm),
                 "Y": _drive(height_y_mm, offset_y_mm)}
        self._drive = drive

        kv_x = drive["X"]["amplitude_kv"]
        kv_y = drive["Y"]["amplitude_kv"]

        # The AC half-amplitude one plate swings.  This is what the current
        # prediction and the envelope use; the DC offset does not draw
        # I = k*f*C*V, it just sits there.
        plate_kv = {"X": drive["X"]["ac_plate_kv"], "Y": drive["Y"]["ac_plate_kv"]}

        self.lbl_kv_x.setText("—" if math.isnan(kv_x) else f"{kv_x:.4f} kV differential")
        self.lbl_kv_y.setText("—" if math.isnan(kv_y) else f"{kv_y:.4f} kV differential")
        self._set_plate_kv(self.lbl_plate_kv_x, drive["X"])
        self._set_plate_kv(self.lbl_plate_kv_y, drive["Y"])
        self._set_sweep(self.lbl_sweep_x, drive["X"], width_x_mm, offset_x_mm)
        self._set_sweep(self.lbl_sweep_y, drive["Y"], height_y_mm, offset_y_mm)

        # --- Per-channel capacitance and current ---------------------------
        caps = self._channel_capacitance()
        self.lbl_c_source.setText("   ".join(
            f"{label} {c_pf:.0f} pF ({src})" for label, (c_pf, src) in caps.items()))
        n_fallback = sum(1 for _c, src in caps.values() if src == "fallback")
        self.lbl_c_source.setStyleSheet(
            theme.status_label(theme.OK if n_fallback == 0 else theme.WARN, bold=False))

        freq_of_axis = {"X": f_x, "Y": f_y}
        currents = {}
        for label, (c_pf, _src) in caps.items():
            axis = AXIS_OF_CHANNEL[label]
            currents[label] = ac_peak_current_ma(freq_of_axis[axis], plate_kv[axis],
                                                  load_pf=c_pf)
        self.lbl_currents.setText("   ".join(
            f"{label} {currents[label]:.3f} mA" for label in AMP_LABELS))

        # --- Envelope, per channel, reported at the worst one ---------------
        self._check_envelope(caps, plate_kv, freq_of_axis)

        # --- Dwell uniformity, per axis ------------------------------------
        # Computed in the SCAN's own frame.  A centre offset moves the scan
        # and the sample together, so the dose profile across the sample is
        # the same shape wherever the pair sits - the offset cancels out of
        # this number and is deliberately not passed in.
        half_x_mm   = drive["X"]["sample_half_mm"]
        half_y_mm   = drive["Y"]["sample_half_mm"]
        scan_half_x = drive["X"]["scan_half_span_mm"]
        scan_half_y = drive["Y"]["scan_half_span_mm"]
        du_x = dwell_uniformity(fwhm_mm=fwhm_mm, scan_half_width_mm=scan_half_x,
                                 sample_half_width_mm=half_x_mm)
        du_y = dwell_uniformity(fwhm_mm=fwhm_mm, scan_half_width_mm=scan_half_y,
                                 sample_half_width_mm=half_y_mm)

        def _pct(du):
            v = du["uniformity_pct"]
            return "—" if math.isnan(v) else f"{v:.3f}%"

        self.lbl_uniformity.setText(
            f"X {_pct(du_x)} peak-to-peak     Y {_pct(du_y)} peak-to-peak")

        # --- Species table -------------------------------------------------
        offset_note = ""
        if abs(offset_x_mm) > 1e-9 or abs(offset_y_mm) > 1e-9:
            offset_note = (f"   ·   centred at X {offset_x_mm:+.3f} mm, "
                           f"Y {offset_y_mm:+.3f} mm (deflections below are "
                           f"the half-span about that centre)")
        self.lbl_target.setText(
            f"Target half-span (sample/2 + {turnaround_k:g} x FWHM):  "
            f"X ±{scan_half_x:.3f} mm    Y ±{scan_half_y:.3f} mm{offset_note}")
        self._fill_species_deflections(kv_x, kv_y, plate_length_cm, plate_gap_cm,
                                        drift_cm, scan_half_x, scan_half_y, sel_row)

    def _set_plate_kv(self, label, drive):
        """Per-plate voltage, checked against the rating at its WORST instant.

        The manufacturer's rating is per plate with respect to the
        enclosure, so it is the plate voltage - not the differential - that
        has to clear it.  With a centre offset the worst instant is
        |offset|/2 + amplitude/2, not amplitude/2: the DC term and the peak
        of the sweep add on the plate that is pushed outward, and checking
        the AC half alone would clear a drive that is actually over the
        rating on one side.
        """
        peak_plate_kv = drive["peak_plate_kv"]
        ac_plate_kv   = drive["ac_plate_kv"]
        offset_plate_kv = drive["offset_plate_kv"]
        if math.isnan(peak_plate_kv):
            label.setText("—")
            label.setStyleSheet("")
            return

        if abs(offset_plate_kv) > 1e-9:
            body = (f"{peak_plate_kv:.4f} kV peak per plate "
                    f"({ac_plate_kv:.4f} AC ± {abs(offset_plate_kv):.4f} DC)")
        else:
            body = f"{ac_plate_kv:.4f} kV per plate"

        if peak_plate_kv > PLATE_RATING_KV:
            label.setText(f"{body}  ⚠ OVER the "
                          f"{PLATE_RATING_KV:.0f} kV/plate rating")
            label.setStyleSheet(theme.status_label(theme.FAULT))
        else:
            label.setText(body)
            label.setStyleSheet("")

    def _set_sweep(self, label, drive, size_mm, offset_mm):
        """Where the beam actually goes, in millimetres, offset included."""
        lo, hi = drive["scan_min_mm"], drive["scan_max_mm"]
        if math.isnan(lo) or math.isnan(hi):
            label.setText("—")
            label.setStyleSheet("")
            return
        sample_lo = offset_mm - size_mm / 2.0
        sample_hi = offset_mm + size_mm / 2.0
        text = (f"swept {lo:+.3f} to {hi:+.3f} mm   "
                f"(sample {sample_lo:+.3f} to {sample_hi:+.3f} mm)")
        if abs(offset_mm) > 1e-9:
            text += (f"   —   asymmetry is a DC term of "
                     f"{drive['offset_kv']:+.4f} kV differential "
                     f"(±{abs(drive['offset_plate_kv']):.4f} kV on the plates), "
                     f"not unequal amplitudes")
        label.setText(text)
        label.setStyleSheet(
            theme.status_label(theme.NEUTRAL, bold=False)
            if abs(offset_mm) > 1e-9 else "")

    def _check_envelope(self, caps, plate_kv, freq_of_axis):
        """Each channel against its OWN envelope; report the tightest.

        Averaging four capacitances would produce an envelope that belongs
        to no channel and hides the one that binds - which is the only one
        that matters.
        """
        worst = None      # (headroom_ratio, label, kv, f, envelope_kv, walls)
        for label, (c_pf, _src) in caps.items():
            axis = AXIS_OF_CHANNEL[label]
            f = freq_of_axis[axis]
            kv = plate_kv[axis]
            walls = envelope_walls(load_pf=c_pf, trip_ma=CAL_AC_TRIP_MA,
                                    shape="triangle", max_kv=CAL_MAX_KV,
                                    max_f_hz=AMP_MAX_BANDWIDTH_HZ)
            if f <= AMP_MAX_BANDWIDTH_HZ:
                env_kv = float(np.interp(f, walls["freq_hz"], walls["envelope_kv"]))
            else:
                env_kv = float("nan")
            ratio = (kv / env_kv) if (env_kv == env_kv and env_kv > 0) else float("inf")
            if worst is None or ratio > worst[0]:
                worst = (ratio, label, kv, f, env_kv, walls)

        ratio, label, kv, f, env_kv, walls = worst

        # The envelope is an AC picture: its current wall comes from
        # I = k*f*C*V, which a DC term does not contribute to.  The
        # amplifier's VOLTAGE ceiling, though, is a ceiling on the actual
        # output at any instant, so it has to be checked against the peak
        # WITH the offset in it - otherwise an offset drive can read
        # "inside the envelope" while asking the amplifier for more volts
        # than it has.
        over_ceiling = [
            axis for axis, d in getattr(self, "_drive", {}).items()
            if d["peak_plate_kv"] == d["peak_plate_kv"]
            and d["peak_plate_kv"] > CAL_MAX_KV
        ]

        if math.isnan(env_kv):
            self.lbl_envelope.setText(
                f"{label}: {f:.1f} Hz exceeds the {AMP_MAX_BANDWIDTH_HZ:.0f} Hz "
                f"bandwidth wall — out of envelope")
            in_envelope = False
        else:
            in_envelope = kv <= env_kv
            verdict = "INSIDE" if in_envelope else "OUTSIDE"
            self.lbl_envelope.setText(
                f"{label}: {kv:.3f} kV at {f:.1f} Hz vs. {env_kv:.3f} kV available "
                f"({100 * ratio:.0f}% of the wall) — {verdict} the envelope")
        if over_ceiling:
            in_envelope = False
            self.lbl_envelope.setText(
                self.lbl_envelope.text() +
                f"   ⚠ {', '.join(sorted(over_ceiling))} peak (offset included) "
                f"exceeds the {CAL_MAX_KV:.1f} kV amplifier ceiling")
        self.lbl_envelope.setStyleSheet(
            theme.status_label(theme.OK if in_envelope else theme.FAULT))

        self._draw_envelope(walls, label, caps, plate_kv, freq_of_axis)

    def _fill_species_deflections(self, kv_x, kv_y, plate_length_cm, plate_gap_cm,
                                   drift_cm, scan_half_x, scan_half_y, sel_row):
        """Every row's sweep at the CURRENTLY COMMANDED voltage.

        Not "what each species would need" - what each species would DO if
        you left the drive where it is and changed beam. That is the number
        that decides whether the next run overruns the sample or under-fills
        it.
        """
        self._updating = True
        try:
            for row in range(self.tbl_species.rowCount()):
                parsed = self._species_row(row)
                for col, (kv, target) in enumerate(
                        ((kv_x, scan_half_x), (kv_y, scan_half_y)),
                        start=N_EDITABLE_COLS):
                    item = self.tbl_species.item(row, col)
                    if parsed is None or math.isnan(kv):
                        item.setText("—")
                        item.setForeground(Qt.GlobalColor.gray)
                        continue
                    _name, _mass, energy_ev, charge = parsed
                    x_mm = displacement_mm(kv, plate_length_cm, plate_gap_cm,
                                            charge, energy_ev, drift_cm)
                    item.setText("—" if math.isnan(x_mm) else f"±{x_mm:.3f}")
                    # Green covers the sample plus its turnaround margin;
                    # amber does not reach it.  Over-covering is not a fault
                    # - it just wastes dose off the sample - so it is not red.
                    ok = (x_mm == x_mm) and x_mm >= target
                    item.setForeground(Qt.GlobalColor.darkGreen if ok
                                       else Qt.GlobalColor.darkYellow)
                font = self.tbl_species.item(row, 0).font()
                font.setBold(row == sel_row)
                for col in range(len(SPECIES_COLUMNS)):
                    self.tbl_species.item(row, col).setFont(font)
        finally:
            self._updating = False

    # ------------------------------------------------------------------
    # Plots
    # ------------------------------------------------------------------

    def _draw_envelope(self, walls, worst_label, caps, plate_kv, freq_of_axis):
        ax = self._ax_env
        ax.clear()
        ax.plot(walls["freq_hz"], walls["current_wall_kv"], label="Current wall")
        ax.plot(walls["freq_hz"], walls["voltage_wall_kv"], "--", label="Voltage wall")
        ax.plot(walls["freq_hz"], walls["slew_wall_kv"], ":", label="Slew wall")
        ax.axvline(walls["bandwidth_wall_hz"], color="gray", linestyle="-.",
                   label="Bandwidth wall")
        # All four requested points, so a channel that is fine is visibly
        # fine rather than merely absent.
        for marker, axis in (("o", "X"), ("s", "Y")):
            ax.plot([freq_of_axis[axis]], [plate_kv[axis]], marker, color="red",
                    markersize=9, label=f"{axis} axis requested")
        ax.set_xscale("log")
        ax.set_ylim(0, walls["voltage_wall_kv"][0] * 1.2)
        ax.set_xlabel("Frequency (Hz)")
        ax.set_ylabel("Peak plate voltage (kV)")
        ax.set_title(f"Walls shown for {worst_label} "
                     f"({caps[worst_label][0]:.0f} pF, tightest channel)",
                     fontsize="small")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize="small")
        self._fig_env.tight_layout()
        self._canvas_env.draw_idle()

