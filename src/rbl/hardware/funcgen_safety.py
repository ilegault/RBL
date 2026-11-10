"""
funcgen_safety.py
Interlock maths and channel/axis mapping for the DG1022Z function generators
that drive the EEL5000 HV amplifiers.

Moved out of funcgen_tab.py so the peak-voltage interlock can be evaluated
(and tested) without constructing a widget — the check must eventually run
on every path to the hardware, not just the one behind the widget's Apply
button (see Phase 8 of the RBL implementation plan).
"""
from rbl.hardware.funcgen_driver import MAX_GEN_VOLTS

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


def peak_status(shape: str, amp_vpp: float, offset_v: float):
    """Classify a channel against the interlock. Returns (status, peak_volts).

    Status is "ok", "warn" (past the advisory threshold — a caller should ask
    before applying) or "block" (past the hard ceiling — Beamline refuses it
    regardless of what any caller decides).

    Lives here rather than in a tab because every screen that can apply a
    channel has to classify it the same way; two copies of this comparison
    would eventually disagree about which tier a given peak falls in.
    """
    peak = channel_peak_volts(shape, amp_vpp, offset_v)
    if peak > PEAK_MAX_VOLTS + 1e-9:
        return "block", peak
    if peak > PEAK_WARN_VOLTS + 1e-9:
        return "warn", peak
    return "ok", peak


def over_limit_channels(params_by_key: dict) -> list[tuple[str, float]]:
    """Return [(key, input_peak)] for channels exceeding the 5 V rail.

    Ordered A1, A2, B1, B2. Used by Apply buttons across the GUI to grey out
    before the operator clicks, naming the offending channels in tooltips.
    Takes {key: ChannelParams} or {key: object/dict} with shape, amp_vpp, offset_v.
    """
    offenders: list[tuple[str, float]] = []
    for key in ("A1", "A2", "B1", "B2"):
        if key not in params_by_key:
            continue
        p = params_by_key[key]
        shape = getattr(p, "shape", None)
        amp = getattr(p, "amp_vpp", None)
        offset = getattr(p, "offset_v", None)
        if shape is None and isinstance(p, dict):
            shape = p.get("shape", "Triangle")
            amp = p.get("amp_vpp", 0.0)
            offset = p.get("offset_v", 0.0)
        peak = channel_peak_volts(shape, amp, offset)
        if peak > PEAK_MAX_VOLTS + 1e-9:
            offenders.append((key, peak))
    return offenders

