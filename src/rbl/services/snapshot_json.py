"""
snapshot_json.py
One place that decides what a beamline snapshot looks like on disk as JSON.

WHY THIS EXISTS
---------------
The old sidecars were both unreadable and unusable:

1. THEY WERE NOT VALID JSON.  Python's json.dump() writes bare `NaN` for a
   float NaN.  Python reads that back; nothing else does — JavaScript's
   JSON.parse, jq, R's jsonlite and most spreadsheet importers all fail with
   "unexpected character".  Every sidecar with an unconnected scope (fwhm =
   NaN) was born broken.  Here NaN and +/-Inf become `null`, and the writer
   passes allow_nan=False so a survivor is a loud crash, not a silent
   corruption.

2. THEY CARRIED RAW WAVEFORMS.  window_kv / window_ma are up to 10 000 raw
   samples PER CHANNEL PER SNAPSHOT, and wave_kv another 30-120.  Written with
   `default=str` a numpy array does not even serialise as a list — it comes out
   as its repr, a 9 700-character string with newlines and ellipses in it, from
   which the numbers cannot be recovered.  A 30-minute run produced a 20 MB
   sidecar that was mostly unparseable text.  Those buffers exist for the live
   scope view; nothing reads them back from disk.  They are dropped.

3. THE NUMBERS THAT MATTER WERE NOT THERE.  The sidecar recorded the raw
   monitor scalars but not the four numbers the HV Amplifiers tab actually
   puts on screen: what was commanded, what was measured, how far apart they
   are, and how much current is flowing at the drive frequency.  Anyone
   reading a sidecar had to re-derive them, in the right conventions, from
   memory.  `amp_summary()` computes them once, HERE, using the same code
   paths the tab uses — see its docstring for each formula and where it comes
   from.

So: this module makes the file small, valid, and already answered.

CONVENTIONS (the ones that are easy to get wrong)
-------------------------------------------------
* The generator reports amplitude as Vpp (peak-to-PEAK).  The amplifier's
  rating, the calibration ladder and every "kV pk" on the amp tab are PEAK
  amplitudes.  The halving happens once, in `_commanded()`.
* Measured AC amplitude is pkpk_kv / 2, NOT peak_kv.  peak_kv is the signed
  sample of largest magnitude, so on a symmetric triangle it is +Vpk or -Vpk
  depending on nothing but which extreme the window caught.
* Measured AC current is the amplitude AT THE DRIVE FREQUENCY (a single-bin
  DFT), not peak and not mean.  Against this rig's ~1.4 mA rms monitor noise,
  peak biases +181% where the fundamental biases +0.2%; mean is ~0 by
  construction for any symmetric drive.
* On DC all three flip to signed quantities: commanded is the offset, measured
  is the window mean, current is the window mean.

Pure-ish: imports config and math helpers, no Qt, no hardware.
"""
import json
import math

from rbl.config import hardware_config as SC
from rbl.config.calibration_config import CAL_UNCERTAINTY_V
from rbl.hardware.ac_metrics import fundamental
from rbl.hardware.amp_monitor import (
    monitor_to_kv, monitor_to_ma, voltage_status, current_status,
)
from rbl.hardware.funcgen_safety import CHANNEL_ROLE, _AMP_GAIN


# ---------------------------------------------------------------------------
# What never reaches disk
# ---------------------------------------------------------------------------

# Bulk sample buffers.  Every one of these is a live-display artefact: it is
# regenerated ~10x a second, nothing reads it back from a sidecar, and writing
# it is what turned a 30 KB record into a 20 MB one.
BULK_KEYS = frozenset({
    "wave_kv",                  # decimated couple of cycles, Overview phase check
    "window_kv", "window_ma",   # full-rate raw window, amp tab scope view
    "volts_downsampled",        # scope trace
    "corrected_downsampled",    # scope trace, baseline-corrected
    "fit_curve_downsampled",    # scope Gaussian fit curve
})

