"""
bpm_calibration.py
Milliseconds -> millimetres, from the BPM's own fiducial marks.

WHAT THIS SOLVES
----------------
The oscilloscope measures a beam profile in TIME.  Nothing in the trace says
how wide the beam is in millimetres until something of a known real-space
size appears in the same trace at the same sweep speed.  That something is
the BPM's pair of calibration (fiducial) marks: on an NEC BPM80 they are
6 cm apart in real space, a mechanical fact of the head.

So the whole calibration is one division:

    mm per second = spacing_mm / (time between the two fiducial peaks)

and the beam's FWHM in millimetres is its FWHM in seconds times that.

WHY THERE ARE NO "DIVISIONS" ANYWHERE IN THIS MODULE
----------------------------------------------------
The written procedure has the operator turn the seconds/div knob until the
fiducial peaks sit 6 divisions apart, then read widths off the screen in
divisions.  That step exists because a human reading a screen has no other
ruler.  The app has a better one: XINCR from the waveform preamble gives the
real time between samples, so the separation is known to a fraction of a
sample without touching the timebase at all.

That also makes the result DURABLE.  mm/s is a property of how fast the BPM
sweeps its wire across the aperture - it does not change when someone
afterwards changes the scope's seconds/div, volts/div, trigger or record
length.  A calibration expressed in "mm per division" would be void the
moment the timebase moved.  This one is not.

THE THIRD PEAK
--------------
A fiducial trace carries THREE peaks, not two: the X mark, the Y mark, and
the scope's trigger peak, which the procedure explicitly warns about.  The
trigger is normally the tallest thing on screen, so that is how it is
identified - but "normally" is not "always", so:

  * the choice is REPORTED, never silent: which peak was treated as the
    trigger and which two as fiducials come back in the result, and the tab
    draws them on the trace;
  * confidence is checked.  If the tallest peak does not stand clear of the
    fiducials by BPM_CAL_TRIGGER_MARGIN, the result comes back
    `confident=False` with a note, and the app asks before using it;
  * the operator can override the pair outright.

SUB-SAMPLE PEAK POSITION
------------------------
Peak positions are refined by fitting a parabola through the apex sample and
its two neighbours.  A fiducial mark a few samples wide, located to the
nearest whole sample, carries up to half a sample of position error at each
end - and the separation is a DIFFERENCE of two positions, so those errors
add.  The parabola costs three multiplies and removes it.

Half-maximum centres are deliberately NOT used here.  They are the right
centre for a beam profile, whose width is the measurement; a fiducial mark's
width means nothing, and its half-maximum centre wanders with any asymmetry
in the mark.  The apex is the mark.

NO Qt, NO SCOPE, NO CONFIG SIDE EFFECTS.  Everything here is a plain
function over a list of floats, so it is testable without hardware.
"""
import logging
import math

from rbl.hardware.profile_fwhm import FwhmError, analyse_profile

log = logging.getLogger(__name__)


class CalibrationError(ValueError):
    """Raised when a fiducial trace cannot yield a mm/s scale."""


# ---------------------------------------------------------------------------
# Sub-sample apex
# ---------------------------------------------------------------------------

def refine_apex(volts: list, i: int) -> float:
    """Fractional sample index of the peak near integer index *i*.

    Parabola through (i-1, i, i+1).  Returns *i* unchanged at the edges of
    the record or when the three samples are collinear (a flat top), where
    a parabola has no vertex to offer.
    """
    n = len(volts)
    if i <= 0 or i >= n - 1:
        return float(i)
    y0, y1, y2 = volts[i - 1], volts[i], volts[i + 1]
    denom = y0 - 2.0 * y1 + y2
    if denom == 0:
        return float(i)
    delta = 0.5 * (y0 - y2) / denom
    # A vertex further than one sample away means the apex is not really
    # here - refuse rather than invent a position.
    if not (-1.0 <= delta <= 1.0):
        return float(i)
    return float(i) + delta


