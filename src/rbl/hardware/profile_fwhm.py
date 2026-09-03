"""
profile_fwhm.py
Gaussian FWHM extraction from a 1-D voltage waveform (beam profile).

ALGORITHM
---------
1. Subtract the baseline (median of the outer 10 % of samples on each side).
2. Find the peak value after baseline subtraction.
3. Locate the half-maximum crossings on each side of the peak by linear
   interpolation between the two samples that straddle the half-maximum.
4. FWHM = right_crossing - left_crossing, in the same units as the
   time-axis (seconds when xincr is in seconds).
5. Convert to physical width using the conversion factor the caller supplies
   (e.g. mm/s from beam velocity, or mm/V from a calibration).

CONVENTIONS
-----------
- All inputs are plain Python lists or tuples — no numpy required for the
  core logic.  scipy is imported optionally only for the Gaussian fit path.
- Voltages must already be baseline-subtracted at the call site, OR the
  caller can pass subtract_baseline=True (the default) to let this module
  do it.
- A FwhmError is raised (never silently returned) for any condition that
  would make the result meaningless:
    * fewer than 4 samples
    * non-positive peak
    * half-maximum crossing not found on either side
- None is never returned — callers should catch FwhmError and treat it as
  "profile not usable" for this frame.
"""
import logging
import math

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class FwhmError(ValueError):
    """Raised when the waveform cannot yield a valid FWHM estimate."""


# ---------------------------------------------------------------------------
# Baseline estimation
# ---------------------------------------------------------------------------

_BASELINE_FRACTION = 0.10   # fraction of samples used per side


def estimate_baseline(volts: list[float]) -> float:
    """Return the median of the outer 10 % of samples (5 % each side).

    Uses at least 1 sample per side even for very short waveforms.
    """
    n    = len(volts)
    wing = max(1, int(n * _BASELINE_FRACTION / 2))
    edge = volts[:wing] + volts[-wing:]
    s    = sorted(edge)
    mid  = len(s) // 2
    if len(s) % 2 == 1:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


# ---------------------------------------------------------------------------
# Half-maximum crossing finder
# ---------------------------------------------------------------------------

