"""
slit_raster_model.py
Sizing a raster that TURNS AROUND ON THE SLIT JAWS instead of on the sample.

WHY THIS EXISTS, AND WHY IT IS NOT `raster_model` WITH DIFFERENT NUMBERS
-----------------------------------------------------------------------
`raster_model.required_drive` sizes a sweep so that its turnaround happens
just past the SAMPLE edge. That is correct only if nothing between the
steerer and the sample intercepts the beam. On this beamline something does:
the BDS18 slits sit ~1 m short of the sample, and once their jaws are inside
the sweep the beam stops being a free-flying spot and starts being a
jaw-defined patch. Two things change, and neither is a tweak:

1. THE JAWS BECOME AN APERTURE, NOT A SHADOW.

   A ray only gets through the jaw at slit-plane position x_s by having been
   bent through angle x_s / z_slit at the plates, and it keeps flying at that
   angle. So it arrives at the sample at

       x_sample = x_slit * (z_sample / z_slit)

   The jaw opening is IMAGED onto the sample, magnified by the ratio of the
   two drifts. On this beamline that is 1.64x on X and 1.50x on Y -- the two
   axes differ because the Y plates sit ~17 cm upstream of the X plates and
   the Y jaws sit ~90 mm downstream of the X jaws. A SQUARE JAW OPENING DOES
   NOT PAINT A SQUARE. This is the "leak" between what the slits block and
   what shows up on the alumina.

   (Strictly, x_sample = M*x_slit - (M-1)*x_0 where x_0 is the ray's offset
   at the plates. The second term is the beam's own size at the plates
   magnified by M-1, and it blurs the edge of the patch; it does not move it.
   It is reported as an edge blur, not folded into the position.)

2. THE TURNAROUND PILE-UP LANDS ON THE JAW FACES, NOT ON THE SAMPLE.

   This is the good news and the whole reason to run this way. A triangle
   sweep has constant velocity everywhere except at its two reversals, and
   the map from slit plane to sample is linear, so the dose across the entire
   imaged patch is FLAT -- provided the reversal happens outside the jaw
   opening. The dose non-uniformity that `raster_model.dwell_uniformity`
   exists to bound is not reduced here, it is relocated onto a water-cooled
   piece of metal.

THE ONE NUMBER THAT MATTERS: HOW FAR PAST THE JAW EDGE
------------------------------------------------------
The beam has width, so the reversal has to happen far enough past the jaw
edge that the beam's TAIL has cleared it. Sweeping a Gaussian of sigma across
a hard edge and integrating over a constant-velocity pass gives the dose at
the jaw edge, relative to deep inside the opening, as exactly

    D(edge) = Phi(margin / sigma)

with `margin` the distance from the jaw edge to the sweep reversal, measured
AT THE SLIT PLANE. In beam widths (margin = k * FWHM, sigma = FWHM/2.3548):

    k = 0.5   ->  12.0 %  low at the edge
    k = 0.75  ->   3.9 %
    k = 1.0   ->   0.93 %
    k = 1.5   ->   0.021 %
    k = 2.0   ->   0.0001 %

k = 1.5 is the default: 0.02 % is far below anything measurable and past it
the extra sweep is only heating jaws.

WHERE THE OLD CODE PUT THIS MARGIN, AND WHY THAT WAS THE BUG
------------------------------------------------------------
`raster_model` applies the same k, but against the SAMPLE edge using the
FWHM AT THE SAMPLE. Under slit-limited operation both of those are the wrong
plane: the edge that matters is the jaw, and the width that matters is the
beam's width where the jaw is. The constant did not change. Its plane did.

THE FWHM THIS MODULE WANTS IS THE ONE AT THE SLIT PLANE
-------------------------------------------------------
Which is measurable: BPM80 now sits ~45 mm upstream of the X jaws -- for this
purpose the same plane -- and `profile_fwhm` already reports FWHM in mm from
it. It must be measured with the RASTER OFF, because a rastered BPM trace
gives the sweep envelope's width, not the beam's.

SCOPE
-----
Pure math. No Qt, no hardware, no config lookups. Distances arrive as plain
floats; `steerer_geometry` is what knows them.
"""
import math
from statistics import NormalDist