# Scope fields that describe a trace we are no longer storing.  Keeping them
# invites a reader to think the trace is there.
_SCOPE_TRACE_META = frozenset({"preamble"})

_SHAPE_LABEL = {
    "SIN": "SINE", "SINE": "SINE",
    "SQU": "SQR",  "SQUARE": "SQR",
    "RAMP": "TRI", "TRI": "TRI", "TRIANGLE": "TRI",
    "DC": "DC",
    "PULS": "PULSE", "NOIS": "NOISE",
}


# ---------------------------------------------------------------------------
# Sanitising
# ---------------------------------------------------------------------------

def _finite(x):
    """float -> float, or None when it is NaN / +-Inf.

    `null` is the only thing standard JSON has for "no reading", and every
    consumer already understands it.  Returning the bare NaN is what made the
    old files unparseable outside Python.
    """
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def sanitize(obj, _depth: int = 0):
    """Recursively convert a snapshot into something json.dump can write.

    Four jobs, in order of how much damage each one prevented:

      * drop BULK_KEYS (see above),
      * NaN / Inf -> None,
      * numpy scalars and arrays -> plain Python (an ndarray that reaches
        json.dump with default=str becomes its repr — a lossy string),
      * plain class instances (e.g. BeamEstimate) -> their __dict__.

    Depth is bounded so a cyclic or pathological object cannot hang the
    recorder mid-run.
    """
    if _depth > 12:
        return None

    # numpy scalar (np.float64 etc.) — has .item(), is not a dict/list
    if hasattr(obj, "item") and hasattr(obj, "dtype") and getattr(obj, "shape", None) == ():
        return sanitize(obj.item(), _depth + 1)

    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in BULK_KEYS or k in _SCOPE_TRACE_META:
                continue
            out[str(k)] = sanitize(v, _depth + 1)
        return out

    if isinstance(obj, (list, tuple, set)):
        return [sanitize(v, _depth + 1) for v in obj]

    if type(obj).__name__ == "ndarray":
        # Should be unreachable — every ndarray on a snapshot lives under a
        # BULK_KEY.  If a new one appears, summarise it rather than dumping
        # thousands of points into a file nobody wanted them in.
        try:
            return {"_ndarray": True, "n": int(obj.size)}
        except Exception:
            return None

    if isinstance(obj, bool) or obj is None or isinstance(obj, (int, str)):
        return obj

    if isinstance(obj, float):
        return _finite(obj)

    if hasattr(obj, "__dict__") and not isinstance(obj, type):
        return {k: sanitize(v, _depth + 1) for k, v in vars(obj).items()}

    return str(obj)


def _strict_default(obj):
    """json.dump default= hook.  Deliberately lossy-but-labelled, never a repr.

    The old writer used `default=str`, which is why a numpy array of 10 000
    floats ended up in the file as the string "[ 0.689  0.949 ... ]".  Anything
    reaching here is a bug in sanitize(); say so in-band instead of writing
    something that looks like data.
    """
    return {"_unserializable": type(obj).__name__}


def dump_json(path: str, obj, indent: int = 2) -> bool:
    """Write `obj` to `path` as STRICT JSON.  Returns False on failure.

    allow_nan=False is the point: if a NaN survives sanitize() we want a
    ValueError here — while the offending file is still being written — rather
    than a file that only Python can read.  The write goes to a temp name and
    is renamed, so a crash mid-write cannot leave a truncated sidecar behind.
    """
    tmp = path + ".part"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(sanitize(obj), fh, indent=indent,
                      allow_nan=False, default=_strict_default)
        import os
        os.replace(tmp, path)
        return True
    except (OSError, ValueError, TypeError):
        try:
            import os
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


# ---------------------------------------------------------------------------
# Derived amplifier metrics — the HV Amplifiers tab, as data
# ---------------------------------------------------------------------------

