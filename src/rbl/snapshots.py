"""
snapshots.py  (rbl/snapshots.py — package-level shared vocabulary)
Frozen dataclasses describing the live state of each beamline subsystem.

WHY THIS EXISTS AT THE PACKAGE ROOT (NOT IN rbl/state/)
--------------------------------------------------------
These types are pure frozen dataclasses with no behaviour and no imports beyond
the standard library.  They are consumed by every layer: hardware workers emit
them, state mixins convert raw readings into them, services log them, and GUI
tabs render them.  Placing them in rbl/state/ created an upward import: hardware
workers (scope_worker, vacuum_worker) had to import from the state layer just to
name their own return type.  Moving them here makes them a shared vocabulary
below all layers.

rbl/state/snapshots.py is kept as a backward-compatible re-export stub so all
existing importers continue to work without change.

Every snapshot carries `connected` so a view can tell "no data because the
instrument isn't hooked up" apart from "no data yet" — before this, a widget
had to infer liveness from whether a label happened to still read the
placeholder text.

Modelled on MotorTab.slit_state (motor_tab.py), which was already this pattern
in miniature: a snapshot dict emitted at poll rate and consumed by a widget in
a different tab. These are that idea generalised, not something new.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AxisSnapshot:
    pos_counts: int
    pos_mm: float
    moving: bool
    enabled: bool
    switches: dict


@dataclass(frozen=True)
class MotorState:
    connected: bool
    zeroed: bool
    axes: dict[str, AxisSnapshot] = field(default_factory=dict)   # "X+" -> ...


@dataclass(frozen=True)
class ChannelSnapshot:
    """One generator channel's READBACK — what the instrument reports it is set to.

    `shape` is the bare head token ("RAMP", "SIN", "DC"); `shape_raw` is the
    untouched `:APPLy?` response it was parsed out of, carried so a log can be
    audited against the wire rather than trusted.

    `load` / `load_ohms` are here because a wrong output-load setting is the
    one fault on this rig that is completely invisible in every other number:
    the EEL5000 input is high-Z, so a generator left on 50 ohm delivers HALF
    the commanded voltage while still reporting the full amplitude in
    `amp_vpp`.  Nothing else in this snapshot would show it.  load_ohms is
    inf for high-Z, NaN when unparseable.
    """
    shape: str
    freq_hz: float
    amp_vpp: float
    offset_v: float
    phase_deg: float
    output_on: bool
    shape_raw: str = ""
    load: str = ""
    load_ohms: float = float("nan")


@dataclass(frozen=True)
class ChannelParams:
    """A channel setpoint intent, e.g. from ChannelPanel.get_params().

    Distinct from ChannelSnapshot (a readback): this is what a caller wants
    to push to the hardware, including the two fields get_state() doesn't
    read back (start_phase_deg, load).
    """
    shape: str
    freq_hz: float
    amp_vpp: float
    offset_v: float
    phase_deg: float
    start_phase_deg: float
    load: str
    output_on: bool


@dataclass(frozen=True)
class FuncGenState:
    connected: dict[str, bool] = field(default_factory=dict)          # "A" -> bool
    timebase: dict[str, str] = field(default_factory=dict)            # "A" -> "INT"/"EXT"
    channels: dict[str, ChannelSnapshot] = field(default_factory=dict)  # "A1" -> ...


@dataclass(frozen=True)
class LogAmpState:
    """Per-slit beam current plus the reconstructed beam position.

    `beam` is a `beam_reconstruction.BeamEstimate` or None (not enough
    geometry/current yet to attempt a reconstruction).

    `volts` holds the raw log-amp voltage each current was derived from, for
    the one screen that shows both. It is ALSO how a consumer tells a paused
    channel from a bad one: a slit missing from `volts` was not in this
    window's scan list (the WAVEFORM profile does not sample the log amps),
    whereas a slit present in `volts` with a NaN current was sampled and read
    outside the log amp's calibrated range. Both show NaN in `currents`, and
    only one of them is a wiring fault worth showing a number for.

    `t` is the stream window's timestamp, on the same monotonic timeline every
    other snapshot from that window carries — a plot appending to a history
    buffer needs the instant the values belong to, not the instant it happened
    to be handed them.
    """
    connected: bool
    currents: dict[str, float] = field(default_factory=dict)   # slit label -> Amps
    volts: dict[str, float] = field(default_factory=dict)      # slit label -> raw V
    t: float = float("nan")
    beam: object = None


@dataclass(frozen=True)
class AmpChannelSnapshot:
    """One amplifier's monitors for one stream window.

    `wave_kv` is the SIGNED deflection waveform for that window, decimated to
    a bounded point count. The scalar fields above are all magnitudes, which
    is enough to answer "how hard is this plate driven?" but says nothing
    about WHEN in the cycle it got there — so a push-pull pair's 0/180
    relationship is invisible in them. That relationship is the thing the
    Overview has to be able to check at a glance, hence the samples.

    Every channel in one window is decimated on the same grid, so index i of
    one channel and index i of another are the same instant to within a scan.
    Comparing two channels sampled any other way would show a phase that is
    an artefact of the resampling.

    `wave_kv` spans `wave_span_s` seconds — NOT a fixed stream window. The
    span is chosen per pair to hold a whole number of cycles of the measured
    drive (`wave_freq_hz`, NaN when no repeating waveform was found, in which
    case the span is just the raw window). A viewer that wants a time axis, or
    wants to say how much of the waveform it is showing, has to be told: the
    samples alone no longer imply 0.1 s.

    `v_live` / `i_live` say whether that monitor was in this window's scan
    list at all. A single-channel profile streams ONE of the eight monitors,
    so the other seven have no reading this window — which is not the same
    thing as reading zero, and a screen has to show the difference (a paused
    readout, not a stale or invented number). Every scalar below is NaN when
    its `*_live` flag is False, but NaN alone cannot distinguish "not
    sampled" from "sampled and unusable", so the flag is carried explicitly.

    `window_kv` / `window_ma` are that window's raw samples at FULL stream
    resolution, already scaled to kV / mA — up to 10 000 points, or None when
    the monitor was not sampled. Distinct from `wave_kv` above, which is a
    decimated couple of cycles chosen for comparing a pair's phase: this is
    every sample, unaligned, for the amplifier tab's scope view, which zooms
    to a few ms and must show what the ADC actually recorded. Held as a
    reference to the stream worker's array, never copied — a copy of eight of
    these per window at 10 Hz would be real work for no gain, since every
    consumer only reads them.
    `dc_kv` / `dc_ma` are the pre-converted window average voltage (in kV)
    and current (in mA) — scaled once in the snapshot layer so GUI screens
    render physical units directly without importing conversion helpers.
    """
    peak_kv: float
    pkpk_kv: float
    rms_kv: float
    rms_ma: float
    raw_v: float
    raw_i: float
    wave_kv: tuple = ()
    wave_span_s: float = float("nan")
    wave_freq_hz: float = float("nan")
    v_live: bool = False
    i_live: bool = False
    window_kv: object = None
    window_ma: object = None
    dc_kv: float = float("nan")
    dc_ma: float = float("nan")


@dataclass(frozen=True)
class VacuumState:
    """Pressure snapshot from both gauge controllers, one poll tick.

    `xgs_readings` and `vgc_readings` hold XgsReading / VgcReading objects
    respectively.  Either list may be empty when the corresponding instrument
    is absent — missing hardware is a normal operating state, not an error.

    `xgs_connected` / `vgc_connected` distinguish "not present" from "present
    but reading an error value".

    `units_xgs` / `units_vgc` carry the unit strings read from each
    instrument at connect time.  They may differ; the log writes both.
    Never convert between units silently.
    """
    timestamp:     float          # time.time() at poll completion
    xgs_readings:  list  = field(default_factory=list)  # list[XgsReading]
    vgc_readings:  list  = field(default_factory=list)  # list[VgcReading]
    xgs_connected: bool  = False
    vgc_connected: bool  = False
    units_xgs:     str   = ""
    units_vgc:     str   = "Torr"   # VGC083 always reports Torr on its display


@dataclass(frozen=True)
class ScopeState:
    """Beam profile snapshot from the TDS 2012 oscilloscope.

    `fwhm_samples` and `fwhm_seconds` are NaN when the FWHM extraction
    failed (e.g. no beam, clipped waveform).  The caller must check
    math.isnan before displaying.

    `volts_downsampled` is a list of floats suitable for plotting — at most
    SCOPE_WAVEFORM_DOWNSAMPLE points (see scope_config.py).  Empty when no
    waveform was acquired this tick.

    `error` is a non-empty string when the worker encountered a protocol or
    timeout error that prevented acquisition; the worker emits a separate
    scope_error signal in that case, but the string is also recorded here so
    a log file has it in-band.
    """
    timestamp:          float
    connected:          bool  = False

    # Idle means the link is UP but no waveform is being transferred - the
    # default, not a fault and not a disconnect.  An idle snapshot is a
    # heartbeat carrying no measurement, so a view should keep showing the
    # last one rather than blanking it.
    idle:               bool  = False

    # Whether the worker is free-running.  False means it takes a shot only
    # when asked.
    continuous:         bool  = False
    channel:            str   = "CH1"
    fwhm_samples:       float = float("nan")
    fwhm_seconds:       float = float("nan")
    xincr:              float = float("nan")
    xzero:              float = float("nan")
    volts_downsampled:  list  = field(default_factory=list)
    preamble:           dict  = field(default_factory=dict)
    error:              str   = ""

    # ---- multi-peak profile -------------------------------------------
    # The sweep crosses the aperture twice per period, so one channel
    # carries two peaks OF THE SAME BEAM.  `peaks` holds one entry per
    # detected peak, left to right, with every position already converted
    # to SECONDS relative to the trigger - a consumer never has to know the
    # sample index or the downsampling factor to draw a marker.
    #
    # Each entry:
    #   index             sample index of the maximum (full resolution)
    #   peak_volts        height above baseline, sign-corrected
    #   half_volts        the half-maximum level used for this peak
    #   left_seconds      interpolated half-maximum crossing, left
    #   right_seconds     interpolated half-maximum crossing, right
    #   centre_seconds    midpoint of the two crossings
    #   fwhm_seconds      right - left; NaN when unresolved
    #   fit_fwhm_seconds  the same peak's width from the Gaussian fit
    #   resolved          False when the peak never falls to half maximum
    #                     before its neighbour - the fit may still measure it
    #   note              why it is unresolved, for display
    #
    # Each entry also carries `levels`: the WIDTH LADDER for that peak, as
    #   {"FWHM": {...}, "FW1/e²": {...}, "FWTM": {...}}
    # with, per level, `seconds`, `mm`, `resolved`, `source`
    # ("measured" / "fit" / "none"), `fit_seconds`, `left_seconds`,
    # `right_seconds`, `volts` and `note`.  Every level is measured
    # independently on THIS trace - none is inferred from another, and
    # nothing is carried between shots, because with quadrupole focusing the
    # profile shape is not the same one twice.
    peaks:                list  = field(default_factory=list)
    n_peaks:              int   = 0
    n_resolved:           int   = 0

    # The ladder's labels, in order, so a consumer can lay out a table
    # without importing the config that chose the levels.
    width_levels:         tuple = ()

    # FWTM / FWHM per axis, from MEASURED widths only.  A true Gaussian gives
    # 1.8226; above that is heavier tails than Gaussian (halo - and the
    # raster planner's droop figures, which invert a normal CDF from the
    # FWHM, are optimistic), below it is a flat-topped or scraped profile.
    # `tail_excess_*` is that ratio over 1.8226 minus one, so +0.14 means
    # "14 % heavier tails than Gaussian".
    #
    # NaN means NOT MEASURED, and there is deliberately no fit fallback: a
    # sum-of-Gaussians fit's own ratio is 1.8226 by construction, so a
    # fit-derived number here would read as "perfectly Gaussian" when it
    # means "nobody measured".  `tail_note` says which it is.
    tail_ratio_x:         float = float("nan")
    tail_ratio_y:         float = float("nan")
    tail_excess_x:        float = float("nan")
    tail_excess_y:        float = float("nan")
    tail_note:            str   = ""

    # The two peaks are the X profile and the Y profile - DIFFERENT
    # DIRECTIONS measured in one trace, not one beam measured twice.  So
    # `mean_fwhm_seconds` is only a rough single-number "beam size" (kept
    # because the Overview wants one scalar), `xy_ratio` is the beam's
    # aspect, and `fwhm_spread` is NOT a fault indicator - a round beam has
    # a ratio near 1, an elliptical one does not, and both are healthy.
    mean_fwhm_seconds:    float = float("nan")
    fwhm_spread:          float = float("nan")
    fwhm_x_seconds:       float = float("nan")
    fwhm_y_seconds:       float = float("nan")
    xy_ratio:             float = float("nan")
    separations_seconds:  list  = field(default_factory=list)

    # Sum-of-Gaussians fit across the whole trace.
    fit_fwhm_seconds:     float = float("nan")
    fit_r_squared:        float = float("nan")

    # Which number `fwhm_seconds` above actually came from: "fit",
    # "half-max", or "none".  Displayed, never guessed at by the consumer.
    fwhm_source:          str   = ""

    # The scope's own PWIDTH measurement - an independent cross-check.
    scope_pwidth_seconds: float = float("nan")

    # Trace context and quality
    baseline_volts:       float = float("nan")
    signal_to_noise:      float = float("nan")
    smooth_window:        int   = 1
    flipped:              bool  = False

    # Raster envelope: the period, in samples, that the analysis treated as
    # raster ripple.  0 means the raw trace was measured directly.
    envelope_samples:     int   = 0

    # How many of the scope's 2500 record points were transferred, and how
    # long that took.  Fewer points means a SHORTER TIME WINDOW at the same
    # sample interval - the transfer is a slice of the record, never a
    # decimation of it - so `points` is also what the plotted window covers.
    points:               int   = 0
    transfer_seconds:     float = float("nan")

    # Off-screen detection.  A clipped peak has no true maximum, so its
    # half-maximum level - and every width from it - is wrong while still
    # looking like a plausible number.
    clipped_low:          bool  = False
    clipped_high:         bool  = False
    clipped_fraction:     float = 0.0

    # Plot-ready traces.  `corrected_downsampled` is smoothed, baseline
    # subtracted and sign corrected - the same signal the half-maximum
    # levels refer to, so markers land where the eye expects them.
    # `xincr_downsampled` is the time step BETWEEN DOWNSAMPLED POINTS, which
    # is not xincr: plotting downsampled data against xincr silently
    # compresses the time axis.
    corrected_downsampled: list  = field(default_factory=list)
    fit_curve_downsampled: list  = field(default_factory=list)
    xincr_downsampled:     float = float("nan")

    # ---- mm scale (BPM fiducial calibration) ---------------------------
    # `mm_per_second` is the BPM's sweep speed across the aperture: the one
    # number that turns any of the widths above into millimetres.  It comes
    # from the fiducial marks (see rbl/hardware/bpm_calibration.py) and is a
    # property of the BPM's own scan, NOT of the oscilloscope - so it stays
    # valid across changes of timebase, volts/div and record length.
    #
    # NaN means uncalibrated, and every *_mm field is NaN with it.  The
    # seconds fields above are always populated: millimetres are an
    # ADDITION to the measurement, never a replacement for it, so a
    # calibration that is later found to be wrong cannot destroy the data
    # that was logged under it.
    mm_per_second:        float = float("nan")
    calibration_name:     str   = ""     # which BPM this scale belongs to
    fwhm_mm:              float = float("nan")   # headline width, in mm
    fwhm_x_mm:            float = float("nan")
    fwhm_y_mm:            float = float("nan")

    # ---- calibration mode ----------------------------------------------
    # True only for a snapshot taken with the BPM controller showing
    # FIDUCIAL MARKS rather than a beam profile.  In that mode the widths
    # above mean nothing (a fiducial mark's width is not a measurement) and
    # only the fields below do.
    cal_mode:             bool  = False
    cal_separation_seconds: float = float("nan")
    cal_mm_per_second:    float = float("nan")
    cal_spacing_mm:       float = float("nan")
    # Peak positions in SECONDS from the START OF THE RECORD (add XZERO to
    # put them in the same frame as the plotted trace).  The calibration
    # itself is a DIFFERENCE of two of these, so XZERO cancels out of it and
    # only a plot ever needs to care.  Each entry:
    #   {"index", "apex_seconds", "volts", "role"}  role ∈ fiducial/trigger/other
    cal_peaks:            list  = field(default_factory=list)
    cal_fiducial_seconds: list  = field(default_factory=list)
    # False means "here is the answer, but the trigger peak was not clearly
    # taller than the marks - look at the trace before saving this".
    cal_confident:        bool  = False
    cal_note:             str   = ""
    cal_source:           str   = ""     # "auto" or "override"


@dataclass(frozen=True)
class AmpState:
    """Every amplifier's monitors for one stream window.

    `t` and `sample_period` describe the window itself: when it ended, and the
    per-sample time step inside `window_kv` / `window_ma`. A consumer
    stitching consecutive windows into one continuous trace needs the real
    step, not a nominal one — rounding the device's actual scan rate is what
    puts a visible seam at every window boundary. `sample_period` is None when
    the producer did not report one.
    """
    connected: bool
    channels: dict[str, AmpChannelSnapshot] = field(default_factory=dict)   # "X+" -> ...
    active_profile: str = ""
    t: float = float("nan")
    sample_period: float | None = None
