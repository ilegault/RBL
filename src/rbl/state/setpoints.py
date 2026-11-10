"""
setpoints.py
FuncGenSetpoints: what the operator has ASKED the four generator channels to
do, as one shared model.

Distinct from ChannelSnapshot (snapshots.py), which is what the instruments
report BACK. A setpoint exists before anything is applied and survives a
generator being disconnected; a snapshot only exists while a poll is landing.

This is a model rather than four spinboxes because two screens now edit the
same four channels: the Function Generators tab (per channel, every field) and
the Overview tab (per axis, amplitude + frequency + output). Without a shared
model the two would drift apart and an Apply from either screen would silently
overwrite whatever the other had typed. Both are views on this object; neither
owns the value.

The shared model also tracks whether an axis's offsets are linked (minus plate
held equal and opposite to plus plate). Linking lives here rather than in a
widget so that the Overview, Function Generators tab, and Raster Planner all
read one source of truth without constructing widgets.

Holds no driver and sends nothing — applying a setpoint is Beamline's job, and
Beamline is where the +/-5 V interlock lives.
"""
from dataclasses import replace

from PySide6.QtCore import QObject, Signal

from rbl.hardware.funcgen_safety import CHANNEL_ROLE
from rbl.state.snapshots import ChannelParams

# The two channels driving each axis, '+' first. Both channels of an axis MUST
# live on the same physical generator — only :PHASe:SYNChronize (same unit)
# gives deterministic alignment, so a cross-unit pair could never hold phase.
AXIS_CHANNELS = {"X": ("A1", "A2"), "Y": ("B1", "B2")}
AXIS_GENERATOR = {"X": "A", "Y": "B"}
CHANNEL_AXIS = {ch: axis for axis, channels in AXIS_CHANNELS.items() for ch in channels}

# Push-pull differential drive: with the '+' plate at 0 deg and the '-' plate at
# 180 deg, the pair reaches its full differential swing with no DC offset on
# either plate. This is a per-CHANNEL property of the pair, never something an
# axis-level edit is allowed to touch — see update_axis().
START_PHASE_DEFAULTS = {"A1": 0.0, "A2": 180.0, "B1": 0.0, "B2": 180.0}

# Triangle, because a raster wants constant sweep velocity across the target;
# a sine would linger at the turn-arounds and thin the deposition in the middle.
DEFAULT_SHAPE   = "Triangle"
DEFAULT_FREQ_HZ = 10.0
# High-Z: the EEL5000 input is high-impedance, and a wrong load setting halves
# the voltage the amplifier actually receives.
DEFAULT_LOAD    = "INFinity"


def default_params(key: str) -> ChannelParams:
    """The safe starting setpoint for one channel: no output, no volts."""
    phase = START_PHASE_DEFAULTS[key]
    return ChannelParams(
        shape=DEFAULT_SHAPE, freq_hz=DEFAULT_FREQ_HZ, amp_vpp=0.0,
        offset_v=0.0, phase_deg=phase, start_phase_deg=phase,
        load=DEFAULT_LOAD, output_on=False,
    )


