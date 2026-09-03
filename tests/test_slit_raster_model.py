"""
Tests for rbl.hardware.slit_raster_model — the jaw-limited raster.

The three things worth pinning, because getting any of them wrong is
invisible at the screen and expensive at the beamline:

  1. The jaw opening is IMAGED onto the sample, magnified by the ratio of the
     drifts, and the two axes do not share a magnification.
  2. The overscan margin is measured from the JAW EDGE against the FWHM AT
     THE SLIT PLANE — not from the sample edge against the FWHM at the
     sample, which is what the old code did.
  3. A sweep that reverses INSIDE the jaw opening must be named, because it
     looks like a working plan and puts the turnaround pile-up back on the
     sample.
"""
import math

import pytest

from rbl.config.steerer_geometry import (
    DRIFT_TO_SAMPLE_CM,
    PLATE_GAP_CM,
    PLATE_LENGTH_CM,
    drift_mm_for,
    slit_plane_z_mm,
)
from rbl.hardware import slit_raster_model as srm

L_CM, D_CM = PLATE_LENGTH_CM, PLATE_GAP_CM
Q, E_EV = 1, 3.0e6
Z_SAMPLE_MM = DRIFT_TO_SAMPLE_CM * 10.0


def drifts(axis):
    return (drift_mm_for(axis, slit_plane_z_mm(axis)),
            drift_mm_for(axis, Z_SAMPLE_MM))


def solve(axis, painted, **kw):
    d_slit, d_sample = drifts(axis)
    kw.setdefault("overscan_k", 1.5)
    return srm.solve_axis(painted, kw.pop("fwhm", 1.0), d_slit, d_sample,
                          L_CM, D_CM, Q, E_EV, **kw)


class TestMagnification:
    def test_it_is_the_ratio_of_the_two_drifts(self):
        assert srm.magnification(1000.0, 2500.0) == pytest.approx(2.5)

    def test_the_two_axes_do_not_share_one(self):
        mx = srm.magnification(*drifts("X"))
        my = srm.magnification(*drifts("Y"))
        assert mx == pytest.approx(1.6425, abs=1e-3)
        assert my == pytest.approx(1.4976, abs=1e-3)
        # ~10 % apart. A square jaw opening does not paint a square, and this
        # is the number that says so.
        assert abs(mx - my) > 0.1

    def test_a_square_jaw_opening_does_not_paint_a_square(self):
        gap = 3.0
        x = gap * srm.magnification(*drifts("X"))
        y = gap * srm.magnification(*drifts("Y"))
        assert abs(x - y) > 0.25

    def test_undefined_before_the_slit_plane(self):
        assert math.isnan(srm.magnification(0.0, 2500.0))


class TestTheJawEdgeDoseLaw:
    """Phi(margin/sigma), pinned at the values the tab quotes to the
    operator. These are the numbers a run gets planned against."""

    @pytest.mark.parametrize("k, droop", [
        (0.5, 11.9516), (0.75, 3.8688), (1.0, 0.9266),
        (1.25, 0.1622), (1.5, 0.0206), (2.0, 0.00012408),
    ])
    def test_droop_at_k_beam_widths(self, k, droop):
        assert srm.edge_droop_pct(k * 1.0, 1.0) == pytest.approx(droop, rel=1e-3)

    def test_it_scales_with_fwhm_not_with_absolute_mm(self):
        # Twice the beam width needs twice the margin for the same droop.
        assert (srm.edge_droop_pct(3.0, 2.0)
                == pytest.approx(srm.edge_droop_pct(1.5, 1.0)))

    def test_the_inverse_agrees_with_the_forward(self):
        for pct in (0.02, 0.93, 3.87, 12.0):
            m = srm.overscan_for_droop(pct, 1.7)
            assert srm.edge_droop_pct(m, 1.7) == pytest.approx(pct, rel=1e-9)

    def test_k_for_a_droop_is_fwhm_independent(self):
        k = srm.overscan_k_for_droop(1.0)
        for fwhm in (0.3, 1.0, 4.2):
            assert srm.edge_droop_pct(k * fwhm, fwhm) == pytest.approx(1.0, rel=1e-9)

    def test_a_reversal_inside_the_jaw_is_below_half_dose(self):
        assert srm.edge_droop_pct(-0.5, 1.0) > 50.0


