"""
raster_model.py
Steerer deflection, required drive amplitude for a uniform raster, and dwell
uniformity — the research deliverable.

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
    """x = theta * L, NEC XY Steerer manual Section IV. Returns mm.

    `drift_cm` is the drift distance from steerer exit to sample (`L`).

    NO l/2 PIVOT TERM, DELIBERATELY
    -------------------------------
    A pivot at the plate CENTRE rather than the exit — x = theta * (L + l/2)
    — is the more complete first-order picture, and this function used to do
    that. It does not any more, for one reason: the manual and the lab's
    deflection sheet both multiply by the drift alone, and everything this
    beamline has ever quoted comes from those.

        Manual Section IV: "and a drift of 1 meter, the deflection will be
        1.64 cm" — that is theta * L exactly, with theta = 0.0164 rad.

        Hirst RHBL Deflection Information.xlsx, "Deflection on Sample":
        I8 = ((C8*10^3) * 12.5 * q) / (2 * 3.8 * E*10^6) * C9 * 2, where C9
        is the drift in mm and nothing is added to it.

    On this beamline the term was worth +2.5 %. An app that silently
    disagrees by 2.5 % with the sheet on the wall is worse than an app that
    is 2.5 % conservative, because the disagreement is what gets argued
    about at the beamline instead of the beam. `plate_length_cm` is still
    taken, and still used — by `deflection_mrad`, which is where `l`
    belongs.

    Pinned both ways in tests/test_raster_model.py.
    """
    theta_rad = deflection_mrad(differential_kv, plate_length_cm, plate_gap_cm,
                                 charge_state, beam_energy_ev) / 1000.0
    if math.isnan(theta_rad):
        return float("nan")
    x_cm = theta_rad * drift_cm
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


def required_drive(target_width_mm: float, fwhm_mm: float,
                    plate_length_cm: float, plate_gap_cm: float,
                    charge_state: int, beam_energy_ev: float,
                    drift_cm: float, turnaround_k: float = 1.5,
                    center_offset_mm: float = 0.0) -> dict:
    """Everything one axis needs, from the FULL sample width and where its
    centre sits relative to the beam axis.

    WHY FULL WIDTH IN, HALVES INSIDE
    --------------------------------
    A sample is measured with calipers, and what comes off the calipers is
    the full width - 16 mm, not "half-width 8". Halving belongs to the math,
    not to the operator, so this takes `target_width_mm` and halves it here.

    WHY AN OFFSET AND NOT TWO AMPLITUDES
    ------------------------------------
    "Raster one side more than the other" reads like "run X+ a bit higher
    than X-", and that does not do it. The plates are push-pull: X+ at
    +v(t) and X- at -v(t) give a plate-to-plate potential of
    (a1 + a2)*s(t) if their amplitudes differ - still a waveform with no
    DC term, so the sweep is still centred on the beam axis and merely a
    different width. What moves the CENTRE of the sweep is a differential
    DC term: X+ at +O/2 and X- at -O/2 (opposite signs, not the same
    offset on both, which cancels). So the asymmetry is parameterised here
    as a centre offset in millimetres, and turned into that DC term.

    Returns, all differential (plate-to-plate) unless the key says otherwise:

        mm_per_kv            deflection sensitivity at this species/energy
        sample_half_mm       target_width_mm / 2
        scan_half_span_mm    sample half + turnaround_k * fwhm
        scan_min_mm/max_mm   the swept interval, offset included
        amplitude_kv         peak of the AC drive
        offset_kv            the DC term that moves the centre
        peak_kv              |offset| + amplitude - the worst instant
        ac_plate_kv          amplitude / 2, what one plate swings
        offset_plate_kv      offset / 2, one plate's DC term
        peak_plate_kv        the worst instant on one plate

    `peak_plate_kv` is what a plate rating has to clear; `ac_plate_kv` is
    what sets the current draw, because a DC term into a capacitor draws
    charging current once and leakage thereafter, not I = k*f*C*V.
    """
    nan = float("nan")
    x_per_kv = displacement_mm(1.0, plate_length_cm, plate_gap_cm, charge_state,
                                beam_energy_ev, drift_cm)
    sample_half_mm = target_width_mm / 2.0
    scan_half_span_mm = sample_half_mm + turnaround_k * fwhm_mm
    if not math.isfinite(x_per_kv) or x_per_kv == 0:
        return {"mm_per_kv": nan, "sample_half_mm": sample_half_mm,
                "scan_half_span_mm": scan_half_span_mm,
                "scan_min_mm": nan, "scan_max_mm": nan,
                "amplitude_kv": nan, "offset_kv": nan, "peak_kv": nan,
                "ac_plate_kv": nan, "offset_plate_kv": nan,
                "peak_plate_kv": nan}

    amplitude_kv = scan_half_span_mm / x_per_kv
    offset_kv    = center_offset_mm / x_per_kv
    peak_kv      = abs(offset_kv) + amplitude_kv
    return {
        "mm_per_kv":         x_per_kv,
        "sample_half_mm":    sample_half_mm,
        "scan_half_span_mm": scan_half_span_mm,
        "scan_min_mm":       center_offset_mm - scan_half_span_mm,
        "scan_max_mm":       center_offset_mm + scan_half_span_mm,
        "amplitude_kv":      amplitude_kv,
        "offset_kv":         offset_kv,
        "peak_kv":           peak_kv,
        "ac_plate_kv":       amplitude_kv / 2.0,
        "offset_plate_kv":   offset_kv / 2.0,
        "peak_plate_kv":     peak_kv / 2.0,
    }


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
        uniformity_pct = (
            (float(np.ptp(sample_dose)) / mean_dose * 100.0) if mean_dose > 0 else float("nan")
        )

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


# ---------------------------------------------------------------------------
# Self-test — no hardware, no Qt.  Run:  python -m rbl.hardware.raster_model
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # The installed steerer's geometry.  This module body stays free of
    # config lookups on purpose (see the SCOPE note above); the self-test is
    # allowed to import it, because a self-test that checks made-up numbers
    # is checking nothing anyone runs.
    from rbl.config.steerer_geometry import PLATE_GAP_CM, PLATE_LENGTH_CM
    l_cm, d_cm = PLATE_LENGTH_CM, PLATE_GAP_CM
    q, E_ev, L_cm = 1, 30_000.0, 100.0

    theta = deflection_mrad(1.0, l_cm, d_cm, q, E_ev)
    assert theta > 0
    x = displacement_mm(1.0, l_cm, d_cm, q, E_ev, L_cm)
    assert x > 0
    # Doubling differential kV must exactly double displacement (linear formula).
    x2 = displacement_mm(2.0, l_cm, d_cm, q, E_ev, L_cm)
    assert abs(x2 - 2 * x) < 1e-9
    print(f"[OK] 2EA021441 geometry: theta={theta:.4f} mrad/kV, x={x:.4f} mm/kV (linear in V)")

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

    # Degenerate inputs must not raise.
    assert math.isnan(deflection_mrad(1.0, l_cm, d_cm, q, 0.0))
    assert math.isnan(displacement_mm(1.0, l_cm, d_cm, q, 0.0, L_cm))
    print("[OK] degenerate inputs handled")

    print("\n[OK] raster_model self-test passed")
