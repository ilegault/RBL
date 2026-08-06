"""
snapshots.py
Frozen dataclasses describing the live state of each beamline subsystem.

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
    shape: str
    freq_hz: float
    amp_vpp: float
    offset_v: float
    phase_deg: float
    output_on: bool


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
    channel:            str   = "CH1"
    fwhm_samples:       float = float("nan")
    fwhm_seconds:       float = float("nan")
    xincr:              float = float("nan")
    xzero:              float = float("nan")
    volts_downsampled:  list  = field(default_factory=list)
    preamble:           dict  = field(default_factory=dict)
    error:              str   = ""


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
    sample_period: float = None
