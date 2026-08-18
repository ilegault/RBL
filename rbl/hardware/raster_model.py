"""
raster_model.py
Steerer deflection, required drive amplitude for a uniform raster, dwell
uniformity, and Lissajous pattern properties — the research deliverable.

WHY THIS EXISTS
---------------
The user will be defending raster parameter choices in defect studies and
needs two things a bench scope cannot give: (a) the amplifier's operating
envelope (see `load_model.envelope_walls`), and (b) the drive amplitude
required for uniform dwell given a measured beam FWHM. Both come from the NEC
XY Steerer manual's deflection formula (Section IV) plus a beam-convolution
model, worked out once here instead of once per experiment on paper.

FACTOR-OF-TWO HAZARD
---------------------
The rig drives push-pull: X+ at +V and X- at -V gives a plate-to-plate
potential of 2V, not V. Every function here takes `differential_kv`
(plate-to-plate) and never `kv` alone, to keep this unambiguous at the call
site. If a caller only has the per-plate voltage, it must multiply by 2
itself, deliberately, rather than have this module guess which one arrived.

SCOPE
-----
Pure math. No Qt, no hardware, no config lookups.
"""
import math

import numpy as np

# Elementary charge, Coulombs.
_E_CHARGE = 1.602176634e-19


def deflection_mrad(differential_kv: float, plate_length_cm: float,
                     plate_gap_cm: float, charge_state: int,
                     beam_energy_ev: float) -> float:
    """theta = V*l*q / (2*d*E), NEC XY Steerer manual Section IV. Returns mrad.

    `differential_kv` is PLATE-TO-PLATE (see module docstring). `l` and `d`
    must use the same length unit (both cm here); the formula's ratio l/d is
    dimensionless so the unit choice does not otherwise matter. `charge_state`
    is in elementary charges, `beam_energy_ev` in eV — q/E is dimensionless
    ratio of charge to energy in matched units (both are electron-volt-scale
    quantities once q is expressed as a multiple of the elementary charge),
    so theta comes out in radians directly; this function returns milliradians
    for readability against typical steerer deflections.
    """
    if beam_energy_ev <= 0 or plate_gap_cm <= 0:
        return float("nan")
    v_volts = differential_kv * 1000.0
    theta_rad = (v_volts * plate_length_cm * charge_state) / (2.0 * plate_gap_cm * beam_energy_ev)
    return theta_rad * 1000.0


def displacement_mm(differential_kv: float, plate_length_cm: float,
                     plate_gap_cm: float, charge_state: int,
                     beam_energy_ev: float, drift_cm: float) -> float:
    """x = theta * (L + l/2), NEC XY Steerer manual Section IV. Returns mm.

    `drift_cm` is the drift distance from steerer exit to sample (`L`). The
    l/2 term accounts for the deflection accruing continuously across the
    plates rather than only after them — the beam's effective pivot point is
    the plate centre, not the exit.
    """
    theta_rad = deflection_mrad(differential_kv, plate_length_cm, plate_gap_cm,
                                 charge_state, beam_energy_ev) / 1000.0
    if math.isnan(theta_rad):
        return float("nan")
    x_cm = theta_rad * (drift_cm + plate_length_cm / 2.0)
    return x_cm * 10.0


def required_differential_kv(target_half_width_mm: float, fwhm_mm: float,
                              plate_length_cm: float, plate_gap_cm: float,
                              charge_state: int, beam_energy_ev: float,
                              drift_cm: float, turnaround_k: float = 1.5) -> float:
    """Plate-to-plate kV needed to put the raster turnaround off the sample.

    Uniform dwell requires the direction change to happen off the sample.
    Because the beam has finite width, the sample's edge does not reach
    uniform dose until the beam CENTRE has travelled roughly
    turnaround_k * FWHM past the sample edge:

        required half-amplitude = target_half_width_mm + turnaround_k * fwhm_mm

    `turnaround_k` defaults to 1.5 (a beam-widths-and-a-half margin) but is a
    parameter, not a hard-coded fact — `raster_model.dwell_uniformity` is what
    justifies a particular value to a reviewer instead of asserting one.
    Inverts `displacement_mm`, which is linear in `differential_kv`, so a
    single-point unit-voltage probe gives the required scale factor exactly
    (no root-finding needed).
    """
    required_half_width_mm = target_half_width_mm + turnaround_k * fwhm_mm
    x_per_kv = displacement_mm(1.0, plate_length_cm, plate_gap_cm, charge_state,
                                beam_energy_ev, drift_cm)
    if not math.isfinite(x_per_kv) or x_per_kv == 0:
        return float("nan")
    return required_half_width_mm / x_per_kv