import numpy as np

from rbl.hardware.raster_model import deflection_mrad

# FWHM = 2*sqrt(2*ln2) * sigma
FWHM_PER_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))    # 2.35482

# EEL5000: 1 V at the generator -> 1000 V on the plate. Kept as a parameter of
# the conversion helpers rather than imported, so this module stays free of
# config lookups (see SCOPE).
DEFAULT_AMP_GAIN = 1000.0

_NORM = NormalDist()
_NAN = float("nan")


def sigma_from_fwhm(fwhm_mm: float) -> float:
    return fwhm_mm / FWHM_PER_SIGMA


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def magnification(drift_to_slit_mm: float, drift_to_target_mm: float) -> float:
    """M = z_target / z_slit, both measured from THIS AXIS'S plate exit.

    Both drifts must start at the same place -- the deflecting plates -- because
    M is the ratio of two lever arms about the same pivot. Using the flange for
    one and the plate exit for the other is a silent few-percent error.
    """
    if drift_to_slit_mm <= 0:
        return _NAN
    return drift_to_target_mm / drift_to_slit_mm


def mm_per_kv_at(drift_mm: float, plate_length_cm: float, plate_gap_cm: float,
                 charge_state: int, beam_energy_ev: float) -> float:
    """Deflection sensitivity, mm at `drift_mm` per plate-to-plate kV.

    One formula for the whole app: this calls `raster_model.deflection_mrad`
    rather than restating theta = V*l*q/(2*d*E), so a correction to the
    deflection physics cannot land in one file and not the other.
    """
    theta_mrad = deflection_mrad(1.0, plate_length_cm, plate_gap_cm,
                                 charge_state, beam_energy_ev)
    if not math.isfinite(theta_mrad):
        return _NAN
    return (theta_mrad / 1000.0) * drift_mm


def edge_blur_mm(beam_fwhm_at_plates_mm: float, magnification_: float) -> float:
    """Blur on the imaged jaw edge from the beam's own size at the plates.

    The (M-1)*x_0 term of x_sample = M*x_slit - (M-1)*x_0. It softens the patch
    edge; it does not move it, and it does not affect dwell inside the patch.
    Usually small -- and usually unmeasured, which is why it is reported
    separately instead of being folded into the painted width.
    """
    if not math.isfinite(magnification_):
        return _NAN
    return abs(magnification_ - 1.0) * beam_fwhm_at_plates_mm


# ---------------------------------------------------------------------------
# The jaw-edge dose law
# ---------------------------------------------------------------------------

def edge_dose_fraction(margin_mm: float, fwhm_mm: float) -> float:
    """Dose at a jaw edge relative to deep inside the opening: Phi(margin/sigma).

    `margin_mm` is jaw edge -> sweep reversal, at the slit plane. Negative
    means the reversal happens INSIDE the opening, i.e. the sweep never
    reaches the jaw and the patch edge is set by the turnaround instead --
    the regime this module exists to get out of.
    """
    if fwhm_mm <= 0:
        return 1.0 if margin_mm >= 0 else 0.0
    return _NORM.cdf(margin_mm / sigma_from_fwhm(fwhm_mm))


def edge_droop_pct(margin_mm: float, fwhm_mm: float) -> float:
    """How far below full dose the jaw edge sits, in percent."""
    return 100.0 * (1.0 - edge_dose_fraction(margin_mm, fwhm_mm))


