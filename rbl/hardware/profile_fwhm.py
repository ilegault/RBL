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
        from scipy.optimize import curve_fit
        import numpy as np
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
