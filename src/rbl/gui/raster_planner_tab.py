"""
raster_planner_tab.py
PySide6 widget for the "Raster Planner" outer tab — the research deliverable.

WHAT THIS TAB ANSWERS
---------------------
"I want a patch of a given size, uniformly dosed, on the sample. Where do the
slit jaws go, and what do I set on the generators?"

TWO WAYS TO PUT AN EDGE ON THE PATCH, AND THE TAB MAKES YOU PICK ONE
--------------------------------------------------------------------
SLIT-LIMITED (the default, and what this beamline should normally run).
    The jaws sit inside the sweep, so the beam turns around ON THE JAW FACES.
    The jaw opening is imaged onto the sample, magnified by the ratio of the
    two drifts, and the dose across the whole patch is flat because a triangle
    sweep has constant velocity everywhere except at reversals — and the
    reversals are on the metal. The turnaround pile-up is not reduced, it is
    relocated off the sample.

STEERER-LIMITED (slits parked open).
    The patch edge is the sweep turnaround itself, so the beam's own width
    smears the edge and the turnaround dwell lands on the sample. This is what
    `raster_model.required_drive` computes and what this tab used to do
    unconditionally. It is still the right mode with the jaws out of the way,
    and it is what reproduces every figure the lab sheet has published, so it
    is kept — as a choice, not as a default.

WHY THE MODE MATTERS MORE THAN IT SOUNDS
-----------------------------------------
The magnification onto the sample is 1.64x on X and 1.50x on Y. Set the jaws
to the size you want on the sample and you get a patch about 60 % too big,
and not even the same amount too big on the two axes. That is the "leak"
between what the slits block and what shows up on the alumina, and no amount
of care with the drive voltage fixes it, because it is not about the drive.

WHERE THE OVERSCAN MARGIN GOES
------------------------------
`turnaround_k` — the sweep reversal happening k beam widths past the edge —
did not change value. It changed PLANE. In slit-limited mode it is measured
from the JAW edge, against the FWHM AT THE SLIT PLANE, because that is the
edge the beam actually has to clear. Measured at the sample against the FWHM
at the sample, as the old code did, it is bounding the wrong thing.

WHY THE FWHM INPUT SAYS "RASTER OFF"
------------------------------------
The width this tab needs is the beam's own width where the jaws are. BPM80
now sits ~45 mm upstream of the X jaws, which for this purpose is the same
plane, and the Profiler already reports its FWHM in mm — but a BPM trace
taken WHILE rastering measures the sweep envelope, not the beam. So the
workflow is: park the raster, measure, then plan.

WHY X AND Y ARE COMPUTED SEPARATELY
-----------------------------------
Samples are rectangles, the axes run at different frequencies, and — new
here — they have different drifts to their own jaws and therefore different
magnifications. One number could never have been right for both.

WHY THE SPECIES TABLE IS THERE
------------------------------
The deflection formula depends on q/E, so the SAME commanded voltage sweeps a
3 MeV proton and a 3 MeV Ni(3+) very differently. Setting the drive up for one
species and then changing beam is the practical way to overrun a sample.

WHY ALL FOUR CHANNELS' CAPACITANCE, NOT ONE
-------------------------------------------
The four channels are four separate physical loads — that is the entire reason
`load_calibration_store` is keyed per channel — and I = k*f*C*V is linear in C,
so using X+'s capacitance for Y- simply predicts the wrong current for Y-.
Each is predicted from its own measurement, and the envelope check reports the
channel with the least headroom rather than an average that belongs to nobody.

WHERE THE DEFAULTS LIVE
-----------------------
| What                     | Lives in                          |
|--------------------------|-----------------------------------|
| Species table rows       | rbl/config/beam_species.py        |
| Steerer + beamline layout| rbl/config/steerer_geometry.py    |
| Calibration limits       | rbl/config/calibration_config.py  |
| FWHM, patch, freq, etc.  | rbl/config/raster_defaults.py      |
| Runtime species list     | ~/.config/rbl/funcgen.json        |
"""
import math

import matplotlib
import numpy as np

matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from rbl.config.beam_species import DEFAULT_SPECIES, DEFAULT_SPECIES_ROW
from rbl.config.calibration_config import (
    CAL_AC_TRIP_MA,
    CAL_LOAD_CAP_PF,
    CAL_MAX_KV,
    ac_peak_current_ma,
)
from rbl.config.hardware_config import AMP_LABELS
from rbl.config.load_calibration_store import capacitance_pf_for
from rbl.config.persistence import load_config, save_config
from rbl.config.raster_defaults import (
    AMP_MAX_BANDWIDTH_HZ,
    FREQ_X_HZ_DEFAULT,
    FREQ_Y_HZ_DEFAULT,
    FWHM_MM_DEFAULT,
    OFFSET_X_MM_DEFAULT,
    OFFSET_Y_MM_DEFAULT,
    SAMPLE_HEIGHT_Y_MM,
    SAMPLE_WIDTH_X_MM,
    TURNAROUND_K_DEFAULT,
)
from rbl.config.scope_config import SCOPE_TAIL_TOLERANCE as TAIL_TOLERANCE
from rbl.config.steerer_geometry import (
    DRIFT_TO_SAMPLE_CM,
    DT_BORE_RADIUS_MM,
    PLATE_GAP_CM,
    PLATE_LENGTH_CM,
    PLATE_RATING_KV,
    PLATE_XY_SEPARATION_VERIFIED,
    SLIT_MODEL,
    SLIT_PLANE_FRACTIONS_VERIFIED,
    SLIT_X_PLANE_FRACTION,
    SLIT_Y_PLANE_FRACTION,
    beamline_planes_mm,
    drift_mm_for,
    slit_plane_z_mm,
)
from rbl.config.steerer_geometry import (
    describe as steerer_description,
)
from rbl.gui import theme
from rbl.gui.widgets.inputs import QuietDoubleSpinBox
from rbl.hardware import slit_raster_model as srm
from rbl.hardware.load_model import envelope_walls
from rbl.hardware.raster_model import (
    displacement_mm,
    dwell_uniformity,
    required_drive,
)
from rbl.hardware.raster_plan import envelope_status as _envelope_status
from rbl.hardware.raster_plan import steerer_limited_solve

_SPECIES_CFG_KEY = "raster_species"

# Which amplifier drives which axis.  X+ and X- are the push-pull pair on the
# X plates and both see the X-axis frequency; likewise Y.
AXIS_OF_CHANNEL = {"X+": "X", "X-": "X", "Y+": "Y", "Y-": "Y"}

# Which blade is the "+" side of each axis, in the sign convention
# slit_raster_model uses.  Matches hardware_config's A,B,C,D -> X+,X-,Y+,Y-.
BLADE_OF = {("X", "plus"): "X+", ("X", "minus"): "X-",
            ("Y", "plus"): "Y+", ("Y", "minus"): "Y-"}

MODE_SLIT    = "Slit-limited — turn around on the jaws"
MODE_STEERER = "Steerer-limited — slits parked open"

SPECIES_COLUMNS = ["Species", "Mass [amu]", "Energy [MeV]", "q",
                   "X deflection [mm]", "Y deflection [mm]"]
N_EDITABLE_COLS = 4

PLANE_COLUMNS = ["Plane", "z [mm]", "X drift", "X commanded", "X passes",
                 "Y drift", "Y commanded", "Y passes", "Note"]


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


def _mm(v, places=3):
    return "—" if (v is None or not math.isfinite(v)) else f"{v:.{places}f}"