# ---------------------------------------------------------------------------
# Which peaks are the fiducials
# ---------------------------------------------------------------------------

def select_fiducial_peaks(heights: list, *, rule: str = "tallest",
                          margin: float = 0.15,
                          override: tuple = None) -> dict:
    """Decide which peaks are the two calibration marks.

    Parameters
    ----------
    heights  : peak heights, left to right (one entry per detected peak)
    rule     : "tallest" drops the highest peak as the trigger; "first" and
               "last" drop by position instead
    margin   : how far above the tallest fiducial the trigger must stand for
               the auto-pick to be reported as confident
    override : (i, j) peak indices chosen by the operator.  Honoured as
               given; confidence is not questioned, because a person looked.

    Returns
    -------
    dict with:
        pair       (i, j) indices into *heights*, left to right
        trigger    index treated as the trigger peak, or None
        confident  bool - False means "shown, not trusted; please confirm"
        note       one sentence explaining the choice or the doubt
        source     "override" or "auto"

    Raises
    ------
    CalibrationError
        When there are not two peaks to choose from at all.
    """
    n = len(heights)

    if override is not None:
        i, j = int(override[0]), int(override[1])
        if not (0 <= i < n and 0 <= j < n) or i == j:
            raise CalibrationError(
                f"chosen fiducial peaks {override} are not two of the "
                f"{n} peaks found on this trace")
        i, j = min(i, j), max(i, j)
        others = [k for k in range(n) if k not in (i, j)]
        trigger = max(others, key=lambda k: heights[k]) if others else None
        return {"pair": (i, j), "trigger": trigger, "confident": True,
                "note": "fiducial peaks chosen by the operator",
                "source": "override"}

    if n < 2:
        raise CalibrationError(
            f"only {n} peak(s) on this trace - the fiducial marks need two. "
            f"Check the BPM controller's output selector is on fiducial "
            f"marks, and that the trace is not clipped or off screen.")

    if n == 2:
        # Two peaks and no trigger visible.  It is a usable pair, but the
        # app cannot verify that the trigger is off screen rather than that
        # one fiducial went missing, so it says so.
        return {"pair": (0, 1), "trigger": None, "confident": False,
                "note": ("only two peaks found, so there is no trigger peak "
                         "to tell apart - check that both marks are the "
                         "calibration peaks and neither is the trigger"),
                "source": "auto"}

    if rule == "first":
        trigger = 0
    elif rule == "last":
        trigger = n - 1
    else:
        trigger = max(range(n), key=lambda k: heights[k])

    rest = [k for k in range(n) if k != trigger]
    # More furniture than three peaks: keep the two tallest survivors, which
    # is what the fiducials are relative to anything else on the trace.
    if len(rest) > 2:
        rest = sorted(sorted(rest, key=lambda k: heights[k],
                             reverse=True)[:2])
    i, j = rest[0], rest[1]

    tallest_fid = max(heights[i], heights[j])
    trig_h = heights[trigger]
    confident = True
    note = (f"peak {trigger + 1} of {n} is the tallest and is treated as the "
            f"trigger; peaks {i + 1} and {j + 1} are the fiducial marks")
    if tallest_fid <= 0 or trig_h < tallest_fid * (1.0 + margin):
        confident = False
        excess = (trig_h / tallest_fid - 1.0) * 100 if tallest_fid > 0 else 0.0
        note = (f"peak {trigger + 1} is only {excess:.0f} % taller than the "
                f"tallest of the other two, so which one is the trigger is "
                f"not clear from height alone - confirm the marked peaks")
    return {"pair": (i, j), "trigger": trigger, "confident": confident,
            "note": note, "source": "auto"}


# ---------------------------------------------------------------------------
# The division itself
# ---------------------------------------------------------------------------