class TestSolveGivesTheRequestedPatch:
    def test_the_patch_is_exactly_the_size_asked_for(self):
        for axis, want in (("X", 5.0), ("Y", 10.0)):
            r = solve(axis, want)
            assert r["painted_max_mm"] - r["painted_min_mm"] == pytest.approx(want)

    def test_the_blades_are_the_patch_divided_by_the_magnification(self):
        r = solve("X", 5.0)
        assert r["blade_plus_mm"] == pytest.approx(2.5 / r["magnification"])
        assert r["gap_mm"] * r["magnification"] == pytest.approx(5.0)

    def test_the_blades_are_narrower_than_the_patch(self):
        # The whole point: set the jaws to the size you want on the sample
        # and you get a patch ~64 % too big.
        r = solve("X", 5.0)
        assert r["gap_mm"] < 5.0

    def test_the_design_lands_in_the_jaw_limited_regime(self):
        assert solve("X", 5.0)["dose_regime"] == "jaw-limited"

    def test_the_dose_across_the_patch_is_flat(self):
        assert solve("X", 5.0)["dose_uniformity_pct"] < 0.05

    def test_the_edge_droop_matches_the_k_that_was_asked_for(self):
        r = solve("X", 5.0, overscan_k=1.0, fwhm=1.0)
        assert r["dose_droop_plus_pct"] == pytest.approx(0.9266, rel=2e-3)


class TestTheMarginIsAtTheJawNotTheSample:
    def test_the_overscan_is_measured_in_slit_plane_millimetres(self):
        r = solve("X", 5.0, overscan_k=1.5, fwhm=1.2)
        assert r["overscan_margin_mm"] == pytest.approx(1.8)
        assert (r["sweep_half_at_slit_mm"] - r["blade_plus_mm"]
                == pytest.approx(1.8))

    def test_a_wider_beam_costs_more_amplitude(self):
        narrow = solve("X", 5.0, fwhm=0.5)
        wide = solve("X", 5.0, fwhm=2.0)
        assert wide["amplitude_kv"] > narrow["amplitude_kv"]

    def test_the_beam_width_does_not_change_the_blades(self):
        # The patch size is set by the jaws and the magnification alone. The
        # FWHM buys overscan, not width — mixing the two is the old bug.
        narrow = solve("X", 5.0, fwhm=0.5)
        wide = solve("X", 5.0, fwhm=2.0)
        assert narrow["blade_plus_mm"] == pytest.approx(wide["blade_plus_mm"])

    def test_more_overscan_costs_transmission(self):
        tight = solve("X", 5.0, overscan_k=0.5)
        loose = solve("X", 5.0, overscan_k=3.0)
        assert loose["dose_transmitted_fraction"] < tight["dose_transmitted_fraction"]
        assert loose["dose_droop_plus_pct"] < tight["dose_droop_plus_pct"]

    def test_transmission_tends_to_the_duty_cycle_of_the_opening(self):
        r = solve("X", 5.0, overscan_k=3.0, fwhm=1.0)
        duty = r["gap_mm"] / (2.0 * r["sweep_half_at_slit_mm"])
        assert r["dose_transmitted_fraction"] == pytest.approx(duty, rel=0.02)