def dwell_uniformity(fwhm_mm: float, scan_half_width_mm: float,
                      sample_half_width_mm: float, n_points: int = 501) -> dict:
    """Simulate dose vs position for a constant-velocity triangle scan
    convolved with a Gaussian beam.

    A triangle raster spends equal time per unit distance everywhere except
    the two turnarounds, so its dwell-time density is uniform except near
    +/-scan_half_width_mm; convolving that uniform density with the beam's
    Gaussian profile is what actually determines the dose the sample sees,
    since the beam has finite width and "dwell time at a point" is not the
    same as "dose delivered to a point". This is what justifies
    `turnaround_k` in `required_differential_kv` to a reviewer instead of
    asserting it.

    Returns {"uniformity_pct", "profile_mm", "profile_dose", "edge_rolloff_mm"}.
    `uniformity_pct` is the peak-to-peak dose variation, as a percentage of
    the mean dose, evaluated only over the sample region
    [-sample_half_width_mm, +sample_half_width_mm] — the raster's own
    turnaround dose pileup outside the sample is irrelevant to uniformity on
    the sample and must not contaminate this number.
    """
    if fwhm_mm <= 0 or scan_half_width_mm <= 0 or sample_half_width_mm <= 0:
        return {"uniformity_pct": float("nan"), "profile_mm": np.array([]),
                "profile_dose": np.array([]), "edge_rolloff_mm": float("nan")}

    sigma_mm = fwhm_mm / (2.0 * math.sqrt(2.0 * math.log(2.0)))
    margin_mm = 4.0 * sigma_mm
    x_max = scan_half_width_mm + margin_mm
    profile_mm = np.linspace(-x_max, x_max, n_points)

    # Dwell-time density of a constant-velocity triangle scan: uniform on
    # [-scan_half_width_mm, +scan_half_width_mm], zero outside.
    dwell = np.where(np.abs(profile_mm) <= scan_half_width_mm, 1.0, 0.0)

    # Convolve with a normalised Gaussian beam kernel of the same sample grid.
    dx = profile_mm[1] - profile_mm[0]
    kernel_x = np.arange(-margin_mm, margin_mm + dx, dx)
    kernel = np.exp(-0.5 * (kernel_x / sigma_mm) ** 2)
    kernel /= kernel.sum()
    profile_dose = np.convolve(dwell, kernel, mode="same")

    in_sample = np.abs(profile_mm) <= sample_half_width_mm
    if not np.any(in_sample):
        uniformity_pct = float("nan")
    else:
        sample_dose = profile_dose[in_sample]
        mean_dose = float(np.mean(sample_dose))
        uniformity_pct = (float(np.ptp(sample_dose)) / mean_dose * 100.0) if mean_dose > 0 else float("nan")

    # Edge rolloff: distance from the sample edge inward to where dose first
    # reaches 95% of the flat-top (mid-scan) dose — how much margin the
    # turnaround costs in practice.
    mid_dose = float(np.interp(0.0, profile_mm, profile_dose))
    edge_rolloff_mm = float("nan")
    if mid_dose > 0:
        threshold = 0.95 * mid_dose
        near_edge = (profile_mm >= 0) & (profile_mm <= scan_half_width_mm)
        below = profile_dose[near_edge] < threshold
        xs = profile_mm[near_edge]
        if np.any(below):
            edge_rolloff_mm = float(scan_half_width_mm - xs[below][0])

    return {
        "uniformity_pct": uniformity_pct,
        "profile_mm": profile_mm,
        "profile_dose": profile_dose,
        "edge_rolloff_mm": edge_rolloff_mm,
    }


def lissajous_metrics(f_fast_hz: float, f_slow_hz: float, span_fast_mm: float,
                       span_slow_mm: float, fwhm_mm: float) -> dict:
    """Raster pattern properties.

    The pattern repeats after T = 1/gcd(f_fast, f_slow) — the shortest time
    in which both axes complete a whole number of cycles. For 517 Hz and
    64 Hz, gcd(517, 64) = 1 Hz (517 = 11*47, 64 = 2^6 share no common
    factor), so closing the pattern takes a full 517 fast-axis cycles (and
    64 slow-axis cycles) = 1.0 s, drawing 517 distinguishable lines before
    any of them repeat. A near-integer frequency ratio instead gives very
    few lines per repeat — a standing striped pattern instead of a filling
    one — so this number is a real design constraint, not trivia.

    Returns {"repeat_period_s", "line_spacing_mm", "lines_per_fwhm",
             "fills_uniformly": bool}.
    """
    if f_fast_hz <= 0 or f_slow_hz <= 0:
        return {"repeat_period_s": float("nan"), "line_spacing_mm": float("nan"),
                "lines_per_fwhm": float("nan"), "fills_uniformly": False}

    # Work in a common integer frequency unit (microhertz) so gcd applies
    # exactly to rational frequencies, not just integers. If g is the gcd
    # frequency (the largest frequency of which both f_fast and f_slow are
    # integer multiples), the pattern repeats after T = 1/g — the shortest
    # time in which BOTH axes complete a whole number of cycles.
    scale = 1_000_000
    a = round(f_fast_hz * scale)
    b = round(f_slow_hz * scale)
    g = math.gcd(a, b)
    repeat_period_s = scale / g if g else float("nan")

    # Fast-axis cycles per repeat period: T * f_fast = a / g. Each fast sweep
    # traces one distinguishable line at a given slow-axis position, so this
    # is the number of lines drawn before the pattern repeats itself.
    n_lines = a // g
    line_spacing_mm = span_slow_mm / n_lines if n_lines > 0 else float("nan")
    lines_per_fwhm = (fwhm_mm / line_spacing_mm) if line_spacing_mm > 0 else float("nan")

    return {
        "repeat_period_s": repeat_period_s,
        "line_spacing_mm": line_spacing_mm,
        "lines_per_fwhm": lines_per_fwhm,
        "fills_uniformly": bool(lines_per_fwhm >= 2.0) if math.isfinite(lines_per_fwhm) else False,
    }