class FuncGenSetpoints(QObject):
    """The four channels' commanded parameters, editable per channel or per axis."""

    changed = Signal(str, object)   # channel key, ChannelParams
    link_changed = Signal(str, bool)  # axis ("X" or "Y"), linked

    def __init__(self, parent=None):
        super().__init__(parent)
        self._params = {key: default_params(key) for key in CHANNEL_ROLE}
        self._linked = {axis: True for axis in AXIS_CHANNELS}
        self._linking_in_progress = False

    # ---- Read ----------------------------------------------------------------

    def get(self, key: str) -> ChannelParams:
        return self._params[key]

    def all(self) -> dict:
        return dict(self._params)

    def axis_params(self, axis: str) -> ChannelParams:
        """The axis's representative setpoint — its '+' channel.

        Amplitude, frequency and output are held identical across a pair by
        update_axis(), so either channel answers for the axis; the '+' one is
        picked so the answer is stable rather than whichever was written last.
        """
        return self._params[AXIS_CHANNELS[axis][0]]

    def axis_matched(self, axis: str) -> bool:
        """Do this axis's two channels still agree on amplitude and frequency?

        They can diverge: the Function Generators tab edits one channel at a
        time, which is a legitimate thing to do (trimming one plate). The
        Overview says so rather than showing one of the two as if it were both.
        """
        plus, minus = (self._params[k] for k in AXIS_CHANNELS[axis])
        return (plus.amp_vpp == minus.amp_vpp
                and plus.freq_hz == minus.freq_hz
                and plus.output_on == minus.output_on)

    def axis_linked(self, axis: str) -> bool:
        """Whether this axis's +/- plates are held equal and opposite in offset."""
        return self._linked[axis]

    # ---- Write ---------------------------------------------------------------

    def update(self, key: str, **fields) -> bool:
        """Change one channel's setpoint. Returns True if anything actually moved.

        Emitting only on a real change is what keeps the two editing screens
        from ping-ponging a value between each other on every keystroke.
        """
        current = self._params[key]
        updated = replace(current, **fields)
        if updated == current:
            return False
        self._params[key] = updated
        self.changed.emit(key, updated)

        if "offset_v" in fields and not self._linking_in_progress:
            axis = CHANNEL_AXIS.get(key)
            if axis and self.axis_linked(axis):
                plus_key, minus_key = AXIS_CHANNELS[axis]
                plus = self._params[plus_key].offset_v
                minus = self._params[minus_key].offset_v
                if abs(plus + minus) > 1e-9:
                    self.set_linked(axis, False)

        return True

    def set_linked(self, axis: str, linked: bool) -> None:
        """Set the linked state for an axis.

        Linking keeps the '+' channel's offset and sets the '-' channel to its
        negative. Unlinking changes no offset. Emits link_changed only on a real
        change.
        """
        if self._linked[axis] == linked:
            return
        self._linked[axis] = linked
        if linked:
            plus_key, minus_key = AXIS_CHANNELS[axis]
            plus_offset = self._params[plus_key].offset_v
            self.update(minus_key, offset_v=-plus_offset)
        self.link_changed.emit(axis, linked)

    def set_axis_offset(self, axis: str, offset_v: float) -> None:
        """Set the differential offset for an axis (+ to offset_v, - to -offset_v)."""
        plus_key, minus_key = AXIS_CHANNELS[axis]
        self._linking_in_progress = True
        try:
            self.update(plus_key, offset_v=offset_v)
            self.update(minus_key, offset_v=-offset_v)
        finally:
            self._linking_in_progress = False

    def send_plan(self, axis: str, amp_vpp: float, offset_v: float, freq_hz: float) -> None:
        """Apply a raster plan to an axis.

        Sets both channels' amp_vpp, freq_hz and shape='Triangle', sets offsets
        equal and opposite (+ to +offset_v, - to -offset_v), and links the axis.
        Leaves output_on, phase_deg, start_phase_deg and load untouched.
        """
        plus_key, minus_key = AXIS_CHANNELS[axis]
        self._linking_in_progress = True
        try:
            self.update(
                plus_key,
                amp_vpp=amp_vpp,
                freq_hz=freq_hz,
                shape=DEFAULT_SHAPE,
                offset_v=offset_v,
            )
            self.update(
                minus_key,
                amp_vpp=amp_vpp,
                freq_hz=freq_hz,
                shape=DEFAULT_SHAPE,
                offset_v=-offset_v,
            )
        finally:
            self._linking_in_progress = False
        self.set_linked(axis, True)

    def update_axis(self, axis: str, **fields) -> bool:
        """Change both channels of an axis together.

        Phase is dropped rather than applied: the 0 deg / 180 deg push-pull
        relationship is the whole reason the pair exists, so an axis-level edit
        must never be able to flatten both channels onto the same phase.

        offset_v is dropped as well: a common-mode offset moves nothing (equal
        offset on both plates creates no net deflection field), so an
        axis-level edit must never touch offset_v. Use set_axis_offset() for
        differential offset.
        """
        fields.pop("phase_deg", None)
        fields.pop("start_phase_deg", None)
        fields.pop("offset_v", None)
        changed = False
        for key in AXIS_CHANNELS[axis]:
            changed = self.update(key, **fields) or changed
        return changed