def overscan_for_droop(droop_pct: float, fwhm_mm: float) -> float:
    """Inverse of `edge_droop_pct`: margin (mm) needed for a given edge droop.

    Answers the operator's question in the direction they ask it -- "I want
    the edges within 1 %, how far off the jaws do I have to sweep?" -- instead
    of making them hunt for a k that lands there.
    """
    if not (0.0 < droop_pct < 100.0) or fwhm_mm <= 0:
        return _NAN
    return _NORM.inv_cdf(1.0 - droop_pct / 100.0) * sigma_from_fwhm(fwhm_mm)


def overscan_k_for_droop(droop_pct: float) -> float:
    """`overscan_for_droop` expressed in beam widths -- independent of FWHM."""
    if not (0.0 < droop_pct < 100.0):
        return _NAN
    return _NORM.inv_cdf(1.0 - droop_pct / 100.0) / FWHM_PER_SIGMA


# ---------------------------------------------------------------------------
# Blade positions
# ---------------------------------------------------------------------------

def blade_positions_mm(painted_full_mm: float, magnification_: float,
                       painted_center_mm: float = 0.0) -> dict:
    """The two blade positions that image to a painted window on the target.

    Returns distances FROM BEAM CENTRE, in the same convention the Galil axes
    use (`hardware_config.counts_to_mm` gives mm from centre for each of X+,
    X-, Y+, Y-), so these numbers can be typed into the motor tab unchanged.

    ASYMMETRY IS SUPPORTED HERE AND IT IS NOT THE SAME LEVER AS THE SWEEP DC
    OFFSET. Moving the window with the JAWS moves what the sample sees without
    touching the drive at all -- useful when the bending magnet has already
    put the beam somewhere other than the mechanical centre. Moving it with a
    plate DC offset moves the whole sweep. They compose, and a plan usually
    wants one or the other, not both. `solve_axis` takes them separately.

    `blade_minus_mm` goes NEGATIVE if the requested window lies entirely to one
    side of the beam axis. That is geometrically fine and mechanically
    probably not -- a blade cannot usually be driven past centre -- so it is
    returned as-is and flagged rather than clamped.
    """
    if not math.isfinite(magnification_) or magnification_ == 0:
        return {"blade_plus_mm": _NAN, "blade_minus_mm": _NAN,
                "gap_mm": _NAN, "reachable": False}
    half = painted_full_mm / 2.0
    plus  = (painted_center_mm + half) / magnification_
    minus = (half - painted_center_mm) / magnification_
    return {"blade_plus_mm": plus, "blade_minus_mm": minus,
            "gap_mm": plus + minus,
            "reachable": plus >= 0.0 and minus >= 0.0}


def mechanical_blades_mm(blade_plus_mm: float, blade_minus_mm: float,
                        beam_centre_at_slit_mm: float = 0.0) -> dict:
    """Beam-frame half-gaps -> the numbers the Galil actually takes.

    THE TWO FRAMES, AND WHY CONFLATING THEM IS THE BUG
    --------------------------------------------------
    Everything else in this module works in the BEAM's frame: x = 0 is where
    the un-rastered beam sits, the sweep is symmetric about it, and the patch
    is centred on it unless someone deliberately asks otherwise. That is the
    frame the physics lives in, because the deflection is measured from
    wherever the beam already was.

    The Galil works in the SLIT's frame: x = 0 is mechanical centre, halfway
    between the two homed blades.

    Those two zeros are not the same point. The bending magnet puts the beam
    where it puts it, so the beam generally arrives some millimetres off
    mechanical centre, and the blades are opened unequally to sit
    symmetrically about IT. On this beamline that offset is currently about
    2 mm.

    An unequal pair of blade readings therefore means one of two completely
    different things:

      * the beam is off mechanical centre and the jaws have been moved to
        meet it -- the aperture is still centred ON THE BEAM, the raster is
        still centred in the aperture, and nothing about the beam-frame
        picture is asymmetric; or
      * someone deliberately wants the patch off-centre FROM THE BEAM, which
        is a real asymmetry with real consequences for margin and
        transmission.

    Only the second is a `painted_center_mm` in `solve_axis`. The first is
    this function, and it touches nothing but the two numbers that get typed
    into the motor tab.

    `beam_centre_at_slit_mm` is where the un-rastered beam sits in the slit's
    own frame, at the slit plane -- which is exactly what balanced currents on
    the four blades measure (see `beam_reconstruction`). Zero when the
    readings are even.
    """
    return {"blade_plus_mm":  blade_plus_mm + beam_centre_at_slit_mm,
            "blade_minus_mm": blade_minus_mm - beam_centre_at_slit_mm}