def mm_per_second(separation_seconds: float, spacing_mm: float) -> float:
    """Sweep speed in mm/s from a fiducial separation.

    This is the entire calibration.  Everything else in this module exists
    to get an honest *separation_seconds* to hand it.
    """
    if not (separation_seconds == separation_seconds) or separation_seconds <= 0:
        raise CalibrationError(
            f"fiducial separation must be positive, got {separation_seconds!r}")
    if not (spacing_mm == spacing_mm) or spacing_mm <= 0:
        raise CalibrationError(
            f"fiducial spacing must be positive, got {spacing_mm!r}")
    return spacing_mm / separation_seconds


def seconds_to_mm(seconds: float, mm_per_s: float) -> float:
    """A width in seconds as a width in millimetres.  NaN in, NaN out."""
    if seconds is None or mm_per_s is None:
        return math.nan
    if not (seconds == seconds) or not (mm_per_s == mm_per_s) or mm_per_s <= 0:
        return math.nan
    return seconds * mm_per_s


# ---------------------------------------------------------------------------
# Full pass over a fiducial trace
# ---------------------------------------------------------------------------

def analyse_fiducials(volts: list, xincr: float, *,
                      spacing_mm: float = 60.0,
                      max_peaks: int = 3,
                      smooth: int = 5,
                      peak_threshold: float = 0.12,
                      min_sep_frac: float = 0.01,
                      polarity: str = "auto",
                      quantum: float = 0.0,
                      trigger_rule: str = "tallest",
                      trigger_margin: float = 0.15,
                      override: tuple = None) -> dict:
    """Measure a fiducial trace and return the mm/s scale it implies.

    The peak FINDING is `analyse_profile` - the same code that finds beam
    peaks, with the raster envelope off (fiducial marks are not rastered)
    and less smoothing.  What is different here is everything after: the
    marks' WIDTHS are meaningless, only their POSITIONS matter, and one of
    the peaks is the trigger and must be thrown away.

    Returns
    -------
    dict with:
        mm_per_second, mm_per_ms, ms_per_mm
        separation_seconds, separation_ms, spacing_mm
        fiducial_indices    (i, j) into the peak list
        fiducial_seconds    [t_i, t_j] from the START OF THE RECORD (add
                            XZERO to compare against a plotted trace; it
                            cancels out of the separation, so the
                            calibration never needs it)
        trigger_index       peak treated as the trigger, or None
        confident, note, source
        peaks               every peak found, with apex_index/apex_seconds/
                            volts/role ("fiducial"/"trigger")
        n_peaks, signal_to_noise, clipping, corrected, baseline, flipped

    Raises
    ------
    CalibrationError
        For anything that makes the number meaningless: no measurable
        peaks, fewer than two candidates, a zero separation.
    """
    if not (xincr == xincr) or xincr <= 0:
        raise CalibrationError(f"xincr must be positive, got {xincr!r}")

    try:
        result = analyse_profile(
            volts, xincr,
            polarity         = polarity,
            smooth           = smooth,
            max_peaks        = max(2, int(max_peaks)),
            peak_threshold   = peak_threshold,
            min_sep_frac     = min_sep_frac,
            envelope_samples = 0,          # fiducial marks are never rastered
            quantum          = quantum,
            axis_labels      = (),         # roles are assigned below, not here
        )
    except FwhmError as exc:
        raise CalibrationError(f"no measurable peaks on this trace: {exc}") from exc

    corrected = result["corrected"]
    heights   = [p["volts"] for p in result["peaks"]]
    choice    = select_fiducial_peaks(heights, rule=trigger_rule,
                                      margin=trigger_margin,
                                      override=override)
    i, j      = choice["pair"]

    apexes = [refine_apex(corrected, p["index"]) for p in result["peaks"]]
    sep_samples = apexes[j] - apexes[i]
    sep_seconds = sep_samples * xincr
    if sep_seconds <= 0:
        raise CalibrationError(
            "the two fiducial peaks resolved to the same position - "
            "nothing to divide by")

    mmps = mm_per_second(sep_seconds, spacing_mm)

    peaks = []
    for k, p in enumerate(result["peaks"]):
        peaks.append({
            "index":        p["index"],
            "apex_index":   apexes[k],
            "apex_seconds": apexes[k] * xincr,
            "volts":        p["volts"],
            "role":         ("fiducial" if k in (i, j)
                             else "trigger" if k == choice["trigger"]
                             else "other"),
        })

    log.info("bpm_calibration: %d peaks, fiducials %s, separation %.4g ms "
             "-> %.4g mm/s (%s%s)", len(peaks), (i + 1, j + 1),
             sep_seconds * 1e3, mmps, choice["source"],
             "" if choice["confident"] else ", UNCONFIRMED")

    return {
        "mm_per_second":      mmps,
        "mm_per_ms":          mmps * 1e-3,
        "ms_per_mm":          (1e3 / mmps) if mmps > 0 else math.nan,
        "separation_seconds": sep_seconds,
        "separation_ms":      sep_seconds * 1e3,
        "spacing_mm":         spacing_mm,
        "fiducial_indices":   (i, j),
        "fiducial_seconds":   [apexes[i] * xincr, apexes[j] * xincr],
        "trigger_index":      choice["trigger"],
        "confident":          choice["confident"],
        "note":               choice["note"],
        "source":             choice["source"],
        "peaks":              peaks,
        "n_peaks":            len(peaks),
        "signal_to_noise":    result["signal_to_noise"],
        "clipping":           result["clipping"],
        "corrected":          corrected,
        "baseline":           result["baseline"],
        "flipped":            result["flipped"],
    }


