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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._params = {key: default_params(key) for key in CHANNEL_ROLE}

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
        return True

    def update_axis(self, axis: str, **fields) -> bool:
        """Change both channels of an axis together.

        Phase is dropped rather than applied: the 0 deg / 180 deg push-pull
        relationship is the whole reason the pair exists, so an axis-level edit
        must never be able to flatten both channels onto the same phase.
        """
        fields.pop("phase_deg", None)
        fields.pop("start_phase_deg", None)
        changed = False
        for key in AXIS_CHANNELS[axis]:
            changed = self.update(key, **fields) or changed
        return changed