# ---------------------------------------------------------------------------
# Dose across the painted window
# ---------------------------------------------------------------------------

def window_dose(blade_plus_mm: float, blade_minus_mm: float,
                sweep_half_mm: float, sweep_center_mm: float,
                fwhm_mm: float, n_points: int = 401) -> dict:
    """Dose vs position across the jaw opening, at the SLIT plane.

    Exact, not simulated. A constant-velocity pass of a Gaussian beam over the
    interval [c-A, c+A] deposits, at slit-plane position x,

        D(x) = Phi((c + A - x)/sigma) - Phi((c - A - x)/sigma)

    normalised so that D = 1 deep inside a fully-swept region. Everything the
    caller wants -- edge droop, peak-to-peak uniformity, and the transmitted
    fraction -- is a functional of that one curve, so they are computed here
    together and cannot drift apart.

    `transmitted_fraction` is the share of the beam that reaches the target;
    the rest is intercepted by the jaws. Deep in the jaw-limited regime it
    tends to gap/(2A), the duty cycle of the opening within the sweep.
    """
    nan_out = {"x_mm": np.array([]), "dose": np.array([]),
               "uniformity_pct": _NAN, "transmitted_fraction": _NAN,
               "droop_plus_pct": _NAN, "droop_minus_pct": _NAN,
               "margin_plus_mm": _NAN, "margin_minus_mm": _NAN,
               "regime": "undefined"}
    gap = blade_plus_mm + blade_minus_mm
    if gap <= 0 or sweep_half_mm <= 0 or fwhm_mm <= 0:
        return nan_out

    sigma = sigma_from_fwhm(fwhm_mm)
    lo, hi = -blade_minus_mm, blade_plus_mm
    x = np.linspace(lo, hi, n_points)
    a = sweep_center_mm + sweep_half_mm
    b = sweep_center_mm - sweep_half_mm
    cdf = np.vectorize(_NORM.cdf)
    dose = cdf((a - x) / sigma) - cdf((b - x) / sigma)

    peak = float(np.max(dose))
    if peak <= 0:
        return nan_out
    mean = float(np.mean(dose))
    uniformity_pct = float(np.ptp(dose)) / mean * 100.0 if mean > 0 else _NAN

    # Transmitted fraction: dose integrated over the opening, against the
    # 2A of sweep length the beam was spread over.
    transmitted = float(np.trapezoid(dose, x)) / (2.0 * sweep_half_mm)

    margin_plus  = a - hi
    margin_minus = lo - b
    if margin_plus > 0 and margin_minus > 0:
        regime = "jaw-limited"
    elif margin_plus <= 0 and margin_minus <= 0:
        regime = "sweep-limited"
    else:
        regime = "one-sided"

    return {
        "x_mm": x,
        "dose": dose / peak,
        "uniformity_pct": uniformity_pct,
        "transmitted_fraction": transmitted,
        "droop_plus_pct":  100.0 * (1.0 - float(dose[-1]) / peak),
        "droop_minus_pct": 100.0 * (1.0 - float(dose[0]) / peak),
        "margin_plus_mm":  margin_plus,
        "margin_minus_mm": margin_minus,
        "regime": regime,
    }


# ---------------------------------------------------------------------------
# The solve: desired patch on the sample -> jaws + drive
# ---------------------------------------------------------------------------

