"""
snapshots.py
Frozen dataclasses describing the live state of each beamline subsystem.

Every snapshot carries `connected` so a view can tell "no data because the
instrument isn't hooked up" apart from "no data yet" — before this, a widget
had to infer liveness from whether a label happened to still read the
placeholder text.

Modelled on MotorTab.jaw_state (motor_tab.py), which was already this pattern
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
    """Per-jaw beam current plus the reconstructed beam position.

    `beam` is a `beam_reconstruction.BeamEstimate` or None (not enough
    geometry/current yet to attempt a reconstruction).
    """
    connected: bool
    currents: dict[str, float] = field(default_factory=dict)   # jaw label -> Amps
    beam: object = None


@dataclass(frozen=True)
class AmpChannelSnapshot:
    peak_kv: float
    pkpk_kv: float
    rms_kv: float
    rms_ma: float
    raw_v: float
    raw_i: float


@dataclass(frozen=True)
class AmpState:
    connected: bool
    channels: dict[str, AmpChannelSnapshot] = field(default_factory=dict)   # "X+" -> ...
    active_profile: str = ""