class RasterPlannerTab(QWidget):
    """Calculator tab. Reads hardware only to OFFER numbers; never on its own.

    `beamline` and `profiler` are optional. Without them the tab is exactly
    the pure calculator it has always been — the Apply button and the "use
    the Profiler's reading" button simply stay disabled. Nothing here moves
    anything until someone presses a button and confirms.
    """

    slit_targets_ready = Signal(dict)   # {"X+": mm, "X-": mm, "Y+": mm, "Y-": mm}

    def __init__(self, beamline=None, profiler=None, parent=None):
        super().__init__(parent)
        self._updating = False       # guards the species table's itemChanged
        self._beamline = beamline
        self._profiler = profiler
        self._solution = {}          # last per-axis solve, for Apply and tests

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        top_row.addWidget(self._build_geometry_box(), stretch=0)
        top_row.addWidget(self._build_beam_box(), stretch=0)
        top_row.addWidget(self._build_target_box(), stretch=0)
        top_row.addWidget(self._build_amp_box(), stretch=0)
        top_row.addStretch(1)
        layout.addLayout(top_row, stretch=0)

        layout.addWidget(self._build_results_box(), stretch=0)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_beamline_page(), "Down the beamline")
        self._tabs.addTab(self._build_planes_page(), "Beamline planes")
        self._tabs.addTab(self._build_dose_page(), "Dose across the patch")
        self._tabs.addTab(self._build_envelope_page(), "Amplifier envelope")
        self._tabs.addTab(self._build_species_page(), "Species")
        layout.addWidget(self._tabs, stretch=1)

        for sb in (self.sb_drift_cm, self.sb_alumina_offset_mm,
                   self.sb_slit_fx, self.sb_slit_fy, self.sb_fwhm_mm,
                   self.sb_beam_centre_x_mm, self.sb_beam_centre_y_mm,
                   self.sb_patch_x_mm, self.sb_patch_y_mm,
                   self.sb_offset_x_mm, self.sb_offset_y_mm,
                   self.sb_turnaround_k, self.sb_freq_x_hz, self.sb_freq_y_hz):
            sb.valueChanged.connect(self._recompute)
        for chk in (self.chk_jaw_offset, self.chk_sweep_offset):
            chk.toggled.connect(self._recompute)
        self.cmb_mode.currentTextChanged.connect(self._recompute)

        default_row = min(DEFAULT_SPECIES_ROW, self.tbl_species.rowCount() - 1)
        if default_row >= 0:
            self.tbl_species.selectRow(default_row)
        self._recompute()

    # ------------------------------------------------------------------
    # Input boxes
    # ------------------------------------------------------------------

    def _build_geometry_box(self):
        box = QGroupBox("Beamline geometry")
        form = QFormLayout(box)

        # The steerer is not a choice.  There is one on this beamline, its
        # part number is stamped on the tube, and its plate geometry is a
        # fact (see steerer_geometry.py).
        self.lbl_steerer = QLabel(steerer_description())
        self.lbl_steerer.setWordWrap(True)
        form.addRow("Steerer:", self.lbl_steerer)

        self.lbl_slits = QLabel(SLIT_MODEL)
        self.lbl_slits.setWordWrap(True)
        form.addRow("Slits:", self.lbl_slits)

        self.sb_drift_cm = _spin(0.1, 1000.0, DRIFT_TO_SAMPLE_CM,
                                 decimals=1, step=1.0)
        form.addRow("Drift, steerer flange -> sample [cm]:", self.sb_drift_cm)

        # The alumina and the sample are not quite the same plane, and the
        # difference is a real magnification difference — small, but it is
        # the plane you MEASURE on versus the plane you IRRADIATE on, so
        # conflating them is how a calibration quietly acquires a bias.
        self.sb_alumina_offset_mm = _spin(-200.0, 200.0, 0.0, decimals=1, step=1.0)
        form.addRow("Alumina offset from sample [mm]:", self.sb_alumina_offset_mm)

        # Where inside the XYSL body each slit pair sits is UNMEASURED (the
        # partlist gives no internal dimension).  It is an input rather than a
        # constant because it is a guess, and because the magnification —
        # the number this whole tab now turns on — is directly proportional
        # to it.  See SLIT_PLANE_FRACTIONS_VERIFIED.
        self.sb_slit_fx = _spin(0.0, 1.0, SLIT_X_PLANE_FRACTION, decimals=3, step=0.05)
        form.addRow("X slit plane, fraction into XYSL:", self.sb_slit_fx)
        self.sb_slit_fy = _spin(0.0, 1.0, SLIT_Y_PLANE_FRACTION, decimals=3, step=0.05)
        form.addRow("Y slit plane, fraction into XYSL:", self.sb_slit_fy)

        self.lbl_magnification = QLabel("—")
        self.lbl_magnification.setWordWrap(True)
        self.lbl_magnification.setStyleSheet(f"font-size: {theme.FS_LABEL}px;")
        form.addRow("Jaw -> sample magnification:", self.lbl_magnification)

        unverified = []
        if not SLIT_PLANE_FRACTIONS_VERIFIED:
            unverified.append("slit plane positions inside the XYSL body")
        if not PLATE_XY_SEPARATION_VERIFIED:
            unverified.append("X–Y plate separation (borrowed from the manual's ES5 row)")
        if unverified:
            note = QLabel("Unmeasured, and the magnification is proportional to "
                          "them: " + "; ".join(unverified) + ".")
            note.setWordWrap(True)
            note.setStyleSheet(theme.status_label(theme.WARN, bold=False)
                               + f"font-size: {theme.FS_CAPTION}px;")
            form.addRow(note)
        return box

    def _build_beam_box(self):
        box = QGroupBox("Beam AT THE SLITS")
        form = QFormLayout(box)

        self.sb_fwhm_mm = _spin(0.001, 100.0, FWHM_MM_DEFAULT, decimals=3, step=0.1)
        form.addRow("FWHM at the slit plane [mm]:", self.sb_fwhm_mm)

        # WHERE THE BEAM IS, AS OPPOSED TO WHERE THE PATCH IS. These two are
        # different questions and used to be answered by the same input,
        # which is why moving the jaws to meet an off-centre beam made the
        # whole beamline picture look asymmetric. See
        # slit_raster_model.mechanical_blades_mm.
        self.sb_beam_centre_x_mm = _spin(-25.0, 25.0, 0.0, decimals=3, step=0.1)
        form.addRow("Beam centre X, slit frame [mm]:", self.sb_beam_centre_x_mm)
        self.sb_beam_centre_y_mm = _spin(-25.0, 25.0, 0.0, decimals=3, step=0.1)
        form.addRow("Beam centre Y, slit frame [mm]:", self.sb_beam_centre_y_mm)

        frame_note = QLabel(
            "Where the UN-RASTERED beam sits relative to mechanical slit "
            "centre — zero when the four blade currents read even. It shifts "
            "the blade numbers below and nothing else: the beam is still "
            "concentric in the aperture, the sweep is still symmetric about "
            "it, and the patch is still centred. Opening the jaws unequally "
            "to meet an off-centre beam is NOT an off-centre patch — that is "
            "\"Patch centre\", and only that moves the picture.")
        frame_note.setWordWrap(True)
        frame_note.setStyleSheet(f"font-size: {theme.FS_CAPTION}px; color: {theme.MUTED};")
        form.addRow(frame_note)

        self.lbl_profiler = QLabel("Profiler not wired")
        self.lbl_profiler.setWordWrap(True)
        self.lbl_profiler.setStyleSheet(f"font-size: {theme.FS_CAPTION}px;")
        form.addRow("Latest BPM reading:", self.lbl_profiler)

        self.btn_use_profiler = QPushButton("Use the Profiler's X reading")
        self.btn_use_profiler.setEnabled(False)
        self.btn_use_profiler.clicked.connect(self._use_profiler_fwhm)
        form.addRow(self.btn_use_profiler)

        # Not auto-pulled, deliberately.  A BPM trace taken while the raster
        # is running measures the SWEEP ENVELOPE, not the beam, and there is
        # nothing in the reading itself that says which it was.  Only the
        # operator knows, so only the operator presses the button.
        note = QLabel(
            "Measure with the RASTER OFF. A rastered BPM trace gives the "
            "sweep envelope's width, not the beam's, and reads high — which "
            "would size every jaw margin here too wide. Park the raster, "
            "take a shot, then plan.")
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size: {theme.FS_CAPTION}px; color: {theme.MUTED};")
        form.addRow(note)

        why = QLabel(
            "BPM80 sits just upstream of the X jaws, so its FWHM is the width "
            "at the slit plane — which is the width the jaw margin has to "
            "clear. The FWHM at the SAMPLE is a different, larger number and "
            "is not what sizes the overscan.")
        why.setWordWrap(True)
        why.setStyleSheet(f"font-size: {theme.FS_CAPTION}px; color: {theme.MUTED};")
        form.addRow(why)
        return box

    def _build_target_box(self):
        box = QGroupBox("Patch wanted on the sample")
        form = QFormLayout(box)

        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems([MODE_SLIT, MODE_STEERER])
        self.cmb_mode.setToolTip(
            "Slit-limited: the jaws cut the sweep, the patch is the jaw "
            "opening magnified, and the turnaround pile-up lands on the "
            "jaws.\n\nSteerer-limited: the slits are open, the patch edge is "
            "the sweep turnaround, and the pile-up lands on the sample. This "
            "is the older behaviour and what the lab sheet's figures assume.")
        form.addRow("Edge of the patch is set by:", self.cmb_mode)

        # FULL width and FULL height, because that is what comes off a pair
        # of calipers - the halving is the math's business.
        self.sb_patch_x_mm = _spin(0.02, 1000.0, SAMPLE_WIDTH_X_MM, decimals=3, step=0.5)
        form.addRow("Patch WIDTH  X (full) [mm]:", self.sb_patch_x_mm)
        self.sb_patch_y_mm = _spin(0.02, 1000.0, SAMPLE_HEIGHT_Y_MM, decimals=3, step=0.5)
        form.addRow("Patch HEIGHT Y (full) [mm]:", self.sb_patch_y_mm)

        # TWO INDEPENDENT WAYS TO MOVE AN OFF-CENTRE PATCH, both off by
        # default because the bending magnet has usually already put the beam
        # where it should be.  See slit_raster_model.solve_axis.
        self.chk_jaw_offset = QCheckBox("Move the patch with the JAWS (asymmetric blades)")
        self.chk_jaw_offset.setToolTip(
            "Opens one blade further than the other. Moves what the sample "
            "sees without touching the drive at all.")
        form.addRow(self.chk_jaw_offset)

        self.sb_offset_x_mm = _spin(-500.0, 500.0, OFFSET_X_MM_DEFAULT, decimals=3, step=0.5)
        form.addRow("Patch centre X [mm]:", self.sb_offset_x_mm)
        self.sb_offset_y_mm = _spin(-500.0, 500.0, OFFSET_Y_MM_DEFAULT, decimals=3, step=0.5)
        form.addRow("Patch centre Y [mm]:", self.sb_offset_y_mm)

        self.chk_sweep_offset = QCheckBox("Centre the SWEEP on the window (plate DC)")
        self.chk_sweep_offset.setToolTip(
            "Only worth anything when the jaws are asymmetric. A centred "
            "sweep has to reach the further jaw with full margin, so the "
            "nearer jaw gets more overscan than it needs and transmission "
            "drops. Centring the sweep on the window restores the minimum "
            "amplitude.\n\nCosts a DC term on the plates: equal and "
            "opposite, since a common-mode offset cancels.")
        form.addRow(self.chk_sweep_offset)

        self.sb_turnaround_k = _spin(0.0, 10.0, TURNAROUND_K_DEFAULT, decimals=2, step=0.1)
        form.addRow("Overscan past the jaw (k x FWHM):", self.sb_turnaround_k)

        self.lbl_droop = QLabel("—")
        self.lbl_droop.setWordWrap(True)
        self.lbl_droop.setStyleSheet(f"font-size: {theme.FS_LABEL}px;")
        form.addRow("Dose at the patch edge:", self.lbl_droop)

        # EVERY droop and overscan number on this tab comes from inverting a
        # NORMAL CDF against the FWHM in the box above.  That is an
        # assumption about the beam, not a measurement of it, and until the
        # Profiler started measuring a second and third level there was no
        # way to know whether it held.  Now there is, so it is said out loud
        # here rather than left implicit in slit_raster_model.
        #
        # This warns and does not correct.  A tail-inflated "effective FWHM"
        # quietly substituted into the box would make every number on the
        # tab describe a beam that does not exist, and the operator would
        # have no way to see it had happened.
        self.lbl_tails = QLabel("—")
        self.lbl_tails.setWordWrap(True)
        self.lbl_tails.setStyleSheet(f"font-size: {theme.FS_CAPTION}px; "
                                     f"color: {theme.MUTED};")
        self.lbl_tails.setToolTip(
            "The droop and overscan figures above assume a Gaussian beam — "
            "they invert a normal CDF from the FWHM alone.\n\n"
            "The Profiler measures the beam at 50 % and 10 % of peak. For a "
            "true Gaussian their ratio is 1.8226. Measured heavier means "
            "more current outside the painted field than these numbers "
            "allow for.")
        form.addRow("Gaussian assumption:", self.lbl_tails)
        return box

    def _build_amp_box(self):
        # Frequency lives HERE, not with the sizing inputs, because it does
        # not appear anywhere in the geometry: theta = V*l*q/(2*d*E) has no f
        # in it, so the swept width and the voltage that produces it are the
        # same at 6 Hz and 600 Hz.
        box = QGroupBox("Amplifier drive (does not affect the width)")
        form = QFormLayout(box)

        self.sb_freq_x_hz = _spin(0.1, 10_000.0, FREQ_X_HZ_DEFAULT, decimals=1, step=1.0)
        form.addRow("X-axis (fast) frequency [Hz]:", self.sb_freq_x_hz)
        self.sb_freq_y_hz = _spin(0.1, 10_000.0, FREQ_Y_HZ_DEFAULT, decimals=1, step=1.0)
        form.addRow("Y-axis (slow) frequency [Hz]:", self.sb_freq_y_hz)

        note = QLabel("Frequency sets the current draw (I = k·f·C·V) and where "
                      "the operating point sits on the envelope. It does not "
                      "change the required voltage or the swept width.")
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size: {theme.FS_CAPTION}px; color: {theme.MUTED};")
        form.addRow(note)

        self.btn_refresh_caps = QPushButton("Re-read measured capacitance")
        self.btn_refresh_caps.setToolTip(
            "Re-read ~/.config/rbl/load_calibration.json.\n\nHappens "
            "automatically when this tab is shown and when a Load "
            "Characterization run finishes; this is for when you want to be "
            "sure.")
        self.btn_refresh_caps.clicked.connect(self.refresh_capacitance)
        form.addRow(self.btn_refresh_caps)
        return box

    # ------------------------------------------------------------------
    # Results / pages
    # ------------------------------------------------------------------

    def _build_results_box(self):
        box = QGroupBox("Set these")
        outer = QHBoxLayout(box)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(14)

        # ---- Left column: species, blades, X/Y generator & plate drive ----
        left = QFormLayout()
        left.setSpacing(2)
        left.setContentsMargins(0, 0, 0, 0)

        self.lbl_design_species = QLabel("—")
        self.lbl_design_species.setStyleSheet(f"font-size: {theme.FS_LABEL}px;")
        left.addRow("Species:", self.lbl_design_species)

        self.lbl_blades = QLabel("—")
        self.lbl_blades.setStyleSheet(f"font-weight: bold; font-size: {theme.FS_BIG}px;")
        self.lbl_blades.setWordWrap(True)
        left.addRow("Slit blades [mm]:", self.lbl_blades)

        self.btn_apply_slits = QPushButton("Apply to slits")
        self.btn_apply_slits.setEnabled(False)
        self.btn_apply_slits.setToolTip(
            "Commands all four blades to the positions above, through the "
            "same Beamline.move_slit path the motor tab uses. Asks first.")
        self.btn_apply_slits.clicked.connect(self._apply_slits)
        left.addRow(self.btn_apply_slits)

        self.lbl_gen_x = QLabel("—")
        self.lbl_gen_x.setWordWrap(True)
        left.addRow("X — generator:", self.lbl_gen_x)
        self.lbl_plate_x = QLabel("—")
        self.lbl_plate_x.setWordWrap(True)
        left.addRow("X — plates:", self.lbl_plate_x)

        self.lbl_gen_y = QLabel("—")
        self.lbl_gen_y.setWordWrap(True)
        left.addRow("Y — generator:", self.lbl_gen_y)
        self.lbl_plate_y = QLabel("—")
        self.lbl_plate_y.setWordWrap(True)
        left.addRow("Y — plates:", self.lbl_plate_y)

        # ---- Right column: patch results and amplifier checks ----
        right = QFormLayout()
        right.setSpacing(2)
        right.setContentsMargins(0, 0, 0, 0)

        self.lbl_patch = QLabel("—")
        self.lbl_patch.setWordWrap(True)
        right.addRow("Painted:", self.lbl_patch)

        self.lbl_uniformity = QLabel("—")
        self.lbl_uniformity.setWordWrap(True)
        right.addRow("Dose:", self.lbl_uniformity)

        self.lbl_transmission = QLabel("—")
        self.lbl_transmission.setWordWrap(True)
        right.addRow("Transmission:", self.lbl_transmission)

        self.lbl_c_source = QLabel("—")
        self.lbl_c_source.setWordWrap(True)
        right.addRow("Load cap:", self.lbl_c_source)

        self.lbl_currents = QLabel("—")
        self.lbl_currents.setWordWrap(True)
        right.addRow("Peak current:", self.lbl_currents)

        self.lbl_envelope = QLabel("—")
        self.lbl_envelope.setWordWrap(True)
        right.addRow("Envelope (worst):", self.lbl_envelope)

        outer.addLayout(left, stretch=1)
        outer.addLayout(right, stretch=1)
        return box

    def _build_beamline_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 4, 4, 4)

        self._fig_line = Figure(figsize=(9, 4.6))
        self._canvas_line = FigureCanvasQTAgg(self._fig_line)
        self._canvas_line.setSizePolicy(QSizePolicy.Policy.Expanding,
                                        QSizePolicy.Policy.Expanding)
        # Two stacked axes plus an x-label need a floor, or the label is the
        # first thing squeezed out and the axis reads as unitless distance.
        self._canvas_line.setMinimumHeight(420)
        # THE JAW DETAIL GETS ITS OWN COLUMN, not an inset. The beamline axes
        # are ~15:1 wide, and the jaw question is entirely vertical -- is the
        # beam clear of the metal -- so an inset inherits the worst possible
        # aspect for it. A narrow tall panel beside each axis costs a fifth of
        # the width and makes the answer legible.
        gs = self._fig_line.add_gridspec(2, 2, width_ratios=(4.6, 1.0),
                                         wspace=0.16)
        self._ax_line_x = self._fig_line.add_subplot(gs[0, 0])
        self._ax_line_y = self._fig_line.add_subplot(gs[1, 0],
                                                     sharex=self._ax_line_x)
        self._ax_jaw_x = self._fig_line.add_subplot(gs[0, 1])
        self._ax_jaw_y = self._fig_line.add_subplot(gs[1, 1])
        lay.addWidget(self._canvas_line, stretch=1)

        hint = QLabel(
            "LEFT — everything is in the BEAM's frame: y = 0 is where the "
            "un-rastered beam arrives, not mechanical slit centre, so opening "
            "the jaws unequally to meet an off-centre beam does not tilt this "
            "picture. Dashed is what the steerer commands; it keeps growing "
            "with drift and is what an aperture UPSTREAM of the jaws must "
            "clear. Shaded is what survives the jaws — downstream of the slit "
            "plane the beam is confined to the cone the jaws admit, which is "
            "why the patch on the sample is the jaw opening magnified and not "
            "the sweep.\n"
            "RIGHT — the jaws close up. Grey is metal, and BOTH jaws are drawn: "
            "the scale is position measured FROM THE BEAM, so 0 is the beam "
            "axis and negative is the far side of it — resolving the beam's "
            "tail needs a deeper zoom than the half-gap, so the bottom of the "
            "panel usually reaches past the beam and out the other side of "
            "the aperture, where the second jaw is. The DASHED profile is the "
            "beam at the instant the jaw cuts it in half; the FILLED profile "
            "is the beam at the sweep reversal, which has to be far enough "
            "behind the metal that its tail has stopped coming through the "
            "aperture. The dimensioned gap between them is the overscan, and "
            "it is the only thing setting the dose at the patch edge.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"font-size: {theme.FS_CAPTION}px; color: {theme.MUTED};")
        lay.addWidget(hint)
        return page

    def _build_planes_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 4, 4, 4)

        self.tbl_planes = QTableWidget(0, len(PLANE_COLUMNS))
        self.tbl_planes.setHorizontalHeaderLabels(PLANE_COLUMNS)
        self.tbl_planes.verticalHeader().setVisible(False)
        self.tbl_planes.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # Numbers get exactly the width they need; the two prose columns take
        # what is left. Stretching all nine equally truncated the Note column,
        # which is the only one that says what to DO about the row.
        header = self.tbl_planes.horizontalHeader()
        for col in range(1, len(PLANE_COLUMNS) - 1):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(len(PLANE_COLUMNS) - 1,
                                    QHeaderView.ResizeMode.Stretch)
        self.tbl_planes.setWordWrap(True)
        self.tbl_planes.setMinimumHeight(200)
        lay.addWidget(self.tbl_planes, stretch=1)

        hint = QLabel(
            "\"Commanded\" is what the steerer asks for — it grows with drift "
            "forever, and it is what an aperture UPSTREAM of the jaws has to "
            "clear. \"Passes\" is what is left after the jaws: downstream of "
            "the slit plane the beam is confined to the cone the jaws admit, "
            "which is why the patch on the sample is the jaw opening "
            "magnified and not the sweep.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"font-size: {theme.FS_CAPTION}px; color: {theme.MUTED};")
        lay.addWidget(hint)
        return page

    def _build_dose_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 4, 4, 4)
        self._fig_dose = Figure(figsize=(7, 4))
        self._canvas_dose = FigureCanvasQTAgg(self._fig_dose)
        self._canvas_dose.setSizePolicy(QSizePolicy.Policy.Expanding,
                                        QSizePolicy.Policy.Expanding)
        self._ax_dose = self._fig_dose.add_subplot(111)
        lay.addWidget(self._canvas_dose, stretch=1)
        hint = QLabel(
            "Dose across the painted patch, mapped to the sample plane. In "
            "slit-limited mode this is flat to within the edge droop, because "
            "the sweep's turnaround pile-up is outside the jaws entirely. The "
            "curve is exact, not simulated: a constant-velocity pass of a "
            "Gaussian over the opening integrates to a difference of two "
            "normal CDFs.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"font-size: {theme.FS_CAPTION}px; color: {theme.MUTED};")
        lay.addWidget(hint)
        return page

    def _build_envelope_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 4, 4, 4)
        self._fig_env = Figure(figsize=(6, 4))
        self._canvas_env = FigureCanvasQTAgg(self._fig_env)
        self._canvas_env.setSizePolicy(QSizePolicy.Policy.Expanding,
                                       QSizePolicy.Policy.Expanding)
        self._ax_env = self._fig_env.add_subplot(111)
        lay.addWidget(self._canvas_env)
        return page

    def _build_species_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 4, 4, 4)

        self.lbl_target = QLabel("—")
        self.lbl_target.setStyleSheet(f"font-size: {theme.FS_CAPTION}px;")
        lay.addWidget(self.lbl_target)

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
        lay.addWidget(self.tbl_species, stretch=1)

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
        lay.addLayout(btn_row)

        hint = QLabel("Select a row to design the drive for that species. "
                      "Mass, energy and charge state are editable; deflection "
                      "is what that species would sweep at the voltage "
                      "commanded above.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"font-size: {theme.FS_CAPTION}px; color: {theme.MUTED};")
        lay.addWidget(hint)
        return page

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------

    def _planes_and_drifts(self):
        """(planes, drift dicts) for the current inputs.

        One place computes distances, so the readout, the table and the plot
        cannot disagree about where the slits are.
        """
        fx, fy = self.sb_slit_fx.value(), self.sb_slit_fy.value()
        z_sample = self.sb_drift_cm.value() * 10.0
        z_alumina = z_sample + self.sb_alumina_offset_mm.value()

        z_slit = {ax: slit_plane_z_mm(ax, fx, fy) for ax in ("X", "Y")}
        d_slit = {ax: drift_mm_for(ax, z_slit[ax]) for ax in ("X", "Y")}
        d_sample = {ax: drift_mm_for(ax, z_sample) for ax in ("X", "Y")}
        d_alumina = {ax: drift_mm_for(ax, z_alumina) for ax in ("X", "Y")}

        planes = [p for p in beamline_planes_mm(None, fx, fy)
                  if p[0] != "Chamber to sample"]
        planes.append(("Sample", z_sample, "target"))
        if abs(z_alumina - z_sample) > 1e-6:
            planes.append(("Alumina", z_alumina, "target"))
        planes.sort(key=lambda p: p[1])
        return planes, z_slit, d_slit, d_sample, d_alumina

    # ------------------------------------------------------------------
    # Recompute
    # ------------------------------------------------------------------

    def _recompute(self):
        try:
            self._do_recompute()
        except Exception as e:                       # pragma: no cover - guard
            self.lbl_envelope.setText(f"Error: {e}")
            self.lbl_envelope.setStyleSheet(theme.status_label(theme.FAULT))

    def _do_recompute(self):
        l_cm, d_cm = PLATE_LENGTH_CM, PLATE_GAP_CM
        fwhm_mm = self.sb_fwhm_mm.value()
        k = self.sb_turnaround_k.value()
        slit_mode = self.cmb_mode.currentText() == MODE_SLIT

        self._refresh_profiler_reading()

        sel_row, species = self._selected_species()
        if species is None:
            self.lbl_design_species.setText("selected row is not a valid species")
            return
        name, _mass, energy_ev, charge = species
        self.lbl_design_species.setText(
            f"{name} — {energy_ev / 1e6:g} MeV, charge state {charge}+")

        planes, z_slit, d_slit, d_sample, d_alumina = self._planes_and_drifts()

        self.lbl_magnification.setText(
            f"X ×{srm.magnification(d_slit['X'], d_sample['X']):.4f}   "
            f"Y ×{srm.magnification(d_slit['Y'], d_sample['Y']):.4f}"
            "   — a square jaw opening does not paint a square")

        want = {"X": self.sb_patch_x_mm.value(), "Y": self.sb_patch_y_mm.value()}
        centre = {"X": self.sb_offset_x_mm.value() if self.chk_jaw_offset.isChecked() else 0.0,
                  "Y": self.sb_offset_y_mm.value() if self.chk_jaw_offset.isChecked() else 0.0}

        self.lbl_droop.setText(
            f"{srm.edge_droop_pct(k * fwhm_mm, fwhm_mm):.4f} % below full dose "
            f"at k = {k:g} × FWHM  ({k * fwhm_mm:.3f} mm past the jaw)")

        if slit_mode:
            sol = {ax: srm.solve_axis(
                       painted_full_mm=want[ax], fwhm_at_slit_mm=fwhm_mm,
                       drift_to_slit_mm=d_slit[ax], drift_to_target_mm=d_sample[ax],
                       plate_length_cm=l_cm, plate_gap_cm=d_cm,
                       charge_state=charge, beam_energy_ev=energy_ev,
                       overscan_k=k, painted_center_mm=centre[ax],
                       use_sweep_offset=self.chk_sweep_offset.isChecked())
                   for ax in ("X", "Y")}
        else:
            sol = {ax: self._steerer_limited(want[ax], centre[ax], fwhm_mm, k,
                                             d_slit[ax], d_sample[ax], l_cm, d_cm,
                                             charge, energy_ev)
                   for ax in ("X", "Y")}
        self._solution = sol

        self._show_blades(sol, slit_mode)
        self._show_drive(sol)
        self._show_patch(sol, d_slit, d_sample, d_alumina, slit_mode)
        self._show_dose(sol, slit_mode)

        plate_kv = {ax: sol[ax]["ac_plate_kv"] for ax in ("X", "Y")}
        caps = self._channel_capacitance()
        freq_of_axis = {"X": self.sb_freq_x_hz.value(), "Y": self.sb_freq_y_hz.value()}
        self._show_capacitance_and_current(caps, plate_kv, freq_of_axis)
        self._check_envelope(caps, plate_kv, freq_of_axis)

        self._fill_planes(planes, sol, d_slit, slit_mode)
        self._draw_beamline(planes, sol, d_slit, z_slit, slit_mode, fwhm_mm)
        self._draw_dose(sol, slit_mode)

        self.lbl_target.setText(
            f"Commanded sweep half-span at the sample:  "
            f"X ±{_mm(sol['X'].get('sweep_half_at_target_mm'))} mm    "
            f"Y ±{_mm(sol['Y'].get('sweep_half_at_target_mm'))} mm   ·   "
            f"at the slit plane it is X ±{_mm(sol['X'].get('sweep_half_at_slit_mm'))}, "
            f"Y ±{_mm(sol['Y'].get('sweep_half_at_slit_mm'))} mm   ·   "
            f"deflections below are at the sample, at the commanded voltage")
        self._fill_species_deflections(
            sol["X"]["amplitude_kv"], sol["Y"]["amplitude_kv"], l_cm, d_cm,
            {"X": d_sample["X"] / 10.0, "Y": d_sample["Y"] / 10.0},
            sol["X"].get("sweep_half_at_target_mm", float("nan")),
            sol["Y"].get("sweep_half_at_target_mm", float("nan")), sel_row)

    def _steerer_limited(self, width_mm, centre_mm, fwhm_mm, k,
                         drift_to_slit_mm, drift_to_sample_mm,
                         l_cm, d_cm, charge, energy_ev):
        """Delegate to raster_plan.steerer_limited_solve (pure function)."""
        return steerer_limited_solve(
            width_mm=width_mm, centre_mm=centre_mm, fwhm_mm=fwhm_mm, k=k,
            drift_to_slit_mm=drift_to_slit_mm, drift_to_sample_mm=drift_to_sample_mm,
            l_cm=l_cm, d_cm=d_cm, charge=charge, energy_ev=energy_ev,
        )

    # ------------------------------------------------------------------
    # Readouts
    # ------------------------------------------------------------------

    def _beam_centre(self):
        return {"X": self.sb_beam_centre_x_mm.value(),
                "Y": self.sb_beam_centre_y_mm.value()}

    def _mechanical_blades(self, sol):
        """The four numbers the motor tab takes, in the SLIT's own frame.

        `sol` is in the beam's frame throughout. This is the only place the
        two frames meet, and it is the only place the beam centre is allowed
        to appear -- everything upstream of here, the plot included, stays in
        the beam's frame, which is why moving the jaws to meet an off-centre
        beam no longer tilts the whole picture.
        """
        centre = self._beam_centre()
        vals = {}
        for ax in ("X", "Y"):
            mech = srm.mechanical_blades_mm(
                sol[ax].get("blade_plus_mm", float("nan")),
                sol[ax].get("blade_minus_mm", float("nan")),
                centre[ax])
            vals[BLADE_OF[(ax, "plus")]] = mech["blade_plus_mm"]
            vals[BLADE_OF[(ax, "minus")]] = mech["blade_minus_mm"]
        return vals

    def _show_blades(self, sol, slit_mode):
        vals = self._mechanical_blades(sol)
        text = "   ".join(f"{lbl} {_mm(vals[lbl])}" for lbl in AMP_LABELS)
        centre = self._beam_centre()
        if any(abs(v) > 1e-9 for v in centre.values()):
            text += (f"   (includes beam centre X {centre['X']:+.3f}, "
                     f"Y {centre['Y']:+.3f} mm — the aperture is still "
                     f"centred on the beam)")

        if slit_mode:
            unreachable = [lbl for lbl, v in vals.items()
                           if math.isfinite(v) and v < 0]
            if unreachable:
                # A blade position past centre is geometrically consistent and
                # mechanically not: the drives close toward the axis, they do
                # not cross it. Flagged rather than clamped, because clamping
                # would silently paint a different patch than the one asked
                # for.
                self.lbl_blades.setText(
                    text + f"   ⚠ {', '.join(sorted(unreachable))} would have to "
                           f"cross MECHANICAL centre — the blades close toward "
                           f"the axis, they do not pass it")
                self.lbl_blades.setStyleSheet(
                    theme.status_label(theme.FAULT) + f"font-size: {theme.FS_BIG}px;")
            else:
                self.lbl_blades.setText(text)
                self.lbl_blades.setStyleSheet(
                    f"font-weight: bold; font-size: {theme.FS_BIG}px;")
            self.btn_apply_slits.setEnabled(
                self._beamline is not None and not unreachable
                and all(math.isfinite(v) for v in vals.values()))
        else:
            self.lbl_blades.setText(
                "slits parked open — open each blade to AT LEAST " + text +
                " or they become the limit instead of the steerer")
            self.lbl_blades.setStyleSheet(
                theme.status_label(theme.NEUTRAL, bold=False)
                + f"font-size: {theme.FS_LABEL}px;")
            self.btn_apply_slits.setEnabled(False)

    def _show_drive(self, sol):
        for ax, lbl_gen, lbl_plate in (("X", self.lbl_gen_x, self.lbl_plate_x),
                                       ("Y", self.lbl_gen_y, self.lbl_plate_y)):
            d = sol[ax]
            vpp = d.get("gen_amp_vpp", float("nan"))
            off_v = d.get("gen_offset_v", 0.0)
            peak_v = d.get("gen_peak_v", float("nan"))
            plus, minus = BLADE_OF[(ax, "plus")], BLADE_OF[(ax, "minus")]

            gen = f"{_mm(vpp, 4)} Vpp on {plus} and {minus}, 180° apart"
            if abs(off_v) > 1e-9:
                gen += (f", with {off_v:+.4f} V DC on {plus} and "
                        f"{-off_v:+.4f} V DC on {minus} — equal and opposite, "
                        f"since a common-mode offset cancels")
            # The +/-5 V amplifier input rail, checked in GENERATOR volts, in
            # the line where those volts are about to be typed.
            if math.isfinite(peak_v) and peak_v > 5.0:
                gen += f"   ⚠ peak {peak_v:.3f} V exceeds the ±5 V amplifier input"
                lbl_gen.setStyleSheet(theme.status_label(theme.FAULT, bold=False))
            else:
                lbl_gen.setStyleSheet("")
            lbl_gen.setText(gen)

            peak_plate = d.get("peak_plate_kv", float("nan"))
            ac_plate = d.get("ac_plate_kv", float("nan"))
            off_plate = d.get("offset_plate_kv", 0.0)
            body = f"±{_mm(ac_plate, 4)} kV per plate · {_mm(d.get('amplitude_kv'), 4)} kV plate-to-plate"
            if abs(off_plate) > 1e-9:
                body += f"  (+{abs(off_plate):.4f} kV DC, worst instant {peak_plate:.4f} kV)"
            # The rating is PER PLATE with respect to the enclosure, so it is
            # the plate voltage — not the differential — that has to clear it,
            # and at its worst instant, offset included.
            if math.isfinite(peak_plate) and peak_plate > PLATE_RATING_KV:
                lbl_plate.setText(body + f"   ⚠ OVER the {PLATE_RATING_KV:.0f} kV/plate rating")
                lbl_plate.setStyleSheet(theme.status_label(theme.FAULT, bold=False))
            else:
                lbl_plate.setText(body)
                lbl_plate.setStyleSheet("")

    def _show_patch(self, sol, d_slit, d_sample, d_alumina, slit_mode):
        parts = []
        for ax in ("X", "Y"):
            d = sol[ax]
            parts.append(f"{ax} {d.get('painted_min_mm', float('nan')):+.3f} to "
                         f"{d.get('painted_max_mm', float('nan')):+.3f} mm "
                         f"({_mm(d.get('painted_full_mm'))} wide)")
        text = "     ".join(parts)

        # The alumina is a different plane from the sample, so it sees a
        # different magnification of the same jaws. Small, and exactly the
        # bias that would otherwise creep into every camera calibration.
        if abs(self.sb_alumina_offset_mm.value()) > 1e-6 and slit_mode:
            alu = []
            for ax in ("X", "Y"):
                r = d_alumina[ax] / d_slit[ax]
                lo = -sol[ax].get("blade_minus_mm", float("nan")) * r
                hi = sol[ax].get("blade_plus_mm", float("nan")) * r
                alu.append(f"{ax} {hi - lo:.3f} mm")
            text += "   ·   on the alumina: " + ", ".join(alu)
        self.lbl_patch.setText(text)

    def _show_dose(self, sol, slit_mode):
        bits, worst_regime = [], None
        for ax in ("X", "Y"):
            d = sol[ax]
            u = d.get("dose_uniformity_pct", float("nan"))
            if slit_mode:
                bits.append(f"{ax} {_mm(u, 4)} % peak-to-peak "
                            f"(edges {_mm(d.get('dose_droop_plus_pct'), 4)} / "
                            f"{_mm(d.get('dose_droop_minus_pct'), 4)} % low)")
                if d.get("dose_regime") != "jaw-limited":
                    worst_regime = d.get("dose_regime")
            else:
                bits.append(f"{ax} {_mm(u, 3)} % peak-to-peak")
        text = "     ".join(bits)
        if worst_regime:
            text += (f"   ⚠ {worst_regime}: the sweep reverses inside the jaw "
                     f"opening, so the turnaround pile-up is back on the sample")
            self.lbl_uniformity.setStyleSheet(theme.status_label(theme.FAULT, bold=False))
        else:
            self.lbl_uniformity.setStyleSheet("")
        self.lbl_uniformity.setText(text)

        if slit_mode:
            tx = sol["X"].get("dose_transmitted_fraction", float("nan"))
            ty = sol["Y"].get("dose_transmitted_fraction", float("nan"))
            both = tx * ty if (math.isfinite(tx) and math.isfinite(ty)) else float("nan")
            self.lbl_transmission.setText(
                f"X {100 * tx:.1f} %     Y {100 * ty:.1f} %     →  "
                f"{100 * both:.1f} % of the beam reaches the sample; the rest "
                f"is intercepted by the jaws. That is the price of a flat top — "
                f"the turnaround dose is landing on metal instead of on the sample.")
            self.lbl_transmission.setStyleSheet("")
        else:
            self.lbl_transmission.setText(
                "not applicable — the jaws are not intercepting the sweep, so "
                "nothing is thrown away, and the turnaround dwell lands on the "
                "sample instead.")
            self.lbl_transmission.setStyleSheet(
                theme.status_label(theme.NEUTRAL, bold=False))

    def _show_capacitance_and_current(self, caps, plate_kv, freq_of_axis):
        self.lbl_c_source.setText("   ".join(
            f"{label} {c_pf:.0f} pF ({src})" for label, (c_pf, src) in caps.items()))
        n_fallback = sum(1 for _c, src in caps.values() if src == "fallback")
        self.lbl_c_source.setStyleSheet(
            theme.status_label(theme.OK if n_fallback == 0 else theme.WARN, bold=False))
        self.lbl_currents.setText("   ".join(
            f"{label} "
            f"{ac_peak_current_ma(freq_of_axis[AXIS_OF_CHANNEL[label]], plate_kv[AXIS_OF_CHANNEL[label]], load_pf=caps[label][0]):.3f} mA"
            for label in AMP_LABELS))

    # ------------------------------------------------------------------
    # Profiler / slits
    # ------------------------------------------------------------------

    def _refresh_profiler_reading(self):
        """Show the Profiler's latest FWHM without ever adopting it.

        Deliberately display-only. The reading carries no flag saying whether
        the raster was running when it was taken, and a rastered trace
        measures the sweep envelope rather than the beam — so the one piece of
        information that decides whether this number is usable lives with the
        operator, not in the data.
        """
        state = getattr(self._profiler, "_last_measured", None)
        if state is None:
            self.lbl_profiler.setText(
                "no measurement yet" if self._profiler is not None
                else "Profiler not wired")
            self.btn_use_profiler.setEnabled(False)
            self._refresh_tail_warning(None)
            return
        x_mm = getattr(state, "fwhm_x_mm", float("nan"))
        y_mm = getattr(state, "fwhm_y_mm", float("nan"))
        self._profiler_fwhm = (x_mm, y_mm)
        self._refresh_tail_warning(state)
        if not math.isfinite(x_mm) and not math.isfinite(y_mm):
            self.lbl_profiler.setText("last shot has no mm calibration — "
                                      "run the BPM fiducial calibration first")
            self.btn_use_profiler.setEnabled(False)
            return
        self.lbl_profiler.setText(f"X {_mm(x_mm)} mm   Y {_mm(y_mm)} mm")
        self.btn_use_profiler.setEnabled(math.isfinite(x_mm))

    def _refresh_tail_warning(self, state):
        """Say whether the last measured profile supports the Gaussian math.

        Reads the LIVE profile only. Nothing is remembered between shots and
        no saved beam shape is consulted: with quadrupole focusing the
        profile is not the same twice, so a warning based on a stored shape
        would be about a beam that is no longer in the pipe.
        """
        if state is None:
            self.lbl_tails.setText(
                "no profile measured — the droop and overscan figures above "
                "assume a Gaussian beam and nothing has tested that")
            self.lbl_tails.setStyleSheet(
                f"font-size: {theme.FS_CAPTION}px; color: {theme.MUTED};")
            return

        ratios = [r for r in (getattr(state, "tail_ratio_x", float("nan")),
                              getattr(state, "tail_ratio_y", float("nan")))
                  if isinstance(r, float) and math.isfinite(r)]
        excesses = [e for e in (getattr(state, "tail_excess_x", float("nan")),
                                getattr(state, "tail_excess_y", float("nan")))
                    if isinstance(e, float) and math.isfinite(e)]
        if not ratios or not excesses:
            note = getattr(state, "tail_note", "")
            self.lbl_tails.setText(
                "not tested — " + (note or "the last shot could not measure "
                                           "FWTM, so the tails are unknown"))
            self.lbl_tails.setStyleSheet(
                f"font-size: {theme.FS_CAPTION}px; color: {theme.MUTED};")
            return

        worst = max(excesses, key=abs)
        shown = "  ".join(f"{r:.3f}" for r in ratios)
        if worst > TAIL_TOLERANCE:
            # The consequential direction. Heavier tails put more current
            # outside the painted field than a Gaussian of this FWHM would,
            # so the margin these numbers ask for is too small.
            self.lbl_tails.setText(
                f"measured FWTM/FWHM {shown} vs 1.8226 — tails are "
                f"{worst * 100:.0f} % HEAVIER than Gaussian, so the droop "
                f"above is optimistic and the overscan it implies is too "
                f"small. Widen the turnaround margin, or measure the edge "
                f"dose directly.")
            self.lbl_tails.setStyleSheet(theme.status_label(theme.WARN,
                                                            bold=False)
                                         + f"font-size: {theme.FS_CAPTION}px;")
        elif worst < -TAIL_TOLERANCE:
            self.lbl_tails.setText(
                f"measured FWTM/FWHM {shown} vs 1.8226 — tails are "
                f"{-worst * 100:.0f} % LIGHTER than Gaussian: a flat-topped "
                f"or scraped profile. The droop above is pessimistic, and "
                f"something upstream is probably clipping the beam.")
            self.lbl_tails.setStyleSheet(theme.status_label(theme.WARN,
                                                            bold=False)
                                         + f"font-size: {theme.FS_CAPTION}px;")
        else:
            self.lbl_tails.setText(
                f"measured FWTM/FWHM {shown} vs 1.8226 — consistent with a "
                f"Gaussian, so the figures above stand")
            self.lbl_tails.setStyleSheet(theme.status_label(theme.OK,
                                                            bold=False)
                                         + f"font-size: {theme.FS_CAPTION}px;")

    def _use_profiler_fwhm(self):
        x_mm, _y_mm = getattr(self, "_profiler_fwhm", (float("nan"), float("nan")))
        if math.isfinite(x_mm):
            self.sb_fwhm_mm.setValue(x_mm)

    def _apply_slits(self):
        """Command all four blades. Asks first, and names every number."""
        if self._beamline is None or not self._solution:
            return
        # The Galil's frame, not the beam's -- see `_mechanical_blades`.
        targets = self._mechanical_blades(self._solution)
        listing = "\n".join(f"    {lbl}   {targets[lbl]:.3f} mm" for lbl in AMP_LABELS)
        answer = QMessageBox.question(
            self, "Move the slits?",
            "Command all four blades to:\n\n" + listing +
            "\n\nThis moves hardware.", QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        for label in AMP_LABELS:
            self._beamline.move_slit(label, targets[label])
        self.slit_targets_ready.emit(targets)

    # ------------------------------------------------------------------
    # Down the beamline
    # ------------------------------------------------------------------

    def _envelopes(self, sol, d_slit, z, slit_mode):
        """{axis: envelope_at(...)} for one plane. Shared by table and plot."""
        out = {}
        for ax in ("X", "Y"):
            d = sol[ax]
            out[ax] = srm.envelope_at(
                drift_mm=drift_mm_for(ax, z),
                drift_to_slit_mm=d_slit[ax],
                amplitude_kv=d.get("amplitude_kv", float("nan")),
                offset_kv=d.get("offset_kv", 0.0),
                mm_per_kv_at_slit=d.get("mm_per_kv_at_slit", float("nan")),
                blade_plus_mm=(d.get("blade_plus_mm", float("inf"))
                               if slit_mode else float("inf")),
                blade_minus_mm=(d.get("blade_minus_mm", float("inf"))
                                if slit_mode else float("inf")))
        return out

    def _fill_planes(self, planes, sol, d_slit, slit_mode):
        self.tbl_planes.setRowCount(len(planes))
        for row, (name, z, kind) in enumerate(planes):
            env = self._envelopes(sol, d_slit, z, slit_mode)
            cells = [name, f"{z:.1f}"]
            for ax in ("X", "Y"):
                e = env[ax]
                cells.append(f"{drift_mm_for(ax, z):.1f}")
                cells.append(f"{e['sweep_min_mm']:+.3f} … {e['sweep_max_mm']:+.3f}")
                cells.append(f"{e['passed_min_mm']:+.3f} … {e['passed_max_mm']:+.3f}")
            # Reorder to Plane, z, Xdrift, Xcmd, Xpass, Ydrift, Ycmd, Ypass
            cells = [cells[0], cells[1], cells[2], cells[3], cells[4],
                     cells[5], cells[6], cells[7]]
            cells.append(self._plane_note(name, z, kind, sol, env, slit_mode))
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                if kind == "slit":
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.tbl_planes.setItem(row, col, item)

    def _plane_note(self, name, z, kind, sol, env, slit_mode):
        """The one sentence that makes each row worth reading.

        Verdicts on slit rows are deliberately NOT pass/fail. Jaws inside the
        sweep is the whole point in slit-limited mode, and it is also how the
        slits get used as a profile monitor — so "intercepting" is a
        description, not a warning. A round bore the beam would paint IS a
        warning, because nothing wants that.
        """
        if kind == "slit":
            ax = "X" if name.startswith("X") else "Y"
            d = sol[ax]
            if not slit_mode:
                return (f"open {ax} jaws past ±"
                        f"{d.get('sweep_half_at_slit_mm', float('nan')):.3f} mm "
                        f"or they clip the sweep")
            bp = d.get("blade_plus_mm", float("nan"))
            bm = d.get("blade_minus_mm", float("nan"))
            m = d.get("overscan_margin_mm", float("nan"))
            return (f"set {ax}+ {bp:.3f}, {ax}- {bm:.3f} mm; sweep turns "
                    f"around {m:.3f} mm outside each jaw")
        if name == "DT":
            worst = max(abs(env[ax][k]) for ax in ("X", "Y")
                        for k in ("sweep_min_mm", "sweep_max_mm"))
            # The DT is the only aperture UPSTREAM of the jaws, so it is the
            # one place the full unclipped sweep has to fit.
            if worst > DT_BORE_RADIUS_MM:
                return (f"⚠ sweep reaches {worst:.2f} mm vs {DT_BORE_RADIUS_MM:.1f} mm "
                        f"assumed bore — the beam is painting the drift tube")
            return (f"clears the {DT_BORE_RADIUS_MM:.1f} mm assumed bore "
                    f"({worst:.2f} mm worst)")
        if name in ("Sample", "Alumina"):
            return "   ".join(
                f"{ax} {env[ax]['passed_max_mm'] - env[ax]['passed_min_mm']:.3f} mm painted"
                for ax in ("X", "Y"))
        return ""

    def _draw_beamline(self, planes, sol, d_slit, z_slit, slit_mode, fwhm_mm):
        """The picture: what the steerer commands vs what survives the jaws.

        DRAWN IN THE BEAM'S FRAME, ALWAYS. y = 0 is where the un-rastered beam
        arrives, not mechanical slit centre. So opening the jaws unequally to
        meet a beam the bending magnet put off-centre does NOT tilt this
        picture -- the aperture is still centred on the beam, the sweep is
        still symmetric about it, and the only thing that moved is the two
        numbers typed into the motor tab. The picture goes asymmetric for
        exactly one reason: someone asked for a patch off-centre FROM THE
        BEAM, which is a real asymmetry. See
        slit_raster_model.mechanical_blades_mm.

        THE FWHM MARKERS ARE AT THE JAW, NOT DOWN THE MIDDLE. A beam-width
        band drawn along the axis says nothing: the beam is not sitting on the
        axis, it is sweeping. The question this plot has to answer is "how far
        past the jaw does the reversal have to be, given the beam width", so
        the beam is drawn twice at each jaw plane -- once straddling the jaw
        edge (where the jaw cuts it in half) and once at the sweep reversal
        (where it has to be fully clear) -- with the overscan between them
        dimensioned.
        """
        from matplotlib.ticker import AutoMinorLocator, MultipleLocator

        z_max = max(z for _n, z, _k in planes)
        zs = np.linspace(0.0, z_max, 300)
        z_sample = self.sb_drift_cm.value() * 10.0
        patch_off = (self.chk_jaw_offset.isChecked()
                     and (abs(self.sb_offset_x_mm.value()) > 1e-9
                          or abs(self.sb_offset_y_mm.value()) > 1e-9))
        bw = z_max * 0.016          # half-width of the beam markers, in z

        for ax, axis_plot, jaw_plot, colour in (
                ("X", self._ax_line_x, self._ax_jaw_x, "tab:blue"),
                ("Y", self._ax_line_y, self._ax_jaw_y, "tab:orange")):
            p = axis_plot
            p.clear()
            jaw_plot.clear()
            d = sol[ax]
            amp_kv = d.get("amplitude_kv", float("nan"))
            off_kv = d.get("offset_kv", 0.0) if slit_mode else 0.0
            mpk    = d.get("mm_per_kv_at_slit", float("nan"))
            blade_plus  = (d.get("blade_plus_mm",  float("inf"))
                           if slit_mode else float("inf"))
            blade_minus = (d.get("blade_minus_mm", float("inf"))
                           if slit_mode else float("inf"))

            cmd_hi, cmd_lo, pass_hi, pass_lo = [], [], [], []
            for z in zs:
                e = srm.envelope_at(
                    drift_mm=drift_mm_for(ax, z), drift_to_slit_mm=d_slit[ax],
                    amplitude_kv=amp_kv, offset_kv=off_kv,
                    mm_per_kv_at_slit=mpk,
                    blade_plus_mm=blade_plus, blade_minus_mm=blade_minus)
                cmd_hi.append(e["sweep_max_mm"])
                cmd_lo.append(e["sweep_min_mm"])
                pass_hi.append(e["passed_max_mm"])
                pass_lo.append(e["passed_min_mm"])
            cmd_hi, cmd_lo = np.array(cmd_hi), np.array(cmd_lo)
            pass_hi, pass_lo = np.array(pass_hi), np.array(pass_lo)

            p.plot(zs, cmd_hi, "--", color=colour, lw=1.2, label="commanded sweep")
            p.plot(zs, cmd_lo, "--", color=colour, lw=1.2)

            if slit_mode:
                mask = zs >= z_slit[ax]
                if mask.any():
                    bz = zs[mask]
                    p.fill_between(bz, pass_hi[mask], cmd_hi[mask], color="0.55",
                                   alpha=0.40, hatch="///", linewidth=0,
                                   label="blocked by jaws")
                    p.fill_between(bz, cmd_lo[mask], pass_lo[mask], color="0.55",
                                   alpha=0.40, hatch="///", linewidth=0)

            p.fill_between(zs, pass_lo, pass_hi, color=colour, alpha=0.30,
                           label="beam through jaws")
            p.axhline(0.0, color="0.6", lw=0.6)

            zj = z_slit[ax]
            p.axvline(zj, color="0.15", lw=2.0, zorder=5)

            if slit_mode and math.isfinite(blade_plus) and math.isfinite(blade_minus):
                self._draw_jaw_detail(p, ax, zj, bw, blade_plus, blade_minus,
                                      d, fwhm_mm)
                self._draw_jaw_inset(jaw_plot, ax, blade_plus, blade_minus,
                                     d, fwhm_mm)
            else:
                jaw_plot.set_axis_off()

            # What actually lands, dimensioned at the sample plane -- the
            # number the whole tab exists to produce, on the picture rather
            # than only in a table.
            lo = d.get("painted_min_mm", float("nan"))
            hi = d.get("painted_max_mm", float("nan"))
            if math.isfinite(lo) and math.isfinite(hi):
                p.annotate("", xy=(z_sample, hi), xytext=(z_sample, lo),
                           arrowprops=dict(arrowstyle="<->", color="0.10", lw=1.6),
                           zorder=6)
                p.text(z_sample - z_max * 0.012, hi,
                       f"{hi - lo:.3f} mm\non sample", ha="right", va="bottom",
                       fontsize="x-small", color="0.10", fontweight="bold")

            for name, z, kind in planes:
                if kind == "target" or name == "DT":
                    p.axvline(z, color="0.85", lw=0.6)
                    p.annotate(name, (z, 0), xytext=(3, -11),
                               textcoords="offset points", fontsize="xx-small",
                               color="0.5")

            # Denser ticks: reading a millimetre off this plot was guesswork
            # against four gridlines.
            p.xaxis.set_major_locator(MultipleLocator(250.0))
            p.xaxis.set_minor_locator(AutoMinorLocator(5))
            p.yaxis.set_minor_locator(AutoMinorLocator(2))
            p.tick_params(axis="both", which="major", labelsize="x-small")
            p.grid(True, which="major", alpha=0.30)
            p.grid(True, which="minor", alpha=0.12)
            p.set_ylabel(f"{ax} [mm]")
            p.margins(y=0.14)
            # The sample dimension is drawn AT the last plane, so without a
            # little air on the right its arrowhead sits on the spine.
            p.set_xlim(-z_max * 0.02, z_max * 1.05)

        handles, labels = self._ax_line_x.get_legend_handles_labels()
        for old_legend in self._fig_line.legends:
            old_legend.remove()
        # One legend for the figure, above the axes: per-axes legends sat on
        # top of the commanded-sweep lines they were labelling.
        self._fig_line.legend(handles, labels, loc="upper center",
                              bbox_to_anchor=(0.5, 0.935), ncol=len(handles),
                              fontsize="x-small", frameon=False)

        note = ("  ·  patch deliberately off-centre FROM THE BEAM"
                if patch_off else
                "  ·  beam frame: y = 0 is the beam, not mechanical slit centre")
        self._fig_line.suptitle(
            "Deflection down the beamline — dashed: commanded, shaded: through "
            "jaws, hatched: blocked by jaws" + note, fontsize="x-small", y=0.995)
        self._ax_line_y.set_xlabel("Distance from the steerer flange [mm]")
        # subplots_adjust, not tight_layout: the jaw panels' hatched axhspan
        # is not a tight_layout-compatible artist and it warns on every redraw.
        self._fig_line.subplots_adjust(left=0.055, right=0.985, top=0.855,
                                       bottom=0.115, hspace=0.30, wspace=0.20)
        self._canvas_line.draw_idle()

    def _draw_jaw_detail(self, p, ax, zj, bw, blade_plus, blade_minus, d, fwhm_mm):
        """Markers on the main plot: where the metal is, and how wide the beam
        is when it gets there.

        The beam is drawn as a CAPPED BAR, not a filled box. A box spanning
        several tens of millimetres of drift implies the beam is that wide
        along the beamline too, which it is not -- the marker is a measurement
        of one quantity, at one plane, and a bar with end caps and a centre
        dot reads as exactly that. It is also narrow enough not to bury the
        jaw tick it is being compared against, which was the whole point of
        putting them side by side.

        Everything else about the jaw lives in the panel to the right
        (`_draw_jaw_inset`); the main plot's y-axis has to span the entire
        commanded sweep, so a labelled beam-width diagram drawn to that scale
        is unreadable.
        """
        centre = d.get("sweep_center_at_slit_mm", 0.0)
        half   = d.get("sweep_half_at_slit_mm", float("nan"))
        if not math.isfinite(half):
            return
        fw = max(fwhm_mm, 1e-6)
        cap = bw * 0.55

        for i, (edge, reversal) in enumerate(((blade_plus, centre + half),
                                              (-blade_minus, centre - half))):
            # The jaw edge: a short heavy tick, so it reads as metal at ONE
            # place rather than as a level running the length of the beamline.
            p.plot([zj - bw * 0.5, zj + bw * 0.5], [edge, edge], color="0.10",
                   lw=3.0, solid_capstyle="butt", zorder=7)
            # The beam, to scale, at the instant the sweep reverses.
            p.plot([zj, zj], [reversal - fw / 2.0, reversal + fw / 2.0],
                   color="green", lw=2.0, solid_capstyle="butt", zorder=6,
                   label="beam FWHM at turnaround" if i == 0 else None)
            for y_cap in (reversal - fw / 2.0, reversal + fw / 2.0):
                p.plot([zj - cap, zj + cap], [y_cap, y_cap], color="green",
                       lw=1.6, solid_capstyle="butt", zorder=6)
            p.plot([zj], [reversal], "o", color="green", ms=3.0, zorder=7)

        p.annotate(f"{ax} jaws", xy=(zj, 1.0), xycoords=("data", "axes fraction"),
                   xytext=(0, -10), textcoords="offset points",
                   fontsize="x-small", color="0.10", fontweight="bold",
                   ha="center")

    def _draw_jaw_inset(self, ins, ax, blade_plus, blade_minus, d, fwhm_mm):
        """The design question, drawn: does the beam's TAIL clear the metal?

        A zoom on the jaw, in the beam's frame, with the beam drawn as the
        Gaussian it actually is rather than as a bar. The dashed profile is
        the beam at the instant the jaw is cutting it in half; the filled
        profile is the beam at the sweep reversal, which has to be far enough
        behind the metal that essentially none of it is still coming through
        the aperture.

        That is the entire content of `overscan_k` -- 1.5 beam widths leaves
        0.02 % of the dose at the patch edge, 0.5 leaves 12 % -- and it is the
        one thing the main plot cannot show, because the main plot's y-axis
        has to hold the whole commanded sweep, which is several times the jaw
        opening at any useful overscan.

        WHY THE SCALE GOES NEGATIVE, AND WHY BOTH JAWS ARE DRAWN
        --------------------------------------------------------
        The scale is position at the slit plane measured FROM THE BEAM, so
        zero is the beam axis and negative is simply the far side of it. The
        zoom needed to resolve the beam's tail is usually deeper than the
        half-gap, so the bottom of the panel lands past the beam axis and out
        the OTHER side of the aperture -- where there is a second jaw.

        The first version drew that region as open space, which was wrong and
        was exactly why the negative numbers looked unexplained: they were
        labelling metal that had not been shaded. Both jaws are now drawn, so
        the panel is a complete cross-section of the slit -- metal, aperture,
        metal -- and a negative reading explains itself.
        """
        margin = d.get("overscan_margin_mm", float("nan"))
        centre = d.get("sweep_center_at_slit_mm", 0.0)
        half   = d.get("sweep_half_at_slit_mm", float("nan"))
        if not (math.isfinite(margin) and math.isfinite(half) and fwhm_mm > 0):
            ins.set_axis_off()
            return
        edge, reversal = blade_plus, centre + half
        sigma = fwhm_mm / srm.FWHM_PER_SIGMA

        lo = min(edge - 3.2 * sigma, reversal - 3.6 * sigma)
        hi = reversal + 3.2 * sigma
        ys = np.linspace(lo, hi, 400)

        metal = dict(color="0.45", alpha=0.42, hatch="////", linewidth=0, zorder=1)
        ins.axhspan(edge, hi, **metal)
        ins.axhline(edge, color="0.10", lw=2.2, zorder=4)
        # The OTHER jaw, whenever the zoom reaches it.
        if lo < -blade_minus:
            ins.axhspan(lo, -blade_minus, **metal)
            ins.axhline(-blade_minus, color="0.10", lw=2.2, zorder=4)
            ins.text(2.02, lo, f"{ax}- JAW  ", fontsize="xx-small", color="0.15",
                     va="bottom", ha="right", zorder=5, fontweight="bold")
        # Zero is the beam, which is what makes a negative number readable.
        if lo < 0.0 < hi:
            ins.axhline(0.0, color="0.35", lw=0.9, ls=":", zorder=4)
            ins.text(2.02, 0.0, "beam axis  ", fontsize="xx-small", color="0.35",
                     va="bottom", ha="right", zorder=5)

        ins.plot(np.exp(-0.5 * ((ys - edge) / sigma) ** 2), ys, "--",
                 color="green", lw=1.1, alpha=0.85, zorder=3)
        g_rev = np.exp(-0.5 * ((ys - reversal) / sigma) ** 2)
        ins.fill_betweenx(ys, 0.0, g_rev, color="green", alpha=0.40, zorder=3)
        ins.plot(g_rev, ys, color="green", lw=1.3, zorder=3)

        ins.annotate("", xy=(1.28, reversal), xytext=(1.28, edge),
                     arrowprops=dict(arrowstyle="<->", color="green", lw=1.1),
                     zorder=5)
        ins.text(1.36, (edge + reversal) / 2.0,
                 f"{d.get('overscan_k', float('nan')):g}×FWHM\n{margin:.2f} mm",
                 fontsize="xx-small", color="green", va="center", ha="left")
        # Top-RIGHT: at a small overscan the turnaround peak reaches the top
        # of the panel on the left and sits under this label.
        ins.text(2.02, hi, f"{ax}+ JAW  ", fontsize="xx-small", color="0.15",
                 va="top", ha="right", zorder=5, fontweight="bold")
        # Centred in the gap that is actually visible, so it labels the white
        # band rather than crowding whichever jaw happens to be nearest the
        # bottom of the zoom.
        gap_lo = max(lo, -blade_minus)
        ins.text(0.04, (gap_lo + edge) / 2.0, "aperture", fontsize="xx-small",
                 color="0.35", va="center", zorder=5)

        ins.set_xlim(0.0, 2.1)
        ins.set_ylim(lo, hi)
        ins.set_xticks([])
        ins.tick_params(axis="y", labelsize="xx-small", pad=1)
        ins.set_ylabel("mm from the beam, at the slit plane",
                       fontsize="xx-small", labelpad=1)
        ins.set_title(f"the {ax} jaws — FWHM {fwhm_mm:.2f} mm",
                      fontsize="xx-small", pad=3)
        for spine in ins.spines.values():
            spine.set_linewidth(0.6)

    def _draw_dose(self, sol, slit_mode):
        ax = self._ax_dose
        ax.clear()
        drew, lowest = False, 1.0
        for name, colour, style in (("X", "tab:blue", "-"),
                                    ("Y", "tab:orange", "--")):
            d = sol[name]
            xs = d.get("dose_x_mm")
            ys = d.get("dose_dose")
            if xs is None or ys is None or len(xs) == 0:
                continue
            # The dose curve is computed at the SLIT plane; the sample sees it
            # magnified, and the sample is the plane anyone cares about.
            scale = d.get("magnification", 1.0) if slit_mode else 1.0
            # X solid, Y dashed: in the symmetric case the two curves are
            # identical and one would sit invisibly under the other.
            ax.plot(np.asarray(xs) * scale, ys, style, color=colour,
                    label=f"{name} axis")
            lowest = min(lowest, float(np.min(ys)))
            drew = True
        if drew:
            ax.axhline(1.0, color="0.7", lw=0.6, ls=":")
            ax.set_xlabel("Position on the sample [mm]")
            ax.set_ylabel("Relative dose")
            # AUTOSCALED, not pinned to 0..1. A well-planned slit-limited
            # patch is flat to 0.02 %, and against a full 0-1 axis that is a
            # straight line that says nothing. The question this plot answers
            # is "how flat", so the axis has to resolve the answer.
            span = max(1e-4, 1.0 - lowest)
            ax.set_ylim(lowest - 0.15 * span, 1.0 + 0.15 * span)
            ax.grid(True, alpha=0.25)
            ax.legend(fontsize="small")
            pp = " · ".join(
                f"{a} {sol[a].get('dose_uniformity_pct', float('nan')):.4f} % p-p"
                for a in ("X", "Y"))
            ax.set_title(
                ("Dose across the patch — flat because the turnaround is on "
                 "the jaws" if slit_mode else
                 "Dose across the patch — edges roll off into the turnaround")
                + f"   ({pp})", fontsize="small")
        self._fig_dose.tight_layout()
        self._canvas_dose.draw_idle()

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

    def _check_envelope(self, caps, plate_kv, freq_of_axis):
        """Delegate to raster_plan.envelope_status; update envelope label.

        Each channel is checked against its own measured capacitance and the
        tightest is reported.  The pure computation lives in raster_plan so it
        can be unit-tested without Qt.
        """
        st = _envelope_status(caps, plate_kv, freq_of_axis, self._solution,
                               axis_of_channel=AXIS_OF_CHANNEL)
        label    = st["worst_label"]
        kv       = st["kv"]
        f        = st["freq_hz"]
        env_kv   = st["env_kv"]
        ratio    = st["ratio"]
        walls    = st["walls"]
        in_env   = st["in_envelope"]

        if st["exceeded_bandwidth"]:
            self.lbl_envelope.setText(
                f"{label}: {f:.1f} Hz exceeds the {AMP_MAX_BANDWIDTH_HZ:.0f} Hz "
                f"bandwidth wall — out of envelope")
        else:
            verdict = "INSIDE" if (in_env and not st["over_ceiling"]) else "OUTSIDE"
            self.lbl_envelope.setText(
                f"{label}: {kv:.3f} kV at {f:.1f} Hz vs. {env_kv:.3f} kV available "
                f"({100 * ratio:.0f}% of the wall) — {verdict} the envelope")
        if st["over_ceiling"]:
            self.lbl_envelope.setText(
                self.lbl_envelope.text() +
                f"   ⚠ {', '.join(sorted(st['over_ceiling']))} peak (offset included) "
                f"exceeds the {CAL_MAX_KV:.1f} kV amplifier ceiling")
        self.lbl_envelope.setStyleSheet(
            theme.status_label(theme.OK if in_env else theme.FAULT))

        self._draw_envelope(walls, label, caps, plate_kv, freq_of_axis)

    def _fill_species_deflections(self, kv_x, kv_y, plate_length_cm, plate_gap_cm,
                                   drift_cm_of_axis, scan_half_x, scan_half_y,
                                   sel_row):
        """Every row's sweep at the CURRENTLY COMMANDED voltage.

        `drift_cm_of_axis` is per axis, not one number for both: the Y plates
        sit ~17 cm upstream of the X plates, so the two axes have different
        drifts to the same sample. Sharing one drift here made the selected
        species' own row disagree with the target it had just been solved
        for, by ~8 % on Y.

        Not "what each species would need" - what each species would DO if
        you left the drive where it is and changed beam. That is the number
        that decides whether the next run overruns the sample or under-fills
        it.
        """
        self._updating = True
        try:
            for row in range(self.tbl_species.rowCount()):
                parsed = self._species_row(row)
                for col, (kv, target, drift_cm) in enumerate(
                        ((kv_x, scan_half_x, drift_cm_of_axis["X"]),
                         (kv_y, scan_half_y, drift_cm_of_axis["Y"])),
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