def solve_axis(painted_full_mm: float, fwhm_at_slit_mm: float,
               drift_to_slit_mm: float, drift_to_target_mm: float,
               plate_length_cm: float, plate_gap_cm: float,
               charge_state: int, beam_energy_ev: float,
               overscan_k: float = 1.5,
               painted_center_mm: float = 0.0,
               use_sweep_offset: bool = False,
               amp_gain: float = DEFAULT_AMP_GAIN) -> dict:
    """One axis, end to end: painted patch on the target -> blades and drive.

    THE TWO WAYS TO MOVE AN OFF-CENTRE PATCH, AND WHY BOTH ARE OPTIONAL
    ------------------------------------------------------------------
    If the beam does not arrive on the mechanical axis, the painted patch has
    to be moved to meet it. There are two independent levers and the right
    one depends on what is already true of the beamline:

      * `painted_center_mm` moves it with the JAWS -- asymmetric blades. No
        DC on the plates, no extra amplitude, and the sweep stays centred.
      * `use_sweep_offset` centres the SWEEP on whatever window the jaws
        define. With symmetric jaws it does nothing. With asymmetric jaws it
        is worth having: a centred sweep must reach the FURTHER jaw with full
        margin, so the nearer jaw gets more overscan than it needs and the
        transmitted fraction drops. Centring the sweep on the window restores
        the minimum amplitude.

    Often the bending magnet has already put the beam where it should be and
    neither is wanted, which is why both default to off.

    Returns a flat dict of everything the tab prints. Voltages are
    plate-to-plate (differential) unless the key says `plate` or `gen`.
    """
    out = {"painted_full_mm": painted_full_mm,
           "painted_center_mm": painted_center_mm}

    M = magnification(drift_to_slit_mm, drift_to_target_mm)
    out["magnification"] = M

    blades = blade_positions_mm(painted_full_mm, M, painted_center_mm)
    out.update(blades)

    mm_per_kv_slit = mm_per_kv_at(drift_to_slit_mm, plate_length_cm, plate_gap_cm,
                                  charge_state, beam_energy_ev)
    mm_per_kv_target = mm_per_kv_at(drift_to_target_mm, plate_length_cm, plate_gap_cm,
                                    charge_state, beam_energy_ev)
    out["mm_per_kv_at_slit"] = mm_per_kv_slit
    out["mm_per_kv_at_target"] = mm_per_kv_target

    margin = overscan_k * fwhm_at_slit_mm
    out["overscan_margin_mm"] = margin
    out["overscan_k"] = overscan_k
    out["fwhm_at_slit_mm"] = fwhm_at_slit_mm
    out["design_droop_pct"] = edge_droop_pct(margin, fwhm_at_slit_mm)

    b_plus  = blades["blade_plus_mm"]
    b_minus = blades["blade_minus_mm"]
    if not (math.isfinite(b_plus) and math.isfinite(b_minus)):
        return out

    # The sweep has to reach `margin` past BOTH jaws.
    if use_sweep_offset:
        sweep_center = (b_plus - b_minus) / 2.0
        sweep_half   = (b_plus + b_minus) / 2.0 + margin
    else:
        sweep_center = 0.0
        sweep_half   = max(b_plus, b_minus) + margin
    out["sweep_center_at_slit_mm"] = sweep_center
    out["sweep_half_at_slit_mm"]   = sweep_half

    if not math.isfinite(mm_per_kv_slit) or mm_per_kv_slit == 0:
        return out

    amplitude_kv = sweep_half / mm_per_kv_slit
    offset_kv    = sweep_center / mm_per_kv_slit
    peak_kv      = abs(offset_kv) + amplitude_kv
    out.update({
        "amplitude_kv":     amplitude_kv,
        "offset_kv":        offset_kv,
        "peak_kv":          peak_kv,
        "ac_plate_kv":      amplitude_kv / 2.0,
        "offset_plate_kv":  offset_kv / 2.0,
        "peak_plate_kv":    peak_kv / 2.0,
    })
    out.update(generator_settings(amplitude_kv, offset_kv, amp_gain))

    # What the sweep is doing at the target, versus what actually lands.
    theta_amp_rad = amplitude_kv * mm_per_kv_slit / drift_to_slit_mm
    out["theta_amplitude_mrad"] = theta_amp_rad * 1000.0
    out["sweep_half_at_target_mm"] = amplitude_kv * mm_per_kv_target
    out["painted_min_mm"] = -b_minus * M
    out["painted_max_mm"] =  b_plus * M

    out.update({"dose_" + k: v for k, v in
                window_dose(b_plus, b_minus, sweep_half, sweep_center,
                            fwhm_at_slit_mm).items()})
    return out


