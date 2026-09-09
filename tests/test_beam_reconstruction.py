"""
Tests for beam_reconstruction: turning four slit currents into a beam position.

The theme running through these is what the measurement can and cannot support.
A centred beam is solvable without knowing the beam width at all; an off-centre
one is not, and the module is expected to widen its reported interval to say so
rather than quietly returning a confident number.
"""
import math

import pytest

from rbl.hardware.beam_reconstruction import (
    NOISE_FLOOR_A,
    _gauss_tail,
    _raster_tail,
    overscan_flags,
    reconstruct,
    solve_axis_centre,
    solve_axis_interval,
)

EDGES = {"X+": 1.5, "X-": -1.5, "Y+": 5.0, "Y-": -5.0}
GOOD  = {"X+": 1e-6, "X-": 1e-6, "Y+": 1e-6, "Y-": 1e-6}


# ── Gaussian tail ─────────────────────────────────────────────────────────────

class TestGaussTail:
    def test_half_the_beam_lies_past_its_own_centre(self):
        assert abs(_gauss_tail(0.0, 0.0, 1.0) - 0.5) < 1e-12

    def test_tail_shrinks_as_the_edge_moves_away(self):
        far  = _gauss_tail(3.0, 0.0, 1.0)
        near = _gauss_tail(1.0, 0.0, 1.0)
        assert far < near < 0.5

    def test_wider_beam_puts_more_flux_past_a_fixed_edge(self):
        assert _gauss_tail(2.0, 0.0, 2.0) > _gauss_tail(2.0, 0.0, 0.5)

    def test_zero_width_is_a_step(self):
        assert _gauss_tail(1.0, 2.0, 0.0) == 1.0
        assert _gauss_tail(1.0, 0.0, 0.0) == 0.0


# ── Single-axis solve ─────────────────────────────────────────────────────────

class TestSolveAxisCentre:
    @pytest.mark.parametrize("sigma", [0.25, 0.5, 1.0, 2.0, 5.0])
    def test_centred_beam_is_width_independent(self, sigma):
        """Equal currents put the beam at the midpoint whatever its width.

        This is the one case where the unmeasurable width genuinely does not
        matter, and it is the anchor the whole widget leans on.
        """
        assert abs(solve_axis_centre(1e-6, 1e-6, 1.5, -1.5, sigma)) < 1e-6

    def test_equal_currents_on_asymmetric_slits_find_their_midpoint(self):
        c = solve_axis_centre(1e-6, 1e-6, 3.0, -1.0, 1.0)
        assert abs(c - 1.0) < 1e-6

    def test_more_current_on_plus_slit_moves_beam_toward_it(self):
        assert solve_axis_centre(2e-6, 1e-6, 1.5, -1.5, 1.0) > 0

    def test_more_current_on_minus_slit_moves_beam_toward_it(self):
        assert solve_axis_centre(1e-6, 2e-6, 1.5, -1.5, 1.0) < 0

    def test_monotonic_in_the_current_ratio(self):
        prev = -math.inf
        for ratio in (0.2, 0.5, 1.0, 2.0, 5.0, 10.0):
            c = solve_axis_centre(ratio * 1e-6, 1e-6, 1.5, -1.5, 1.0)
            assert c > prev
            prev = c

    @pytest.mark.parametrize("true_c", [-1.2, -0.5, 0.0, 0.5, 1.2])
    def test_round_trip_recovers_a_planted_beam(self, true_c):
        """Generate the currents a known beam would produce, then invert them."""
        sigma = 0.85
        i_plus  = _gauss_tail(1.5, true_c, sigma)
        i_minus = _gauss_tail(1.5, -true_c, sigma)   # mirrored for the '-' slit
        got = solve_axis_centre(i_plus, i_minus, 1.5, -1.5, sigma)
        assert abs(got - true_c) < 1e-6

    def test_unusable_current_gives_nan(self):
        assert math.isnan(solve_axis_centre(float("nan"), 1e-6, 1.5, -1.5, 1.0))
        assert math.isnan(solve_axis_centre(1e-6, float("nan"), 1.5, -1.5, 1.0))
        assert math.isnan(solve_axis_centre(0.0, 1e-6, 1.5, -1.5, 1.0))

    def test_floor_level_current_is_not_signal(self):
        assert math.isnan(
            solve_axis_centre(NOISE_FLOOR_A * 0.5, 1e-6, 1.5, -1.5, 1.0))

    def test_crossed_slits_are_rejected(self):
        """A '-' edge on the far side of the '+' edge is not a real aperture."""
        assert math.isnan(solve_axis_centre(1e-6, 1e-6, -1.5, 1.5, 1.0))


# ── The ambiguity interval ────────────────────────────────────────────────────