# ---------------------------------------------------------------------------
# Persistence: one entry per BPM
# ---------------------------------------------------------------------------
#
# Kept here rather than in the tab so the format has exactly one definition
# and a test can pin it.  The store lives in the same JSON file as every
# other saved setting (rbl.config.persistence).
#
#     cfg["bpm_calibrations"] = {
#         "BPM 2": {mm_per_second, separation_seconds, spacing_mm,
#                   confident, source, note, xincr, channel, saved_iso},
#         ...
#     }
#     cfg["bpm_calibration_active"] = "BPM 2"
#
# The ACTIVE name is stored separately from the entries so that switching
# which BPM the profiler is scaled by does not rewrite any calibration.

CAL_STORE_KEY  = "bpm_calibrations"
CAL_ACTIVE_KEY = "bpm_calibration_active"


def calibration_entry(name: str, result: dict, *, channel: str = "",
                      xincr: float = math.nan, saved_iso: str = "") -> dict:
    """One saved calibration, from an analyse_fiducials() result."""
    return {
        "name":               name,
        "mm_per_second":      result["mm_per_second"],
        "mm_per_ms":          result["mm_per_ms"],
        "separation_seconds": result["separation_seconds"],
        "spacing_mm":         result["spacing_mm"],
        "fiducial_indices":   list(result["fiducial_indices"]),
        "trigger_index":      result["trigger_index"],
        "confident":          result["confident"],
        "source":             result["source"],
        "note":               result["note"],
        "channel":            channel,
        "xincr":              xincr,
        "saved_iso":          saved_iso,
    }


def load_calibrations(cfg: dict) -> tuple:
    """(entries_by_name, active_name) out of a loaded config dict."""
    entries = cfg.get(CAL_STORE_KEY) or {}
    if not isinstance(entries, dict):
        entries = {}
    active = cfg.get(CAL_ACTIVE_KEY) or ""
    if active not in entries:
        active = ""
    return entries, active


def active_mm_per_second(cfg: dict) -> float:
    """The scale currently in force, or NaN when nothing is calibrated."""
    entries, active = load_calibrations(cfg)
    entry = entries.get(active) or {}
    value = entry.get("mm_per_second", math.nan)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return math.nan
    return value if value == value and value > 0 else math.nan