def describe_axis(blade_plus_mm: float, blade_minus_mm: float,
                  amplitude_kv: float, offset_kv: float,
                  fwhm_at_slit_mm: float,
                  drift_to_slit_mm: float, drift_to_target_mm: float,
                  plate_length_cm: float, plate_gap_cm: float,
                  charge_state: int, beam_energy_ev: float) -> dict:
    """The other direction: given jaws and a drive, what lands on the target.

    The forward check on `solve_axis`, and the honest answer when the settings
    on the machine are not the ones the solve asked for. Crucially this is the
    call that names the SWEEP-LIMITED case -- the sweep reversing inside the
    jaw opening, which puts the turnaround pile-up back on the sample and is
    the failure the whole slit-limited scheme exists to avoid. It cannot be
    seen from `solve_axis`, which constructs settings that never do it.
    """
    M = magnification(drift_to_slit_mm, drift_to_target_mm)
    mm_per_kv_slit = mm_per_kv_at(drift_to_slit_mm, plate_length_cm, plate_gap_cm,
                                  charge_state, beam_energy_ev)
    mm_per_kv_target = mm_per_kv_at(drift_to_target_mm, plate_length_cm, plate_gap_cm,
                                    charge_state, beam_energy_ev)
    sweep_half   = amplitude_kv * mm_per_kv_slit
    sweep_center = offset_kv * mm_per_kv_slit

    dose = window_dose(blade_plus_mm, blade_minus_mm, sweep_half, sweep_center,
                       fwhm_at_slit_mm)

    # In the sweep-limited regime the patch edge is the sweep, not the jaw, so
    # the painted extent is whichever comes first on each side.
    painted_max = min(blade_plus_mm,  sweep_center + sweep_half) * M
    painted_min = max(-blade_minus_mm, sweep_center - sweep_half) * M

    out = {
        "magnification": M,
        "mm_per_kv_at_slit": mm_per_kv_slit,
        "mm_per_kv_at_target": mm_per_kv_target,
        "sweep_half_at_slit_mm": sweep_half,
        "sweep_center_at_slit_mm": sweep_center,
        "sweep_half_at_target_mm": amplitude_kv * mm_per_kv_target,
        "painted_min_mm": painted_min,
        "painted_max_mm": painted_max,
        "painted_full_mm": painted_max - painted_min,
        "painted_center_mm": (painted_max + painted_min) / 2.0,
    }
    out.update({"dose_" + k: v for k, v in dose.items()})
    return out


# ---------------------------------------------------------------------------
# Drive <-> generator
# ---------------------------------------------------------------------------