def _commanded(snap):
    """(is_dc, shape_label, freq_hz, amplitude_kv, output_on) from a readback.

    Mirrors AmpTab._commanded exactly.  amplitude_kv is a PEAK amplitude on AC
    and a SIGNED level on DC.  Vpp -> Vpk halving happens here and nowhere else.
    """
    if snap is None:
        return (True, "—", 0.0, float("nan"), False)
    shape = str(getattr(snap, "shape", "") or "").upper()
    head  = shape.split(",")[0].strip()
    freq  = float(getattr(snap, "freq_hz", 0.0) or 0.0)
    gain  = _AMP_GAIN / 1000.0          # generator volts -> output kV
    on    = bool(getattr(snap, "output_on", False))
    if head.startswith("DC") or freq <= 0:
        return (True, "DC", 0.0,
                float(getattr(snap, "offset_v", 0.0) or 0.0) * gain, on)
    label = _SHAPE_LABEL.get(head, head[:4] or "AC")
    amp_kv = abs(float(getattr(snap, "amp_vpp", 0.0) or 0.0)) / 2.0 * gain
    return (False, label, freq, amp_kv, on)


def _measured_kv(ch, is_dc: bool) -> float:
    """Measured output in kV, in the SAME convention as the command.

    AC: pkpk_kv / 2.  Not peak_kv — that is the signed extreme the window
    happened to catch, and comparing it against a positive commanded peak
    flips the deviation to about -200% whenever the negative half wins.
    DC: the window mean, because polarity is part of the answer.
    """
    if ch is None:
        return float("nan")
    if is_dc:
        return monitor_to_kv(getattr(ch, "raw_v", float("nan")))
    pkpk = getattr(ch, "pkpk_kv", float("nan"))
    return (pkpk / 2.0) if (isinstance(pkpk, float) and math.isfinite(pkpk)) \
        else float("nan")


def _measured_ma(ch, is_dc: bool, freq_hz: float, sample_period):
    """(mA, method) for one channel.  Mirrors AmpTab._measured_current.

    method is one of:
      "mean"  DC setpoint — the window mean of the current monitor.
      "pk@f"  amplitude at the drive frequency (single-bin DFT).  The number
              to trust: rejects the monitor's ~1.4 mA rms noise floor.
      "rms*"  fallback when the window holds fewer than 4 whole cycles (below
              ~40 Hz at a 100 ms window).  Cruder, and flagged as such.
      ""      the monitor was not in this window's scan list at all.
    """
    if ch is None or not getattr(ch, "i_live", False):
        return float("nan"), ""
    if is_dc:
        return monitor_to_ma(getattr(ch, "raw_i", float("nan"))), "mean"
    wave = getattr(ch, "window_ma", None)
    if wave is not None and sample_period:
        amp_ma, _phase = fundamental(wave, 1.0 / sample_period, freq_hz)
        if math.isfinite(amp_ma):
            return amp_ma, "pk@f"
    return getattr(ch, "rms_ma", float("nan")), "rms*"