class TestForwardAndReverseAgree:
    def test_describe_reproduces_what_solve_designed(self):
        d_slit, d_sample = drifts("X")
        r = solve("X", 5.0)
        back = srm.describe_axis(r["blade_plus_mm"], r["blade_minus_mm"],
                                 r["amplitude_kv"], r["offset_kv"], 1.0,
                                 d_slit, d_sample, L_CM, D_CM, Q, E_EV)
        assert back["painted_full_mm"] == pytest.approx(5.0)
        assert back["dose_regime"] == "jaw-limited"

    def test_an_underdriven_sweep_is_named_sweep_limited(self):
        d_slit, d_sample = drifts("X")
        r = solve("X", 5.0)
        weak = srm.describe_axis(r["blade_plus_mm"], r["blade_minus_mm"],
                                 r["amplitude_kv"] * 0.3, 0.0, 1.0,
                                 d_slit, d_sample, L_CM, D_CM, Q, E_EV)
        assert weak["dose_regime"] == "sweep-limited"
        # ...and it paints LESS than the jaws would allow, which is how the
        # failure shows up on the alumina.
        assert weak["painted_full_mm"] < 5.0

    def test_a_sweep_limited_pass_is_not_uniform(self):
        d_slit, d_sample = drifts("X")
        r = solve("X", 5.0)
        weak = srm.describe_axis(r["blade_plus_mm"], r["blade_minus_mm"],
                                 r["amplitude_kv"] * 0.3, 0.0, 1.0,
                                 d_slit, d_sample, L_CM, D_CM, Q, E_EV)
        assert weak["dose_uniformity_pct"] > r["dose_uniformity_pct"] * 10


class TestAsymmetry:
    def test_an_off_centre_patch_opens_one_blade_further(self):
        r = solve("X", 5.0, painted_center_mm=1.0)
        assert r["blade_plus_mm"] > r["blade_minus_mm"]
        assert r["gap_mm"] * r["magnification"] == pytest.approx(5.0)

    def test_the_patch_moves_by_what_was_asked(self):
        r = solve("X", 5.0, painted_center_mm=1.0)
        centre = (r["painted_max_mm"] + r["painted_min_mm"]) / 2.0
        assert centre == pytest.approx(1.0)

    def test_centring_the_sweep_buys_back_amplitude_and_transmission(self):
        off = solve("X", 5.0, painted_center_mm=1.5, use_sweep_offset=False)
        on = solve("X", 5.0, painted_center_mm=1.5, use_sweep_offset=True)
        assert on["amplitude_kv"] < off["amplitude_kv"]
        assert on["dose_transmitted_fraction"] > off["dose_transmitted_fraction"]

    def test_the_sweep_offset_does_nothing_when_the_jaws_are_symmetric(self):
        off = solve("X", 5.0, use_sweep_offset=False)
        on = solve("X", 5.0, use_sweep_offset=True)
        assert on["amplitude_kv"] == pytest.approx(off["amplitude_kv"])
        assert on["offset_kv"] == pytest.approx(0.0)

    def test_a_blade_that_would_cross_centre_is_reported_not_clamped(self):
        r = solve("X", 2.0, painted_center_mm=5.0)
        assert r["blade_minus_mm"] < 0
        assert r["reachable"] is False


class TestGeneratorConversion:
    """Three quantities, two distinct values, one gain of 1000. Every one of
    them has to carry its owner."""

    def test_vpp_equals_the_differential_kv_at_gain_1000(self):
        g = srm.generator_settings(6.63, 0.0)
        assert g["gen_amp_vpp"] == pytest.approx(6.63)

    def test_the_plate_sees_half_the_differential(self):
        r = solve("X", 5.0)
        assert r["ac_plate_kv"] == pytest.approx(r["amplitude_kv"] / 2.0)
        assert r["gen_amp_vpp"] == pytest.approx(2 * r["ac_plate_kv"])

    def test_the_dc_term_is_half_the_differential_on_each_channel(self):
        g = srm.generator_settings(0.0, 2.0)
        assert g["gen_offset_v"] == pytest.approx(1.0)

    def test_the_peak_is_offset_plus_half_the_amplitude(self):
        g = srm.generator_settings(4.0, 2.0)
        assert g["gen_peak_v"] == pytest.approx(1.0 + 2.0)