def generator_settings(amplitude_kv: float, offset_kv: float,
                       amp_gain: float = DEFAULT_AMP_GAIN) -> dict:
    """Plate-to-plate kV -> what to type on ONE generator channel.

    THE COINCIDENCE THAT HAS TO BE LABELLED. At a gain of 1000 the number of
    generator VOLTS peak-to-peak equals the number of plate-to-plate KILOVOLTS
    exactly, and equals twice the per-plate peak kV. Three different quantities
    with two distinct values; printing any of them bare is how the wrong one
    gets set. Every key here says whose volts it is.

    The pair runs push-pull -- 180 degrees apart, and the DC term EQUAL AND
    OPPOSITE, because a common-mode offset cancels across the plates and moves
    nothing.
    """
    scale = amp_gain / 1000.0        # kV on a plate per generator volt
    if scale == 0:
        return {"gen_amp_vpp": _NAN, "gen_offset_v": _NAN, "gen_peak_v": _NAN}
    gen_amp_vpp  = amplitude_kv / scale
    gen_offset_v = (offset_kv / 2.0) / scale
    return {
        "gen_amp_vpp":  gen_amp_vpp,
        "gen_offset_v": gen_offset_v,
        "gen_peak_v":   abs(gen_offset_v) + abs(gen_amp_vpp) / 2.0,
    }


# ---------------------------------------------------------------------------
# Down the beamline
# ---------------------------------------------------------------------------

def envelope_at(drift_mm: float, drift_to_slit_mm: float,
                amplitude_kv: float, offset_kv: float, mm_per_kv_at_slit: float,
                blade_plus_mm: float, blade_minus_mm: float) -> dict:
    """Where the beam can be at one plane -- commanded sweep and what survives.

    Two different envelopes, and the difference between them is the point:

      `sweep_*`  what the steerer commands. Grows linearly with drift forever.
                 It is what an aperture UPSTREAM of the jaws has to clear,
                 because up there the whole sweep is still flying.

      `passed_*` what is left after the jaws. Downstream of the slit plane the
                 beam is confined to the cone the jaws admit, so this stops
                 growing as the sweep and starts growing as the jaw opening --
                 which is exactly why the patch on the sample is the jaw
                 opening magnified, and not the sweep.

    Upstream of the slit plane the two are identical, because nothing has been
    intercepted yet.
    """
    if drift_to_slit_mm <= 0 or not math.isfinite(mm_per_kv_at_slit):
        return {"sweep_min_mm": _NAN, "sweep_max_mm": _NAN,
                "passed_min_mm": _NAN, "passed_max_mm": _NAN, "clipped": False}
    ratio = drift_mm / drift_to_slit_mm
    sweep_half   = amplitude_kv * mm_per_kv_at_slit * ratio
    sweep_center = offset_kv * mm_per_kv_at_slit * ratio
    sweep_min, sweep_max = sweep_center - sweep_half, sweep_center + sweep_half

    # STRICTLY less-than: at the jaw plane itself the jaws are already
    # cutting, so that row must report the opening and not the sweep. With
    # <= here the slit row of the plane table showed its own axis unclipped
    # -- the one row where the clipping is the whole point.
    if drift_mm < drift_to_slit_mm:
        return {"sweep_min_mm": sweep_min, "sweep_max_mm": sweep_max,
                "passed_min_mm": sweep_min, "passed_max_mm": sweep_max,
                "clipped": False}
    return {"sweep_min_mm": sweep_min, "sweep_max_mm": sweep_max,
            "passed_min_mm": max(sweep_min, -blade_minus_mm * ratio),
            "passed_max_mm": min(sweep_max,  blade_plus_mm * ratio),
            "clipped": True}