def amp_summary(amp_state, funcgen_state) -> dict:
    """Per-axis commanded-vs-measured summary — the amp tab's table, as data.

    MUST be called with the LIVE dataclasses, before sanitize() drops the raw
    windows: the "pk@f" current is computed FROM window_ma, so it can only be
    derived while that buffer is still attached.  That is the whole reason the
    summary is built at snapshot time instead of by a later reader.

    One entry per axis in SC.AMP_LABELS, each with:

        shape, freq_hz, output_on   what the generator reports being set to
        commanded_kv                Vpp/2 * gain  (AC) or offset*gain (DC)
        measured_kv                 pkpk/2 (AC) or window mean (DC)
        delta_v, delta_pct          measured - commanded
        within_uncertainty          |delta| <= CAL_UNCERTAINTY_V (30 V).
                                    Inside the documented uncertainty budget
                                    is AGREEMENT, not a finding — the same
                                    threshold the calibration tab uses before
                                    it will report a deviation at all.
        current_ma, current_method  see _measured_ma
        rms_ma                      raw RMS, always, as a cross-check
        voltage_status              "ok" within +/-5 kV else "over"
        current_status              "ok" <=20 mA DC, "peak" <=100 mA (legal
                                    only as a <4 ms transient), else "over"
        v_live, i_live              whether each monitor was sampled at all.
                                    A paused readout is not a zero reading.

    `delta_*` and the statuses are None when the output is OFF or a monitor is
    not live: the generator still reports the amplitude it is configured for,
    so comparing against a channel that is driving nothing would flag -100% on
    hardware behaving exactly as intended.
    """
    channels = getattr(amp_state, "channels", None) or {}
    sample_period = getattr(amp_state, "sample_period", None)
    fg_channels = getattr(funcgen_state, "channels", None) or {}

    # funcgen channel key ("A1") -> axis label ("X+")
    by_axis = {}
    for key, snap in fg_channels.items():
        axis = CHANNEL_ROLE.get(key)
        if axis is not None:
            by_axis[axis] = snap

    out = {}
    for axis in SC.AMP_LABELS:
        ch   = channels.get(axis)
        snap = by_axis.get(axis)
        is_dc, label, freq, cmd_kv, out_on = _commanded(snap)

        v_live = bool(getattr(ch, "v_live", False)) if ch is not None else False
        i_live = bool(getattr(ch, "i_live", False)) if ch is not None else False

        meas_kv = _measured_kv(ch, is_dc) if v_live else float("nan")
        cur_ma, method = _measured_ma(ch, is_dc, freq, sample_period)

        entry = {
            "shape":            label,
            "freq_hz":          _finite(freq),
            "output_on":        out_on,
            "mode":             "DC" if is_dc else "AC",
            "commanded_kv":     _finite(cmd_kv),
            "measured_kv":      _finite(meas_kv),
            "pkpk_kv":          _finite(getattr(ch, "pkpk_kv", None)) if ch else None,
            "current_ma":       _finite(cur_ma),
            "current_method":   method,
            "rms_ma":           _finite(getattr(ch, "rms_ma", None)) if ch else None,
            "v_live":           v_live,
            "i_live":           i_live,
            "delta_v":            None,
            "delta_pct":          None,
            "within_uncertainty": None,
            "voltage_status":     None,
            "current_status":     None,
        }

        if v_live and math.isfinite(meas_kv):
            entry["voltage_status"] = voltage_status(meas_kv)
        if i_live and math.isfinite(cur_ma):
            entry["current_status"] = current_status(cur_ma)

        if (out_on and math.isfinite(cmd_kv) and abs(cmd_kv) > 1e-9
                and math.isfinite(meas_kv)):
            d_kv = meas_kv - cmd_kv
            entry["delta_v"]            = _finite(d_kv * 1000.0)
            entry["delta_pct"]          = _finite(100.0 * d_kv / abs(cmd_kv))
            entry["within_uncertainty"] = bool(abs(d_kv) * 1000.0 <= CAL_UNCERTAINTY_V)

        out[axis] = entry
    return out


def funcgen_summary(funcgen_state) -> dict:
    """Per-axis commanded settings — shape, freq, amplitude, output state.

    Keyed by axis label ("X+", "X-", "Y+", "Y-"), not channel key ("A1").
    """
    channels = getattr(funcgen_state, "channels", None) or {}
    out = {}
    for key, snap in channels.items():
        axis = CHANNEL_ROLE.get(key)
        if axis is None:
            continue
        shape = str(getattr(snap, "shape", "") or "").upper()
        head = shape.split(",")[0].strip()
        label = _SHAPE_LABEL.get(head, head[:4] or "?")
        out[axis] = {
            "shape":     label,
            "freq_hz":   _finite(getattr(snap, "freq_hz", 0.0)),
            "amp_vpp":   _finite(getattr(snap, "amp_vpp", 0.0)),
            "offset_v":  _finite(getattr(snap, "offset_v", 0.0)),
            "output_on": bool(getattr(snap, "output_on", False)),
        }
    return out