# ---------------------------------------------------------------------------
# Self-test — no hardware, no Qt.  Run:  python -m rbl.hardware.raster_model
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # ES5 geometry from Section 1.7, a simple round-trip sanity check rather
    # than reproduction of a specific worked example from the manual (none is
    # quoted verbatim in the plan).
    l_cm, d_cm = 12.7, 3.8
    q, E_ev, L_cm = 1, 30_000.0, 100.0

    theta = deflection_mrad(1.0, l_cm, d_cm, q, E_ev)
    assert theta > 0
    x = displacement_mm(1.0, l_cm, d_cm, q, E_ev, L_cm)
    assert x > 0
    # Doubling differential kV must exactly double displacement (linear formula).
    x2 = displacement_mm(2.0, l_cm, d_cm, q, E_ev, L_cm)
    assert abs(x2 - 2 * x) < 1e-9
    print(f"[OK] ES5 geometry: theta={theta:.4f} mrad/kV, x={x:.4f} mm/kV (linear in V)")

    # required_differential_kv inverts displacement_mm exactly.
    req_kv = required_differential_kv(target_half_width_mm=5.0, fwhm_mm=2.0,
                                       plate_length_cm=l_cm, plate_gap_cm=d_cm,
                                       charge_state=q, beam_energy_ev=E_ev,
                                       drift_cm=L_cm, turnaround_k=1.5)
    x_at_req = displacement_mm(req_kv, l_cm, d_cm, q, E_ev, L_cm)
    assert abs(x_at_req - (5.0 + 1.5 * 2.0)) < 1e-6, x_at_req
    print(f"[OK] required_differential_kv inverts displacement_mm: {req_kv:.4f} kV "
          f"-> {x_at_req:.4f} mm (expect {5.0 + 1.5 * 2.0:.4f})")

    # Dwell uniformity: a scan much wider than the FWHM should be very uniform
    # over a sample well inside the scan.
    du = dwell_uniformity(fwhm_mm=1.0, scan_half_width_mm=10.0, sample_half_width_mm=5.0)
    assert du["uniformity_pct"] < 1.0, du["uniformity_pct"]
    print(f"[OK] wide scan vs narrow sample: uniformity {du['uniformity_pct']:.4f}% "
          f"(edge rolloff {du['edge_rolloff_mm']:.3f} mm)")

    # A scan barely wider than the sample should show much worse uniformity.
    du_tight = dwell_uniformity(fwhm_mm=1.0, scan_half_width_mm=5.5, sample_half_width_mm=5.0)
    assert du_tight["uniformity_pct"] > du["uniformity_pct"]
    print(f"[OK] tight scan is less uniform: {du_tight['uniformity_pct']:.2f}% "
          f"> {du['uniformity_pct']:.4f}%")

    # Lissajous: the user's stated operating points, 517 Hz / 64 Hz.
    m = lissajous_metrics(f_fast_hz=517.0, f_slow_hz=64.0, span_fast_mm=10.0,
                           span_slow_mm=10.0, fwhm_mm=1.0)
    assert math.gcd(517, 64) == 1
    assert abs(m["repeat_period_s"] - 1.0) < 1e-6, m["repeat_period_s"]
    print(f"[OK] 517/64 Hz: repeat period {m['repeat_period_s']:.3f} s "
          f"(517 fast cycles, 64 slow cycles), line spacing "
          f"{m['line_spacing_mm']:.4f} mm, fills_uniformly={m['fills_uniformly']}")

    # A near-integer ratio (e.g. 500/64 = 7.8125, still not integer, so use an
    # exact multiple) should NOT fill uniformly: too few lines per FWHM.
    m_striped = lissajous_metrics(f_fast_hz=128.0, f_slow_hz=64.0, span_fast_mm=10.0,
                                   span_slow_mm=10.0, fwhm_mm=1.0)
    assert m_striped["lines_per_fwhm"] < 2.0
    assert m_striped["fills_uniformly"] is False
    print(f"[OK] 2:1 ratio (128/64 Hz) leaves stripes: "
          f"lines_per_fwhm={m_striped['lines_per_fwhm']:.3f}, "
          f"fills_uniformly={m_striped['fills_uniformly']}")

    # Degenerate inputs must not raise.
    assert math.isnan(deflection_mrad(1.0, l_cm, d_cm, q, 0.0))
    assert math.isnan(displacement_mm(1.0, l_cm, d_cm, q, 0.0, L_cm))
    bad = lissajous_metrics(0.0, 64.0, 10.0, 10.0, 1.0)
    assert math.isnan(bad["repeat_period_s"]) and bad["fills_uniformly"] is False
    print("[OK] degenerate inputs handled")

    print("\n[OK] raster_model self-test passed")