if __name__ == "__main__":
    from rbl.config.steerer_geometry import (
        DRIFT_TO_SAMPLE_CM,
        PLATE_GAP_CM,
        PLATE_LENGTH_CM,
        drift_mm_for,
        slit_plane_z_mm,
    )
    l_cm, d_cm, q, E = PLATE_LENGTH_CM, PLATE_GAP_CM, 1, 3.0e6
    z_sample = DRIFT_TO_SAMPLE_CM * 10.0

    print("edge droop vs overscan (k in beam widths at the slit plane)")
    for k in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
        print(f"   k={k:<5} droop {edge_droop_pct(k * 1.0, 1.0):9.5f} %")
    assert edge_droop_pct(1.5, 1.0) < 0.05
    assert edge_droop_pct(0.5, 1.0) > 10.0
    # ...and the inverse agrees with the forward.
    for pct in (0.02, 0.93, 3.87):
        k = overscan_k_for_droop(pct)
        assert abs(edge_droop_pct(k * 1.0, 1.0) - pct) < 1e-6, (pct, k)
    print("[OK] droop law inverts")

    print("\n5 x 10 mm on the sample, 1.0 mm FWHM at the slits, 3 MeV protons:")
    for ax, want in (("X", 5.0), ("Y", 10.0)):
        ds = drift_mm_for(ax, slit_plane_z_mm(ax))
        dp = drift_mm_for(ax, z_sample)
        r = solve_axis(want, 1.0, ds, dp, l_cm, d_cm, q, E, overscan_k=1.5)
        print(f"  {ax}: M={r['magnification']:.4f}  blades "
              f"{r['blade_plus_mm']:.3f}/{r['blade_minus_mm']:.3f} mm  "
              f"sweep +/-{r['sweep_half_at_slit_mm']:.3f} mm at slit  "
              f"{r['amplitude_kv']:.3f} kV p-p  {r['gen_amp_vpp']:.3f} Vpp/ch  "
              f"{r['peak_plate_kv']:.3f} kV/plate  "
              f"through {100 * r['dose_transmitted_fraction']:.1f} %  "
              f"droop {r['dose_droop_plus_pct']:.4f} %")
        # The patch really is the jaws imaged, and it really is the size asked for.
        assert abs((r["painted_max_mm"] - r["painted_min_mm"]) - want) < 1e-9
        assert r["dose_regime"] == "jaw-limited"
        assert r["dose_uniformity_pct"] < 0.1

        # Forward and reverse must agree.
        back = describe_axis(r["blade_plus_mm"], r["blade_minus_mm"],
                             r["amplitude_kv"], r["offset_kv"], 1.0, ds, dp,
                             l_cm, d_cm, q, E)
        assert abs(back["painted_full_mm"] - want) < 1e-6, back["painted_full_mm"]

    # A sweep too small to reach the jaws must be NAMED, not silently accepted.
    ds = drift_mm_for("X", slit_plane_z_mm("X"))
    dp = drift_mm_for("X", z_sample)
    r = solve_axis(5.0, 1.0, ds, dp, l_cm, d_cm, q, E, overscan_k=1.5)
    weak = describe_axis(r["blade_plus_mm"], r["blade_minus_mm"],
                         r["amplitude_kv"] * 0.3, 0.0, 1.0, ds, dp,
                         l_cm, d_cm, q, E)
    assert weak["dose_regime"] == "sweep-limited", weak["dose_regime"]
    assert weak["painted_full_mm"] < 5.0
    print(f"\n[OK] under-driven sweep flagged 'sweep-limited', paints only "
          f"{weak['painted_full_mm']:.3f} mm of the 5.000 mm window")

    # Asymmetric jaws: centring the sweep on the window buys back transmission.
    off = solve_axis(5.0, 1.0, ds, dp, l_cm, d_cm, q, E, painted_center_mm=1.5,
                     use_sweep_offset=False)
    on  = solve_axis(5.0, 1.0, ds, dp, l_cm, d_cm, q, E, painted_center_mm=1.5,
                     use_sweep_offset=True)
    assert on["amplitude_kv"] < off["amplitude_kv"]
    assert on["dose_transmitted_fraction"] > off["dose_transmitted_fraction"]
    print(f"[OK] off-centre patch: sweep offset OFF {off['amplitude_kv']:.3f} kV / "
          f"{100 * off['dose_transmitted_fraction']:.1f} % through,  "
          f"ON {on['amplitude_kv']:.3f} kV / "
          f"{100 * on['dose_transmitted_fraction']:.1f} % through")

    print("\n[OK] slit_raster_model self-test passed")