def current_summary(logamp_state) -> dict:
    """Beam currents per slit jaw in Amps, plus connected flag."""
    currents = getattr(logamp_state, "currents", None) or {}
    return {
        "connected": bool(getattr(logamp_state, "connected", False)),
        "A": {k: _finite(v) for k, v in currents.items()},
    }


def pressure_summary(vacuum_state) -> dict:
    """Gauge pressures from both controllers, keyed by label."""
    out = {}
    for r in getattr(vacuum_state, "xgs_readings", []):
        ch = getattr(r, "channel", None)
        label = getattr(ch, "label", None) or str(ch)
        p = getattr(r, "pressure", None)
        state = getattr(r, "state", "")
        out[label] = _finite(p) if p is not None else state
    for r in getattr(vacuum_state, "vgc_readings", []):
        label = str(getattr(r, "channel", ""))
        p = getattr(r, "pressure", None)
        state = getattr(r, "state", "")
        out[label] = _finite(p) if p is not None else state
    return out


def scope_summary(scope_state) -> dict:
    """Beam profile essentials — just connected, FWHM, and peak count."""
    return {
        "connected":    bool(getattr(scope_state, "connected", False)),
        "fwhm_seconds": _finite(getattr(scope_state, "fwhm_seconds", None)),
        "fwhm_source":  getattr(scope_state, "fwhm_source", "") or None,
        "n_peaks":      getattr(scope_state, "n_peaks", 0),
    }


def slit_summary(motor_state) -> dict:
    """Aperture width/height in mm, plus each jaw, from a MotorState.

    The four jaws are logged individually elsewhere; what an analysis actually
    wants is the OPENING — the sum of the two opposing jaws — because that is
    what the beam saw.  Deriving it here means no reader has to know the sign
    convention of the jaw positions.
    """
    axes = getattr(motor_state, "axes", None) or {}

    def _pos(label):
        ax = axes.get(label)
        if ax is None:
            return None
        return _finite(getattr(ax, "pos_mm", None))

    xp, xm = _pos("X+"), _pos("X-")
    yp, ym = _pos("Y+"), _pos("Y-")
    width  = (xp + xm) if (xp is not None and xm is not None) else None
    height = (yp + ym) if (yp is not None and ym is not None) else None
    return {
        "aperture_w_mm": width,
        "aperture_h_mm": height,
        "jaws_mm": {"X+": xp, "X-": xm, "Y+": yp, "Y-": ym},
        "zeroed":    bool(getattr(motor_state, "zeroed", False)),
        "connected": bool(getattr(motor_state, "connected", False)),
    }


# ---------------------------------------------------------------------------
# Dataclass -> lean dict
# ---------------------------------------------------------------------------

def asdict_lean(obj, _depth: int = 0):
    """Like dataclasses.asdict(), except it never copies a bulk buffer.

    dataclasses.asdict() deep-copies every field it walks.  On an AmpState
    that is eight ndarrays of up to 10 000 floats, copied on every stream
    window at 10 Hz, purely so the very next step could throw them away.  This
    walks the fields itself and skips BULK_KEYS BEFORE recursing, so the big
    arrays are never touched, and sanitises (NaN -> None) in the same pass.
    """
    import dataclasses

    if _depth > 12:
        return None

    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        out = {}
        for f in dataclasses.fields(obj):
            if f.name in BULK_KEYS or f.name in _SCOPE_TRACE_META:
                continue
            out[f.name] = asdict_lean(getattr(obj, f.name), _depth + 1)
        return out

    if isinstance(obj, dict):
        return {str(k): asdict_lean(v, _depth + 1)
                for k, v in obj.items()
                if k not in BULK_KEYS and k not in _SCOPE_TRACE_META}

    if isinstance(obj, (list, tuple, set)):
        return [asdict_lean(v, _depth + 1) for v in obj]

    return sanitize(obj, _depth)
