"""
raster_plan.py
Pure-physics helpers extracted from gui/raster_planner_tab.py.

WHY THIS EXISTS
---------------
The raster planner tab is the research deliverable — its numbers get defended
in write-ups.  Math that lives inside a widget method cannot be unit-tested, so
outputs are only as trustworthy as the operator's eyeballs.

This module holds the computations that were previously embedded in the widget:

  steerer_limited_solve  — drive amplitude + slit-jaw floor for the mode where
                           the slits are parked open and the sweep edge defines
                           the patch boundary (``_steerer_limited`` in the tab).

  envelope_status        — per-channel operating-envelope check: which channel
                           has the least headroom, whether it is inside the
                           walls, and a ``walls`` dict for plotting.  The widget
                           calls this and renders the result; the computation
                           lives here so it can be verified without Qt.

SCOPE
-----
Pure math.  No Qt, no widget state, no config lookups that depend on runtime
state.  Takes plain floats/dicts and returns plain floats/dicts or dataclasses.
All config constants (CAL_MAX_KV, AMP_MAX_BANDWIDTH_HZ, …) are imported from
config/, not embedded as literals.
"""
import math

import numpy as np

from rbl.config.calibration_config import CAL_AC_TRIP_MA, CAL_MAX_KV
from rbl.config.raster_defaults import AMP_MAX_BANDWIDTH_HZ
from rbl.hardware.load_model import envelope_walls
from rbl.hardware.raster_model import required_drive, dwell_uniformity
from rbl.hardware import slit_raster_model as srm


# ---------------------------------------------------------------------------
# Steerer-limited solve
# ---------------------------------------------------------------------------

def steerer_limited_solve(
        width_mm: float,
        centre_mm: float,
        fwhm_mm: float,
        k: float,
        drift_to_slit_mm: float,
        drift_to_sample_mm: float,
        l_cm: float,
        d_cm: float,
        charge: int,
        energy_ev: float,
) -> dict:
    """Compute the drive solution for steerer-limited mode (slits parked open).

    In steerer-limited mode the sweep reversal is the patch edge, so the beam's
    own width smears the edge and the reversal dwell lands on the sample.  This
    is the mode that reproduces the lab sheet's published figures.

    Args:
        width_mm:             Desired painted width at the sample (mm).
        centre_mm:            Sweep centre offset at the sample (mm, 0 = on-axis).
        fwhm_mm:              Beam FWHM measured at the slit plane (mm).
        k:                    Turnaround factor: reversal happens k * fwhm_mm
                              past the patch edge.
        drift_to_slit_mm:     Distance from the deflecting plates to the slit (mm).
        drift_to_sample_mm:   Distance from the deflecting plates to the sample (mm).
        l_cm:                 Deflecting-plate length (cm).
        d_cm:                 Deflecting-plate gap (cm).
        charge:               Beam charge state (integer, e.g. 1 for proton).
        energy_ev:            Beam kinetic energy (eV).

    Returns:
        Dict with all keys from ``raster_model.required_drive`` plus the
        slit-plane geometry keys added by this mode:

          mm_per_kv_at_slit    — sensitivity at the slit plane (mm kV⁻¹)
          magnification        — slit-to-sample magnification
          sweep_half_at_slit   — half-span at the slit (mm)
          sweep_center_at_slit — centre offset at the slit (mm)
          sweep_half_at_target — half-span at the sample (mm)
          painted_min_mm       — patch near edge (mm, in beam frame)
          painted_max_mm       — patch far edge (mm, in beam frame)
          painted_full_mm      — requested patch width (== width_mm)
          painted_center_mm    — requested centre (== centre_mm)
          blade_plus_mm        — minimum jaw-plus opening to clear this sweep
          blade_minus_mm       — minimum jaw-minus opening to clear this sweep
          dose_uniformity_pct  — dwell-uniformity figure of merit (%)
          dose_transmitted_fraction — NaN (steerer-limited; no jaw clipping)
          dose_regime          — "steerer-limited"
          dose_droop_plus_pct  — NaN (no jaw clipping in this mode)
          dose_droop_minus_pct — NaN
          dose_x_mm            — profile position array (mm)
          dose_dose            — normalised dose profile array
          fwhm_at_slit_mm      — fwhm_mm (slit plane = where the beam is measured)
          ac_plate_kv          — peak plate voltage for the AC raster
          peak_plate_kv        — worst-case instantaneous plate voltage (with offset)
    """
    drive = required_drive(
        target_width_mm=width_mm, fwhm_mm=fwhm_mm,
        plate_length_cm=l_cm, plate_gap_cm=d_cm, charge_state=charge,
        beam_energy_ev=energy_ev, drift_cm=drift_to_sample_mm / 10.0,
        turnaround_k=k, center_offset_mm=centre_mm)

    out = dict(drive)
    mm_per_kv_slit = srm.mm_per_kv_at(drift_to_slit_mm, l_cm, d_cm, charge, energy_ev)
    amp_kv, off_kv = drive["amplitude_kv"], drive["offset_kv"]

    out["mm_per_kv_at_slit"]       = mm_per_kv_slit
    out["magnification"]           = srm.magnification(drift_to_slit_mm, drift_to_sample_mm)
    out["sweep_half_at_slit_mm"]   = amp_kv * mm_per_kv_slit
    out["sweep_center_at_slit_mm"] = off_kv * mm_per_kv_slit
    out["sweep_half_at_target_mm"] = drive["scan_half_span_mm"]
    out["painted_min_mm"]          = drive["scan_min_mm"]
    out["painted_max_mm"]          = drive["scan_max_mm"]
    out["painted_full_mm"]         = width_mm
    out["painted_center_mm"]       = centre_mm
    # Minimum jaw opening that would clear this sweep (not a setpoint — a floor).
    out["blade_plus_mm"]  = out["sweep_center_at_slit_mm"] + out["sweep_half_at_slit_mm"]
    out["blade_minus_mm"] = out["sweep_half_at_slit_mm"]   - out["sweep_center_at_slit_mm"]
    out.update(srm.generator_settings(amp_kv, off_kv))

    du = dwell_uniformity(fwhm_mm=fwhm_mm,
                          scan_half_width_mm=drive["scan_half_span_mm"],
                          sample_half_width_mm=drive["sample_half_mm"])
    out["dose_uniformity_pct"]        = du["uniformity_pct"]
    out["dose_transmitted_fraction"]  = float("nan")
    out["dose_regime"]                = "steerer-limited"
    out["dose_droop_plus_pct"]        = float("nan")
    out["dose_droop_minus_pct"]       = float("nan")
    out["dose_x_mm"]                  = du["profile_mm"]
    out["dose_dose"] = (du["profile_dose"] / np.max(du["profile_dose"])
                        if du["profile_dose"].size else du["profile_dose"])
    out["fwhm_at_slit_mm"] = fwhm_mm
    return out