class TestAmbiguityInterval:
    def test_centred_beam_has_no_ambiguity(self):
        c, lo, hi = solve_axis_interval(1e-6, 1e-6, 1.5, -1.5, 1.0)
        assert abs(c) < 1e-6
        assert (hi - lo) < 1e-6

    def test_interval_widens_as_the_beam_goes_off_centre(self):
        """The width assumption matters more the more lopsided the currents."""
        widths = []
        for ratio in (1.0, 2.0, 5.0, 20.0):
            _, lo, hi = solve_axis_interval(ratio * 1e-6, 1e-6, 1.5, -1.5, 1.0)
            widths.append(hi - lo)
        assert widths == sorted(widths)
        assert widths[-1] > widths[0]

    def test_nominal_estimate_lies_inside_its_own_interval(self):
        c, lo, hi = solve_axis_interval(6e-6, 1e-6, 1.5, -1.5, 1.0)
        assert lo <= c <= hi

    def test_unsolvable_axis_gives_all_nan(self):
        c, lo, hi = solve_axis_interval(0.0, 1e-6, 1.5, -1.5, 1.0)
        assert math.isnan(c) and math.isnan(lo) and math.isnan(hi)

    def test_raster_mode_widens_the_interval_further(self):
        """Sweep amplitude is unknown too, so it adds to the ambiguity."""
        _, s_lo, s_hi = solve_axis_interval(6e-6, 1e-6, 1.5, -1.5, 0.85)
        _, r_lo, r_hi = solve_axis_interval(6e-6, 1e-6, 1.5, -1.5, 0.85,
                                            half_span_mm=3.0)
        assert (r_hi - r_lo) > (s_hi - s_lo)


# ── Raster profile ────────────────────────────────────────────────────────────

class TestRasterTail:
    def test_sweeping_spreads_flux_past_an_outside_edge(self):
        assert _raster_tail(2.0, 0.0, 1.5, 0.5) > _gauss_tail(2.0, 0.0, 0.5)

    def test_vanishing_sweep_collapses_to_the_static_case(self):
        swept  = _raster_tail(2.0, 0.0, 1e-9, 0.5)
        static = _gauss_tail(2.0, 0.0, 0.5)
        assert abs(swept - static) < 1e-6

    def test_centred_sweep_splits_evenly_about_its_centre(self):
        assert abs(_raster_tail(0.0, 0.0, 3.0, 0.5) - 0.5) < 1e-9

    @pytest.mark.parametrize("true_c", [-0.6, 0.0, 0.3, 0.9])
    def test_round_trip_recovers_a_swept_beam(self, true_c):
        span, sigma = 2.5, 0.5
        i_plus  = _raster_tail(1.5, true_c, span, sigma)
        i_minus = _raster_tail(1.5, -true_c, span, sigma)
        got = solve_axis_centre(i_plus, i_minus, 1.5, -1.5, sigma,
                                half_span_mm=span)
        assert abs(got - true_c) < 1e-5


# ── Full reconstruction ───────────────────────────────────────────────────────

class TestReconstruct:
    def test_balanced_beam_sits_on_the_axis(self):
        est = reconstruct(GOOD, EDGES, 0.85)
        assert est.ok
        assert abs(est.x) < 1e-6 and abs(est.y) < 1e-6
        assert est.bad_slits == [] and est.reason == ""

    def test_axes_are_solved_independently(self):
        """X imbalance must not leak into the Y answer."""
        est = reconstruct({**GOOD, "X+": 4e-6}, EDGES, 0.85)
        assert est.ok
        assert est.x > 0
        assert abs(est.y) < 1e-6

    def test_one_dead_slit_invalidates_the_whole_estimate(self):
        """A partial picture is easier to misread than no picture."""
        est = reconstruct({**GOOD, "Y-": 1e-12}, EDGES, 0.85)
        assert not est.ok
        assert est.bad_slits == ["Y-"]
        assert "Y-" in est.reason

    def test_nan_slit_is_reported_as_bad(self):
        est = reconstruct({**GOOD, "X+": float("nan")}, EDGES, 0.85)
        assert not est.ok and est.bad_slits == ["X+"]

    def test_every_bad_slit_is_listed(self):
        est = reconstruct({"X+": 1e-6, "X-": 0.0, "Y+": 0.0, "Y-": 1e-6},
                          EDGES, 0.85)
        assert est.bad_slits == ["X-", "Y+"]

    def test_missing_slit_position_blocks_reconstruction(self):
        est = reconstruct(GOOD, {**EDGES, "X+": float("nan")}, 0.85)
        assert not est.ok
        assert "position unknown" in est.reason

    def test_absent_slit_position_key_blocks_reconstruction(self):
        edges = {k: v for k, v in EDGES.items() if k != "Y+"}
        est = reconstruct(GOOD, edges, 0.85)
        assert not est.ok and "Y+" in est.reason

    def test_raster_mode_uses_per_axis_sweep_spans(self):
        """A tall narrow raster is not the same as a square one."""
        square = reconstruct({**GOOD, "X+": 3e-6}, EDGES, 0.85, 3.0, 3.0)
        tall   = reconstruct({**GOOD, "X+": 3e-6}, EDGES, 0.85, 3.0, 8.0)
        assert square.ok and tall.ok
        # Y span differs, so the Y solve differs; X is untouched by it.
        assert abs(square.x - tall.x) < 1e-9



# ── Overscan ──────────────────────────────────────────────────────────────────

class TestOverscanFlags:
    def test_all_blades_reached_when_all_carry_current(self):
        assert all(overscan_flags(GOOD).values())

    def test_blade_at_the_floor_is_not_being_reached(self):
        flags = overscan_flags({**GOOD, "Y-": 1e-12})
        assert flags["X+"] and flags["X-"] and flags["Y+"]
        assert not flags["Y-"]

    def test_missing_channel_counts_as_not_reached(self):
        flags = overscan_flags({"X+": 1e-6})
        assert flags["X+"] and not flags["Y+"]