def _find_crossing(volts: list[float], half: float,
                   start: int, step: int) -> float:
    """Walk from *start* in direction *step* (+1 right, -1 left) and return
    the interpolated index where volts crosses *half*.

    Returns the fractional sample index of the crossing.

    Raises FwhmError if the crossing is not found before an edge.
    """
    n = len(volts)
    i = start
    while 0 < i + step < n - 1 or (step == 1 and i + step == n - 1) or \
          (step == -1 and i + step == 0):
        j = i + step
        if j < 0 or j >= n:
            break
        vi, vj = volts[i], volts[j]
        # Crossing: one sample above half, the next below (or exactly on)
        if (vi >= half >= vj) or (vi <= half <= vj):
            if vi == vj:
                return float(i)
            # Linear interpolation: crossing = i + (j-i) * fraction_from_i_to_j
            frac = (half - vi) / (vj - vi)
            return i + (j - i) * frac
        i = j
    raise FwhmError(
        f"Half-maximum crossing not found walking from index {start} "
        f"in direction {step:+d} (half={half:.4g}, n={n})"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_fwhm_samples(volts: list[float], *,
                         subtract_baseline: bool = True) -> float:
    """Return the FWHM of the beam profile in sample units.

    Parameters
    ----------
    volts            : list of voltage values (one per sample)
    subtract_baseline: if True, subtract the baseline estimate first.

    Returns
    -------
    float
        FWHM in sample-index units (multiply by xincr to get seconds).

    Raises
    ------
    FwhmError
        If the waveform has fewer than 4 samples, the baseline-subtracted
        peak is non-positive, or a half-maximum crossing cannot be found.
    """
    n = len(volts)
    if n < 4:
        raise FwhmError(f"Waveform too short: {n} samples (minimum 4)")

    if subtract_baseline:
        bl    = estimate_baseline(volts)
        volts = [v - bl for v in volts]

    peak_idx = max(range(n), key=lambda i: volts[i])
    peak_val = volts[peak_idx]

    if peak_val <= 0.0:
        raise FwhmError(
            f"Baseline-subtracted peak is non-positive ({peak_val:.4g}); "
            "cannot compute FWHM"
        )

    half = peak_val / 2.0

    left_idx  = _find_crossing(volts, half, peak_idx, step=-1)
    right_idx = _find_crossing(volts, half, peak_idx, step=+1)

    fwhm_samples = right_idx - left_idx
    if fwhm_samples <= 0:
        raise FwhmError(
            f"FWHM is non-positive ({fwhm_samples:.4g} samples); "
            "waveform may be symmetric around the edge"
        )

    log.debug(
        "profile_fwhm: peak=%d (%.4g V), half=%.4g, "
        "left=%.3f, right=%.3f, fwhm=%.3f samples",
        peak_idx, peak_val, half, left_idx, right_idx, fwhm_samples,
    )
    return float(fwhm_samples)


def compute_fwhm_seconds(volts: list[float], xincr: float, *,
                         subtract_baseline: bool = True) -> float:
    """FWHM in seconds.

    Parameters
    ----------
    volts  : voltage samples
    xincr  : seconds per sample (from preamble XINCR field)

    Returns
    -------
    float  FWHM in seconds.

    Raises FwhmError on any failure.
    """
    if xincr <= 0:
        raise FwhmError(f"xincr must be positive, got {xincr!r}")
    fwhm_s = compute_fwhm_samples(volts, subtract_baseline=subtract_baseline)
    return fwhm_s * xincr


def gaussian_fwhm_fit(volts: list[float], xincr: float = 1.0, *,
                      subtract_baseline: bool = True) -> dict:
    """Fit a Gaussian to the profile and return FWHM + fit quality.

    Uses scipy.optimize.curve_fit.  Falls back gracefully if scipy is not
    installed (raises FwhmError with a clear message).

    Returns
    -------
    dict with keys:
        fwhm_samples : float
        fwhm_seconds : float  (fwhm_samples * xincr)
        amplitude    : float  (fitted peak)
        center       : float  (fitted centre in sample units)
        sigma_samples: float  (fitted sigma; FWHM = 2√(2 ln 2) · sigma)
        r_squared    : float  (coefficient of determination; 1.0 = perfect fit)

    Raises FwhmError on failure.
    """
    try:
        import numpy as np
        from scipy.optimize import curve_fit
    except ImportError as exc:
        raise FwhmError(
            "gaussian_fwhm_fit requires scipy and numpy; "
            "install them with: pip install scipy numpy"
        ) from exc

    n = len(volts)
    if n < 4:
        raise FwhmError(f"Waveform too short for Gaussian fit: {n} samples")

    volts_arr = np.asarray(volts, dtype=float)
    if subtract_baseline:
        volts_arr = volts_arr - estimate_baseline(list(volts_arr))

    xs        = np.arange(n, dtype=float)
    peak_idx  = int(np.argmax(volts_arr))
    amplitude = float(volts_arr[peak_idx])
    if amplitude <= 0:
        raise FwhmError(
            f"Baseline-subtracted peak is non-positive ({amplitude:.4g})"
        )

    def _gaussian(x, A, mu, sigma):
        return A * np.exp(-0.5 * ((x - mu) / sigma) ** 2)

    sigma0 = n / 6.0   # rough initial guess: 1/6 of the window
    p0 = [amplitude, float(peak_idx), sigma0]

    try:
        popt, _ = curve_fit(
            _gaussian, xs, volts_arr, p0=p0,
            bounds=([0, 0, 0.5], [np.inf, n - 1, n]),
            maxfev=5000,
        )
    except Exception as exc:
        raise FwhmError(f"Gaussian fit did not converge: {exc}") from exc

    A, mu, sigma = popt
    fwhm_samples = 2.0 * math.sqrt(2.0 * math.log(2.0)) * abs(sigma)
    fwhm_seconds = fwhm_samples * xincr

    # R²
    y_pred    = _gaussian(xs, *popt)
    ss_res    = float(np.sum((volts_arr - y_pred) ** 2))
    ss_tot    = float(np.sum((volts_arr - volts_arr.mean()) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    log.debug(
        "profile_fwhm: Gaussian fit A=%.4g mu=%.2f sigma=%.2f "
        "fwhm=%.2f samples r²=%.4f",
        A, mu, sigma, fwhm_samples, r_squared,
    )
    return {
        "fwhm_samples":  fwhm_samples,
        "fwhm_seconds":  fwhm_seconds,
        "amplitude":     float(A),
        "center":        float(mu),
        "sigma_samples": float(abs(sigma)),
        "r_squared":     r_squared,
    }


# ===========================================================================
# MULTI-PEAK ANALYSIS
#
# WHY THIS EXISTS ALONGSIDE THE SINGLE-PEAK FUNCTIONS ABOVE
# ---------------------------------------------------------
# One channel carries TWO peaks, and they are the X profile and the Y
# profile - two different directions, measured in one trace.  They are NOT
# one beam measured twice, so:
#
#   * their mean is only a rough "beam size", and their ratio is the beam's
#     ASPECT, not a quality metric.  A 10 % difference between them may be
#     a perfectly healthy elliptical beam;
#   * each is reported under its own axis label and tracked separately.
#
# compute_fwhm_samples() finds the tallest peak and walks outward from it -
# with two peaks present it walks straight into the neighbour, and a single
# Gaussian fitted across both reports the peak SEPARATION rather than a
# width (measured on the real beam line: r^2 fell to 0.14-0.36).
#
# So this section measures every peak independently:
#   * each peak has its own height, so its own half-maximum level;
#   * each peak's crossing search is fenced in by the valleys either side,
#     and can never wander into a neighbour;
#   * the fit is a SUM of N Gaussians, one per detected peak.
#
# AND WHEN THE BEAM IS RASTERED, none of that applies to the raw trace: the
# fast raster axis chops each profile into a train of teeth, and the beam
# width lives in their ENVELOPE.  See upper_envelope().
#
# The single-peak functions above are unchanged and still used wherever a
# trace really does hold one peak.
# ===========================================================================

MAX_PEAKS = 4

# How far above the noise sigma a peak must stand to count as signal.
# 5 sigma clears the ~3.5 sigma largest sample of a pure-noise record.
_MIN_PEAK_SIGMA = 5.0


def _median(values) -> float:
    """Plain median.  Used for the noise estimate, where estimate_baseline()
    would wrongly re-window an already-windowed slice."""
    s = sorted(values)
    if not s:
        return 0.0
    mid = len(s) // 2
    return float(s[mid]) if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def moving_average(volts: list[float], window: int) -> list[float]:
    """Boxcar smoother, applied before the half-maximum search.

    Noise riding on top of a peak inflates the apparent peak height, which
    lifts the half-maximum level, which narrows the measured width.  On the
    real beam line at S/N ~ 12 this biased the reading ~30 % low.  A boxcar
    of width w broadens a true Gaussian only in quadrature, so keep w well
    under the expected FWHM and the bias costs nothing.

    window <= 1 returns a copy, unchanged.
    """
    if window <= 1:
        return list(volts)
    n = len(volts)
    half = window // 2
    out = []
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        out.append(sum(volts[lo:hi]) / (hi - lo))
    return out


def find_peaks(volts: list[float], threshold: float, min_sep: int,
               max_peaks: int) -> list[int]:
    """Indices of the tallest local maxima above *threshold*, left to right.

    Candidates closer than *min_sep* samples to an already-accepted (taller)
    peak are discarded as the same peak seen twice through noise.
    """
    n = len(volts)
    cands = [i for i in range(1, n - 1)
             if volts[i] >= volts[i - 1] and volts[i] >= volts[i + 1]
             and volts[i] >= threshold]
    if not cands and n:
        cands = [max(range(n), key=lambda i: volts[i])]

    kept: list[int] = []
    for i in sorted(cands, key=lambda i: volts[i], reverse=True):
        if all(abs(i - k) >= min_sep for k in kept):
            kept.append(i)
        if len(kept) >= max_peaks:
            break
    return sorted(kept)


def upper_envelope(volts: list[float], ripple_samples: int) -> list[float]:
    """Envelope through the tops of a RASTERED burst.

    A rastered beam does not paint a smooth Gaussian on the detector.  The
    fast raster axis chops the profile into a train of crossings, and what
    carries the beam width is the ENVELOPE those crossing-tops trace out -
    the ripple itself is the raster, not the beam.  Measuring half maximum
    on the raw trace measures a raster tooth: on the real beam line that
    produced widths that jumped between 4.4 and 4.9 ms while a single
    Gaussian fit sat at r2 = 0.30, because neither was looking at a beam.

    Method: split the record into blocks one ripple period long, take each
    block's maximum, and interpolate between those points.  Deliberately
    NOT a rolling maximum, which would widen every feature by the window
    and inflate the FWHM by roughly that much.

    ripple_samples is the raster period in samples; <= 1 returns a copy.
    """
    n = len(volts)
    if ripple_samples <= 1 or n < 4:
        return list(volts)
    block = max(2, int(ripple_samples))

    nodes = []
    for start in range(0, n, block):
        stop = min(n, start + block)
        best = max(range(start, stop), key=lambda i: volts[i])
        nodes.append((best, volts[best]))
    if len(nodes) < 2:
        return list(volts)

    out = [0.0] * n
    # Clamp flat outside the first and last node: in the quiet stretches a
    # block maximum is just baseline noise, so this costs nothing there.
    for i in range(nodes[0][0] + 1):
        out[i] = nodes[0][1]
    for i in range(nodes[-1][0], n):
        out[i] = nodes[-1][1]
    for (i0, v0), (i1, v1) in zip(nodes, nodes[1:]):
        span = i1 - i0
        if span <= 0:
            continue
        for i in range(i0, i1 + 1):
            out[i] = v0 + (v1 - v0) * (i - i0) / span
    return out


def detect_clipping(volts: list[float], tolerance: float = 0.02) -> dict:
    """Flag a trace that ran off the top or bottom of the screen.

    A clipped peak has no true maximum, so its half-maximum level is wrong
    and every width derived from it is wrong with it - quietly, and in a way
    that looks like a plausible number.  Detection is by how many samples
    share the extreme value exactly: a real signal crosses its extreme once,
    a clipped one sits on it.

    Returns {"low": bool, "high": bool, "low_fraction": f, "high_fraction": f}.
    """
    n = len(volts)
    if n == 0:
        return {"low": False, "high": False,
                "low_fraction": 0.0, "high_fraction": 0.0}
    lo, hi = min(volts), max(volts)
    n_lo = sum(1 for v in volts if v == lo)
    n_hi = sum(1 for v in volts if v == hi)
    return {
        "low":  n_lo / n > tolerance,
        "high": n_hi / n > tolerance,
        "low_fraction":  n_lo / n,
        "high_fraction": n_hi / n,
    }


def _fenced_crossing(volts: list[float], peak_i: int, half: float,
                     lo: int, hi: int, direction: int):
    """Interpolated index where *volts* crosses *half*, walking from the peak.

    Confined to [lo, hi] - the valleys either side of this peak.  Returns
    None when the trace never reaches half maximum inside that fence, which
    means this peak and its neighbour are not resolved at half height.
    """
    i = peak_i
    while lo <= i + direction <= hi:
        j = i + direction
        if (volts[i] >= half >= volts[j]) or (volts[i] <= half <= volts[j]):
            if volts[i] == volts[j]:
                return float(i)
            frac = (half - volts[i]) / (volts[j] - volts[i])   # 0..1, i -> j
            return i + direction * frac
        i = j
    return None


def analyse_profile(volts: list[float], xincr: float, *,
                    polarity: str = "auto",
                    smooth: int = 1,
                    max_peaks: int = 2,
                    peak_threshold: float = 0.30,
                    min_sep_frac: float = 0.02,
                    baseline_frac: float = _BASELINE_FRACTION,
                    envelope_samples: int = 0,
                    quantum: float = 0.0,
                    axis_labels: tuple = ("X", "Y")) -> dict:
    """Measure every beam peak in one trace.

    Parameters
    ----------
    volts          : voltage samples, as acquired
    xincr          : seconds per sample (preamble XINCR)
    polarity       : "auto" (larger excursion wins), "pos", or "neg"
    smooth         : boxcar window in samples; 1 disables smoothing
    max_peaks      : how many peaks to look for (capped at MAX_PEAKS)
    peak_threshold : ignore maxima below this fraction of the tallest peak
    min_sep_frac   : minimum peak separation, as a fraction of the record
    envelope_samples : raster period in samples.  > 1 measures the ENVELOPE
                     of a rastered burst instead of the raw trace - see
                     upper_envelope().  0 or 1 leaves the trace alone.
    quantum        : volts per ADC count (preamble YMULT).  Floors the noise
                     estimate: the scope cannot resolve below one count, so
                     a trace whose quiet stretch sits on exactly one code
                     has an apparent noise of zero and would report an
                     absurd signal-to-noise ratio.
    axis_labels    : what the peaks ARE, left to right.  On this beam line
                     the two peaks are the X and the Y profile - two
                     different measurements of two different directions,
                     NOT one beam measured twice.

    Returns
    -------
    dict with:
        peaks               list, left to right; each entry carries index,
                            volts, half_volts, left_index, right_index,
                            centre_index, fwhm_samples, fwhm_seconds,
                            resolved (bool) and note (str)
        n_peaks, n_resolved
        mean_fwhm_seconds   mean over RESOLVED peaks, NaN if none resolved
        fwhm_spread         (max-min)/mean over resolved peaks; 0.03 = 3 %
        separations_seconds gaps between consecutive peak centres
        corrected           smoothed, baseline-subtracted, sign-corrected
        raw                 the input, untouched
        baseline, flipped, noise (robust sigma), tallest_volts,
                            signal_to_noise (peak / sigma), smooth

    Raises
    ------
    FwhmError
        Only when there is nothing measurable at all: too few samples, no
        positive peak, or a peak indistinguishable from the noise floor.
        Peaks that merely fail to resolve at half height are REPORTED as
        unresolved, not raised - the Gaussian fit can still measure them.
    """
    n = len(volts)
    if n < 16:
        raise FwhmError(f"Waveform too short: {n} samples (minimum 16)")
    max_peaks = max(1, min(int(max_peaks), MAX_PEAKS))

    raw = list(volts)
    clipping = detect_clipping(raw)
    work = moving_average(raw, smooth)
    if envelope_samples > 1:
        work = upper_envelope(work, envelope_samples)

    wing = max(1, int(n * baseline_frac / 2))
    baseline = estimate_baseline(work)
    v = [x - baseline for x in work]

    if polarity == "neg":
        flip = True
    elif polarity == "pos":
        flip = False
    else:
        flip = abs(min(v)) > abs(max(v))
    if flip:
        v = [-x for x in v]

    # Noise is measured as a ROBUST SIGMA - the median absolute deviation of
    # the quiet edges, scaled by 1.4826 so it means the same thing as a
    # standard deviation for Gaussian noise.  The plain median of |edge|
    # under-reads by that same factor, and a 3x bar on it lets a trace of
    # pure noise through: the largest of 2500 Gaussian samples sits around
    # 3.5 sigma, so it would be "detected" as a peak.  Requiring 5 sigma
    # puts the gate above what noise alone can produce.
    edge = v[:wing] + v[-wing:]
    edge_med = _median(edge)
    noise = 1.4826 * _median([abs(x - edge_med) for x in edge])
    # Floor at half an ADC count.  Without it, a quiet stretch that happens
    # to sit on a single ADC code gives noise = 0 and a signal-to-noise
    # ratio in the billions - a number that says nothing except that the
    # denominator collapsed.
    noise = max(noise, 0.5 * quantum, 1e-12)
    tallest = max(v)
    if tallest <= 0.0:
        raise FwhmError(
            f"Baseline-subtracted peak is non-positive ({tallest:.4g}); "
            "cannot compute FWHM"
        )
    if tallest < _MIN_PEAK_SIGMA * noise:
        raise FwhmError(
            f"Tallest peak {tallest:.4g} V is under {_MIN_PEAK_SIGMA:.0f}x the "
            f"edge noise sigma ({noise:.4g} V) - nothing to measure"
        )

    threshold = max(peak_threshold * tallest, _MIN_PEAK_SIGMA * noise)
    min_sep = max(3, int(n * min_sep_frac))
    idx = find_peaks(v, threshold, min_sep, max_peaks)

    peaks = []
    for k, i in enumerate(idx):
        lo = 0 if k == 0 else min(range(idx[k - 1], i), key=lambda j: v[j])
        hi = (n - 1) if k == len(idx) - 1 else min(range(i, idx[k + 1]),
                                                   key=lambda j: v[j])
        half = v[i] / 2.0
        left = _fenced_crossing(v, i, half, lo, hi, -1)
        right = _fenced_crossing(v, i, half, lo, hi, +1)

        entry = {
            "axis": (axis_labels[k] if k < len(axis_labels) else f"#{k + 1}"),
            "index": i,
            "volts": v[i],
            "half_volts": half,
            "left_index": left,
            "right_index": right,
            "fence": (lo, hi),
            "resolved": left is not None and right is not None,
            "note": "",
            "fwhm_samples": float("nan"),
            "fwhm_seconds": float("nan"),
            "centre_index": float(i),
        }
        if entry["resolved"]:
            width = right - left
            if width > 0:
                entry["fwhm_samples"] = width
                entry["fwhm_seconds"] = width * xincr
                entry["centre_index"] = (left + right) / 2.0
            else:
                entry["resolved"] = False
                entry["note"] = "degenerate width"
        if not entry["resolved"] and not entry["note"]:
            side = "left" if left is None else "right"
            entry["note"] = (f"never falls to half maximum on the {side} "
                             f"before the neighbouring peak")
        peaks.append(entry)

    if not peaks:
        raise FwhmError("No peaks found above the detection threshold")

    good = [p["fwhm_seconds"] for p in peaks if p["resolved"]]
    mean_fwhm = sum(good) / len(good) if good else float("nan")
    spread = ((max(good) - min(good)) / mean_fwhm if len(good) > 1
              else (0.0 if good else float("nan")))
    separations = [(peaks[k + 1]["centre_index"] - peaks[k]["centre_index"]) * xincr
                   for k in range(len(peaks) - 1)]

    log.debug("profile_fwhm: %d peaks, %d resolved, mean fwhm %.4g s, "
              "spread %.1f %%", len(peaks), len(good), mean_fwhm,
              (spread * 100 if spread == spread else float('nan')))

    # Per-axis widths.  These are the numbers that mean something: the two
    # peaks are different directions, so their MEAN is only a rough "beam
    # size" and their ratio is the beam's aspect, not a quality metric.
    by_axis = {p["axis"]: p["fwhm_seconds"] for p in peaks if p["resolved"]}
    fwhm_x = by_axis.get(axis_labels[0] if axis_labels else "X", float("nan"))
    fwhm_y = by_axis.get(axis_labels[1] if len(axis_labels) > 1 else "Y",
                         float("nan"))
    xy_ratio = (fwhm_x / fwhm_y
                if (fwhm_x == fwhm_x and fwhm_y == fwhm_y and fwhm_y > 0)
                else float("nan"))

    return {
        "peaks": peaks,
        "n_peaks": len(peaks),
        "n_resolved": len(good),
        "mean_fwhm_seconds": mean_fwhm,
        "fwhm_spread": spread,
        "fwhm_x_seconds": fwhm_x,
        "fwhm_y_seconds": fwhm_y,
        "xy_ratio": xy_ratio,
        "separations_seconds": separations,
        "corrected": v,
        "raw": raw,
        "baseline": baseline,
        "flipped": flip,
        "noise": noise,
        "tallest_volts": tallest,
        "signal_to_noise": tallest / noise,
        "smooth": smooth,
        "envelope_samples": envelope_samples,
        "clipping": clipping,
    }


def fit_gaussians(result: dict, xincr: float) -> dict | None:
    """Least-squares fit of a SUM of Gaussians, one per detected peak.

    Returns None (never raises) when scipy/numpy are missing or the fit does
    not converge - the half-maximum numbers stand on their own.

    Returns
    -------
    dict with peaks (per-peak amplitude/centre_index/sigma_samples/
    fwhm_samples/fwhm_seconds), offset, r_squared, curve, and
    mean_fwhm_seconds.
    """
    try:
        import numpy as np
        from scipy.optimize import curve_fit
    except ImportError:
        log.debug("profile_fwhm: scipy/numpy missing - skipping Gaussian fit")
        return None

    y = np.asarray(result["corrected"], dtype=float)
    x = np.arange(y.size, dtype=float)
    peaks = result["peaks"]
    k = len(peaks)
    if k == 0 or y.size < 8:
        return None

    def model(xx, *p):
        out = np.full_like(xx, p[-1])
        for m in range(k):
            a, mu, sigma = p[3 * m: 3 * m + 3]
            out = out + a * np.exp(-0.5 * ((xx - mu) / sigma) ** 2)
        return out

    p0, lo, hi = [], [], []
    for pk in peaks:
        w = pk["fwhm_samples"]
        sigma0 = (w / 2.3548) if (w == w and w > 0) else max(y.size / 20.0, 2.0)
        p0 += [max(pk["volts"], 1e-9), pk["centre_index"], sigma0]
        lo += [0.0, 0.0, 0.5]
        hi += [float("inf"), float(y.size), float(y.size)]
    p0 += [0.0]
    lo += [-float("inf")]
    hi += [float("inf")]

    try:
        popt, _ = curve_fit(model, x, y, p0=p0, bounds=(lo, hi), maxfev=20000)
    except Exception as exc:
        log.debug("profile_fwhm: Gaussian fit did not converge: %s", exc)
        return None

    pred = model(x, *popt)
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    per_peak = []
    for m in range(k):
        a, mu, sigma = popt[3 * m: 3 * m + 3]
        fw = 2.0 * math.sqrt(2.0 * math.log(2.0)) * abs(sigma)
        per_peak.append({
            "amplitude": float(a),
            "centre_index": float(mu),
            "sigma_samples": float(abs(sigma)),
            "fwhm_samples": fw,
            "fwhm_seconds": fw * xincr,
        })
    widths = [p["fwhm_seconds"] for p in per_peak]
    return {
        "peaks": per_peak,
        "offset": float(popt[-1]),
        "r_squared": r2,
        "curve": pred.tolist(),
        "mean_fwhm_seconds": sum(widths) / len(widths) if widths else float("nan"),
    }


def best_fwhm(result: dict, fit: dict | None,
              min_r2: float = 0.90) -> tuple[float, str]:
    """Pick the number to display: the fit when it is trustworthy.

    The half-maximum reading looks at four samples per peak, so noise moves
    it; the fit uses every sample.  But a fit is only as good as its model,
    so it wins only while r^2 clears *min_r2* - below that the model is
    wrong (a missed peak, a non-Gaussian profile) and the half-maximum
    reading, blunt as it is, is the honest one.

    Returns (seconds, source) where source is "fit", "half-max", or "none".
    """
    if fit is not None:
        r2 = fit.get("r_squared", float("nan"))
        mean = fit.get("mean_fwhm_seconds", float("nan"))
        if r2 == r2 and mean == mean and r2 >= min_r2:
            return mean, "fit"
    mean = result.get("mean_fwhm_seconds", float("nan"))
    if mean == mean:
        return mean, "half-max"
    return float("nan"), "none"

# ===========================================================================
# THE WIDTH LADDER — one width is not a beam
#
# WHY FWHM ALONE IS NOT ENOUGH ON THIS BEAM LINE
# ----------------------------------------------
# Half maximum describes the CORE of a profile and says nothing about where
# the beam actually ends.  Two beams with identical FWHM can put very
# different amounts of current outside the painted field, and on this beam
# line that difference is not academic: `slit_raster_model` derives the
# overscan needed for a given edge droop by inverting a NORMAL CDF from the
# FWHM alone.  Every one of those numbers is only as true as the assumption
# that the beam is Gaussian.
#
# So the ladder exists to put that assumption on trial, per shot:
#
#   FWHM     50 %      the core
#   FW1/e2   13.5 %    the optics convention; exactly 4 sigma for a Gaussian
#   FWTM     10 %      the tails
#
# For a true Gaussian the widths are locked to each other:
#
#       FW(f) / FWHM  =  sqrt( ln(1/f) / ln 2 )
#
#   f = 0.135335 (1/e^2)  ->  1.69864     (i.e. 4 sigma)
#   f = 0.10     (tenth)  ->  1.82261
#
# A measured FWTM/FWHM ABOVE 1.8226 means heavier tails than Gaussian -
# halo - and the planner's droop figures are optimistic.  BELOW it means a
# flat-topped or truncated profile, usually a beam scraping an aperture
# upstream.  The ratio is the diagnostic; the individual widths are the
# evidence for it.
#
# EVERY LEVEL IS MEASURED INDEPENDENTLY, EVERY SHOT
# -------------------------------------------------
# Nothing here is carried between shots and nothing is inferred from the
# FWHM.  Each level runs its own crossing search from the apex, against its
# own level voltage, with its own noise check and its own resolved/unresolved
# verdict.  That is deliberate: with quadrupole focusing the profile can be a
# different shape session to session and even shot to shot, so a width
# derived from a remembered shape would be describing a beam that is no
# longer there.  The ONLY constant is sqrt(ln(1/f)/ln 2), which is a property
# of the Gaussian function, not of any beam.
#
# TWO WAYS A LEVEL CAN FAIL, AND THEY MEAN DIFFERENT THINGS
# ---------------------------------------------------------
#   * BELOW THE NOISE FLOOR.  A level under a few sigma of the edge noise is
#     not a width, it is a reading of the noise.  Refused outright.
#   * SKIRTS OVERLAP.  The crossing search is fenced by the valley between
#     this peak and its neighbour.  At 10 % the two profiles' skirts may
#     never fall that low before meeting, so there is no crossing to find
#     inside the fence.  That is real information - it means the peaks are
#     close relative to their width - and it is reported as unresolved, with
#     the sum-of-Gaussians fit's number offered BESIDE it and labelled as
#     fit-derived.  It is never quietly truncated at the fence, which would
#     read as a narrow beam.
# ===========================================================================

# The levels measured, as fractions of each peak's own height.
LEVEL_FWHM   = 0.50
LEVEL_1_OVER_E2 = 0.13533528323661270   # exp(-2); FW here is exactly 4 sigma
LEVEL_FWTM   = 0.10

DEFAULT_LEVELS = (LEVEL_FWHM, LEVEL_1_OVER_E2, LEVEL_FWTM)

# A level below this many sigma of the edge noise is not measurable.
LEVEL_NOISE_GUARD = 3.0


def level_label(level: float) -> str:
    """The name an operator would use for this level."""
    if abs(level - LEVEL_FWHM) < 1e-9:
        return "FWHM"
    if abs(level - LEVEL_1_OVER_E2) < 1e-6:
        return "FW1/e²"
    if abs(level - LEVEL_FWTM) < 1e-9:
        return "FWTM"
    return f"FW{level * 100:g}%"


def gaussian_width_ratio(level: float) -> float:
    """FW at *level*, divided by FWHM, for a true Gaussian.

    sqrt(ln(1/f) / ln 2).  This is the yardstick the measured widths are
    compared against - a property of the Gaussian function, not of any beam,
    which is why it is the one thing here that does not come from the trace.
    """
    if not (0.0 < level < 1.0):
        raise FwhmError(f"level must be between 0 and 1, got {level!r}")
    return math.sqrt(math.log(1.0 / level) / math.log(2.0))


def gaussian_width_from_sigma(sigma: float, level: float) -> float:
    """FW at *level* for a Gaussian of this sigma: 2·sigma·sqrt(2·ln(1/f))."""
    if not (0.0 < level < 1.0) or sigma <= 0:
        return float("nan")
    return 2.0 * abs(sigma) * math.sqrt(2.0 * math.log(1.0 / level))


def measure_width_levels(result: dict, xincr: float, *,
                         levels=DEFAULT_LEVELS,
                         fit: dict = None,
                         noise_guard: float = LEVEL_NOISE_GUARD) -> list:
    """Widths at several levels, measured independently on THIS trace.

    Parameters
    ----------
    result      : an `analyse_profile` result - supplies the corrected trace,
                  the peaks, their per-peak fences and the noise sigma
    xincr       : seconds per sample
    levels      : fractions of each peak's own height to measure at
    fit         : a `fit_gaussians` result, or None.  Used ONLY to offer a
                  fit-derived number beside a level that could not be
                  measured, always labelled as such - never to replace a
                  measurement that succeeded
    noise_guard : how many noise sigmas a level must clear to be measurable

    Returns
    -------
    list, one entry per peak, left to right:
        axis, index, peak_volts,
        levels : {label: {level, volts, left_index, right_index,
                          width_samples, width_seconds, resolved, source,
                          fit_width_seconds, note}}
        tail_ratio        FWTM / FWHM, from MEASURED widths only.  NaN
                          when either level was not measurable - there is
                          deliberately no fit fallback, because a Gaussian
                          fit's own ratio is the constant 1.8226 and would
                          read as "perfectly Gaussian" when it means "not
                          measured"
        tail_ratio_source "measured" or "none"
        tail_note         why the ratio is absent, when it is
        tail_excess       measured ratio / 1.8226 - 1, i.e. how much heavier
                          (+) or lighter (-) the tails are than Gaussian
    """
    v        = result["corrected"]
    noise    = result.get("noise", 0.0) or 0.0
    peaks    = result["peaks"]
    fit_peaks = (fit or {}).get("peaks", [])

    out = []
    for k, p in enumerate(peaks):
        i        = p["index"]
        lo, hi   = p.get("fence", (0, len(v) - 1))
        peak_v   = p["volts"]
        sigma    = (fit_peaks[k]["sigma_samples"] if k < len(fit_peaks)
                    else float("nan"))

        entry = {
            "axis":       p.get("axis", f"#{k + 1}"),
            "index":      i,
            "peak_volts": peak_v,
            "levels":     {},
        }

        for level in levels:
            label     = level_label(level)
            level_v   = level * peak_v
            fit_width = (gaussian_width_from_sigma(sigma, level) * xincr
                         if sigma == sigma and sigma > 0 else float("nan"))

            rec = {
                "level":             level,
                "volts":             level_v,
                "left_index":        None,
                "right_index":       None,
                "width_samples":     float("nan"),
                "width_seconds":     float("nan"),
                "resolved":          False,
                "source":            "none",
                "fit_width_seconds": fit_width,
                "note":              "",
            }

            # A level buried in the noise is a reading of the noise, not a
            # width.  Refused before any search, so the note says WHY rather
            # than reporting a crossing found in a noise excursion.
            if level_v < noise_guard * noise:
                rec["note"] = (
                    f"the {label} level ({level_v:.4g} V) is under "
                    f"{noise_guard:g}× the edge noise sigma ({noise:.4g} V) — "
                    f"any crossing found there would be noise")
                rec["source"] = "fit" if fit_width == fit_width else "none"
                entry["levels"][label] = rec
                continue

            left  = _fenced_crossing(v, i, level_v, lo, hi, -1)
            right = _fenced_crossing(v, i, level_v, lo, hi, +1)
            if left is None or right is None:
                side = "left" if left is None else "right"
                rec["note"] = (
                    f"never falls to {level * 100:g} % of its own height on "
                    f"the {side} before the neighbouring peak — the two "
                    f"profiles' skirts overlap at this level")
                rec["source"] = "fit" if fit_width == fit_width else "none"
                entry["levels"][label] = rec
                continue

            width = right - left
            if width <= 0:
                rec["note"] = "degenerate width"
                rec["source"] = "fit" if fit_width == fit_width else "none"
                entry["levels"][label] = rec
                continue

            rec.update({
                "left_index":    left,
                "right_index":   right,
                "width_samples": width,
                "width_seconds": width * xincr,
                "resolved":      True,
                "source":        "measured",
            })
            entry["levels"][label] = rec

        # ---- the diagnostic -------------------------------------------
        # MEASURED OVER MEASURED, OR NOTHING.
        #
        # There is no fit fallback here, and that is the whole point.  The
        # sum-of-Gaussians fit would happily supply both widths - and their
        # ratio would come out at 1.8226 EVERY TIME, because a Gaussian fit
        # is Gaussian by construction.  Displayed next to the word "tails"
        # that reads as "your beam is perfectly Gaussian", when what actually
        # happened is that nobody measured.  A number that cannot come out
        # any other way carries no information, so it is not produced.
        #
        # The fit's widths are still offered PER LEVEL above, where they are
        # labelled fit-derived and answer a different question: "how wide
        # would a Gaussian of this core be down there?"  That is a useful
        # thing to see.  It is not evidence about the real tails.
        half  = entry["levels"].get(level_label(LEVEL_FWHM), {})
        tenth = entry["levels"].get(level_label(LEVEL_FWTM), {})
        if half.get("resolved") and tenth.get("resolved"):
            ratio  = tenth["width_seconds"] / half["width_seconds"]
            source = "measured"
            note   = ""
        else:
            ratio  = float("nan")
            source = "none"
            unmeasured = ("FWTM" if not tenth.get("resolved") else "FWHM")
            note = (f"the tails cannot be judged this shot — {unmeasured} was "
                    f"not measurable, and a Gaussian fit cannot answer the "
                    f"question, since its own ratio is 1.8226 by construction")
        entry["tail_ratio"]        = ratio
        entry["tail_ratio_source"] = source
        entry["tail_note"]         = note
        entry["tail_excess"] = (ratio / gaussian_width_ratio(LEVEL_FWTM) - 1.0
                                if ratio == ratio else float("nan"))
        out.append(entry)

    return out