# ---------------------------------------------------------------------------
# Envelope status
# ---------------------------------------------------------------------------

_DEFAULT_AXIS_OF_CHANNEL = {"X+": "X", "X-": "X", "Y+": "Y", "Y-": "Y"}


def envelope_status(
        caps: dict,
        plate_kv: dict,
        freq_of_axis: dict,
        solution: dict,
        axis_of_channel: dict | None = None,
) -> dict:
    """Check whether the operating point is inside the amplifier envelope.

    Evaluates each channel against its own measured capacitance (or fallback)
    and reports the channel with the least headroom.

    Args:
        caps:            {amp_label: (c_pf, source_str)} — from the tab's
                         ``_channel_capacitance()`` or a test fixture.
        plate_kv:        {axis: peak_plate_kv} — AC amplitude per axis.
        freq_of_axis:    {axis: freq_hz} — drive frequency per axis.
        solution:        {axis: solve_dict} — the full per-axis solution, used
                         to check ``peak_plate_kv`` (offset included) against
                         the voltage ceiling.
        axis_of_channel: {amp_label: axis} mapping.  Defaults to the standard
                         X+/X-/Y+/Y- layout.

    Returns:
        Dict with:
          in_envelope    — True if the worst channel is within the walls AND
                           no axis exceeds the voltage ceiling.
          worst_label    — amp label of the tightest channel.
          ratio          — kv / env_kv for the worst channel (>1 → outside).
          kv             — requested kV for the worst channel.
          freq_hz        — drive frequency for the worst channel.
          env_kv         — envelope kV limit at that frequency (NaN if beyond
                           the bandwidth wall).
          walls          — ``envelope_walls()`` output for the worst channel.
          over_ceiling   — list of axes whose peak_plate_kv exceeds CAL_MAX_KV.
          exceeded_bandwidth — True if the worst channel's frequency is above
                              AMP_MAX_BANDWIDTH_HZ.
    """
    aoc = axis_of_channel if axis_of_channel is not None else _DEFAULT_AXIS_OF_CHANNEL

    worst = None  # (headroom_ratio, label, kv, freq_hz, env_kv, walls)
    for label, (c_pf, _src) in caps.items():
        axis = aoc[label]
        f    = freq_of_axis[axis]
        kv   = plate_kv[axis]
        walls = envelope_walls(load_pf=c_pf, trip_ma=CAL_AC_TRIP_MA,
                               shape="triangle", max_kv=CAL_MAX_KV,
                               max_f_hz=AMP_MAX_BANDWIDTH_HZ)
        if f <= AMP_MAX_BANDWIDTH_HZ:
            env_kv = float(np.interp(f, walls["freq_hz"], walls["envelope_kv"]))
        else:
            env_kv = float("nan")
        ratio = (kv / env_kv) if (math.isfinite(env_kv) and env_kv > 0) else float("inf")
        if worst is None or ratio > worst[0]:
            worst = (ratio, label, kv, f, env_kv, walls)

    ratio, label, kv, f, env_kv, walls = worst

    exceeded_bandwidth = not math.isfinite(env_kv)
    in_envelope = (not exceeded_bandwidth) and (kv <= env_kv)

    over_ceiling = [
        axis for axis, d in solution.items()
        if math.isfinite(d.get("peak_plate_kv", float("nan")))
        and d["peak_plate_kv"] > CAL_MAX_KV
    ]
    if over_ceiling:
        in_envelope = False

    return {
        "in_envelope":          in_envelope,
        "worst_label":          label,
        "ratio":                ratio,
        "kv":                   kv,
        "freq_hz":              f,
        "env_kv":               env_kv,
        "walls":                walls,
        "over_ceiling":         over_ceiling,
        "exceeded_bandwidth":   exceeded_bandwidth,
    }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from rbl.config.steerer_geometry import PLATE_LENGTH_CM, PLATE_GAP_CM

    # --- steerer_limited_solve ---
    sol = steerer_limited_solve(
        width_mm=20.0, centre_mm=0.0, fwhm_mm=5.0, k=1.0,
        drift_to_slit_mm=500.0, drift_to_sample_mm=800.0,
        l_cm=PLATE_LENGTH_CM, d_cm=PLATE_GAP_CM,
        charge=1, energy_ev=3e6,
    )
    assert "amplitude_kv"        in sol, "missing amplitude_kv"
    assert "blade_plus_mm"       in sol, "missing blade_plus_mm"
    assert "dose_uniformity_pct" in sol, "missing dose_uniformity_pct"
    assert sol["dose_regime"] == "steerer-limited"
    assert sol["painted_full_mm"] == 20.0
    assert sol["fwhm_at_slit_mm"] == 5.0
    print(f"[OK] steerer_limited_solve: amp={sol['amplitude_kv']:.4f} kV, "
          f"blade+={sol['blade_plus_mm']:.3f} mm")

    # Symmetric sweep (centre=0): blade_plus == blade_minus
    assert abs(sol["blade_plus_mm"] - sol["blade_minus_mm"]) < 1e-9, \
        "symmetric sweep must have equal blade clearances"
    print("[OK] blade clearances equal for centred sweep")

    # --- envelope_status — inside ---
    caps_in   = {"X+": (500.0, "measured"), "X-": (500.0, "measured"),
                 "Y+": (500.0, "measured"), "Y-": (500.0, "measured")}
    plate_in  = {"X": 1.0, "Y": 1.0}
    freq_in   = {"X": 10.0, "Y": 10.0}
    fake_sol  = {"X": {"peak_plate_kv": 1.0}, "Y": {"peak_plate_kv": 1.0}}
    status_in = envelope_status(caps_in, plate_in, freq_in, fake_sol)
    assert status_in["in_envelope"], f"expected inside envelope, got {status_in}"
    assert status_in["over_ceiling"] == []
    print(f"[OK] envelope_status inside: ratio={status_in['ratio']:.3f}")

    # --- envelope_status — outside (very high kV) ---
    plate_out = {"X": 5.5, "Y": 5.5}
    fake_sol2 = {"X": {"peak_plate_kv": 5.5}, "Y": {"peak_plate_kv": 5.5}}
    status_out = envelope_status(caps_in, plate_out, freq_in, fake_sol2)
    assert not status_out["in_envelope"], "expected outside envelope"
    print(f"[OK] envelope_status outside: ratio={status_out['ratio']:.3f}")

    # --- envelope_status — beyond bandwidth ---
    freq_bw = {"X": AMP_MAX_BANDWIDTH_HZ * 2, "Y": AMP_MAX_BANDWIDTH_HZ * 2}
    status_bw = envelope_status(caps_in, plate_in, freq_bw, fake_sol)
    assert not status_bw["in_envelope"]
    assert status_bw["exceeded_bandwidth"]
    print("[OK] envelope_status beyond bandwidth: exceeded_bandwidth=True")

    print("\n[OK] raster_plan self-test passed")