class TestEnvelopeDownTheBeamline:
    def test_upstream_of_the_jaws_nothing_is_clipped(self):
        d_slit, _ = drifts("X")
        e = srm.envelope_at(d_slit * 0.5, d_slit, 3.0, 0.0, 0.9, 1.5, 1.5)
        assert e["passed_min_mm"] == pytest.approx(e["sweep_min_mm"])
        assert e["passed_max_mm"] == pytest.approx(e["sweep_max_mm"])
        assert e["clipped"] is False

    def test_the_jaw_plane_itself_reports_the_opening_not_the_sweep(self):
        # Regression: with a <= boundary the slit row of the plane table
        # showed its own axis UNCLIPPED — the one row where the clipping is
        # the entire point of looking.
        d_slit, _ = drifts("X")
        e = srm.envelope_at(d_slit, d_slit, 3.0, 0.0, 0.9, 1.5, 1.5)
        assert e["passed_max_mm"] == pytest.approx(1.5)
        assert e["sweep_max_mm"] > e["passed_max_mm"]
        assert e["clipped"] is True

    def test_downstream_the_commanded_sweep_outruns_the_beam(self):
        d_slit, d_sample = drifts("X")
        e = srm.envelope_at(d_sample, d_slit, 3.0, 0.0, 0.9, 1.5, 1.5)
        assert e["sweep_max_mm"] > e["passed_max_mm"]
        assert e["clipped"] is True

    def test_the_passed_beam_grows_as_the_jaw_opening_not_the_sweep(self):
        d_slit, d_sample = drifts("X")
        e = srm.envelope_at(d_sample, d_slit, 3.0, 0.0, 0.9, 1.5, 1.5)
        assert e["passed_max_mm"] == pytest.approx(
            1.5 * srm.magnification(d_slit, d_sample))


class TestTheBeamFrameAndTheSlitFrame:
    """An unequal pair of blades means one of two completely different
    things, and the model has to keep them apart."""

    def test_a_centred_beam_leaves_the_blades_alone(self):
        m = srm.mechanical_blades_mm(1.522, 1.522, 0.0)
        assert m["blade_plus_mm"] == pytest.approx(1.522)
        assert m["blade_minus_mm"] == pytest.approx(1.522)

    def test_an_off_centre_beam_opens_one_blade_and_closes_the_other(self):
        m = srm.mechanical_blades_mm(1.522, 1.522, -1.218)
        assert m["blade_plus_mm"] == pytest.approx(0.304)
        assert m["blade_minus_mm"] == pytest.approx(2.740)

    def test_the_gap_is_unchanged_by_a_beam_offset(self):
        # The aperture is the same size; it has only been moved to meet the
        # beam. If this ever changes, the patch size changes with it.
        for b in (-3.0, -1.218, 0.0, 0.75):
            m = srm.mechanical_blades_mm(1.522, 1.522, b)
            assert m["blade_plus_mm"] + m["blade_minus_mm"] == pytest.approx(3.044)

    def test_it_is_the_inverse_of_itself(self):
        m = srm.mechanical_blades_mm(1.522, 1.522, -1.218)
        back = srm.mechanical_blades_mm(m["blade_plus_mm"], m["blade_minus_mm"],
                                        +1.218)
        assert back["blade_plus_mm"] == pytest.approx(1.522)
        assert back["blade_minus_mm"] == pytest.approx(1.522)

    def test_a_beam_offset_is_not_a_patch_offset(self):
        # Same four blade numbers, completely different physics. The patch
        # offset costs amplitude and transmission; the beam offset costs
        # nothing, because the aperture is still centred on the beam.
        centred = solve("X", 5.0)
        moved_patch = solve("X", 5.0, painted_center_mm=-2.0)
        assert moved_patch["amplitude_kv"] > centred["amplitude_kv"]
        assert (moved_patch["dose_transmitted_fraction"]
                < centred["dose_transmitted_fraction"])
        # ...whereas the beam offset never reaches solve_axis at all.
        mech = srm.mechanical_blades_mm(centred["blade_plus_mm"],
                                        centred["blade_minus_mm"], -1.218)
        assert mech["blade_plus_mm"] == pytest.approx(
            moved_patch["blade_plus_mm"], abs=1e-3)
