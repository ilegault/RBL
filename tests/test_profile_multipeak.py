"""
tests/test_profile_multipeak.py
Offline tests for the multi-peak half of rbl/hardware/profile_fwhm.py.

These encode what was learned on the real beam line: the sweep crosses the
aperture twice per period, noise biases a half-maximum reading NARROW, and a
single Gaussian fitted across two peaks reports the separation rather than a
width.
"""
import math
import random

import pytest

from rbl.hardware.profile_fwhm import (
    MAX_PEAKS,
    FwhmError,
    analyse_profile,
    best_fwhm,
    detect_clipping,
    find_peaks,
    fit_gaussians,
    moving_average,
    upper_envelope,
)

N = 2500
XINCR = 2e-5            # 50 ms record, as at 5 ms/div
SIGMA = 30.0
TRUE_FWHM_S = 2.3548 * SIGMA * XINCR


def two_peak_trace(noise=0.0, seed=7, centres=(800, 1700), amp=0.22,
                   sigma=SIGMA, baseline=0.03):
    rng = random.Random(seed)
    out = []
    for i in range(N):
        y = baseline + sum(amp * math.exp(-0.5 * ((i - c) / sigma) ** 2)
                           for c in centres)
        if noise:
            y += rng.gauss(0.0, noise)
        out.append(y)
    return out


# ---------------------------------------------------------------------------
# moving_average
# ---------------------------------------------------------------------------

class TestMovingAverage:

    def test_window_one_is_identity(self):
        v = [1.0, 5.0, 2.0]
        assert moving_average(v, 1) == v

    def test_length_preserved(self):
        v = [float(i) for i in range(100)]
        assert len(moving_average(v, 11)) == 100

    def test_constant_signal_unchanged(self):
        v = [3.0] * 50
        assert all(abs(x - 3.0) < 1e-12 for x in moving_average(v, 9))

    def test_spike_is_flattened(self):
        v = [0.0] * 50
        v[25] = 10.0
        assert max(moving_average(v, 11)) < 1.5


# ---------------------------------------------------------------------------
# find_peaks
# ---------------------------------------------------------------------------

class TestFindPeaks:

    def test_finds_both_peaks(self):
        v = two_peak_trace()
        idx = find_peaks(v, threshold=0.1, min_sep=50, max_peaks=2)
        assert len(idx) == 2
        assert abs(idx[0] - 800) < 5 and abs(idx[1] - 1700) < 5

    def test_returns_left_to_right(self):
        idx = find_peaks(two_peak_trace(), 0.1, 50, 2)
        assert idx == sorted(idx)

    def test_threshold_rejects_small_peak(self):
        v = two_peak_trace(centres=(800,)) 
        for i in range(1600, 1700):
            v[i] += 0.01 * math.exp(-0.5 * ((i - 1650) / 10) ** 2)
        idx = find_peaks(v, threshold=0.1, min_sep=50, max_peaks=4)
        assert len(idx) == 1

    def test_min_sep_merges_close_candidates(self):
        v = two_peak_trace(centres=(800, 830))
        idx = find_peaks(v, threshold=0.1, min_sep=200, max_peaks=4)
        assert len(idx) == 1


# ---------------------------------------------------------------------------
# analyse_profile
# ---------------------------------------------------------------------------

class TestAnalyseProfile:

    def test_two_peaks_measured_independently(self):
        r = analyse_profile(two_peak_trace(), XINCR, max_peaks=2)
        assert r["n_peaks"] == 2
        assert r["n_resolved"] == 2
        for p in r["peaks"]:
            assert p["fwhm_seconds"] == pytest.approx(TRUE_FWHM_S, rel=0.05)

    def test_identical_peaks_have_near_zero_spread(self):
        r = analyse_profile(two_peak_trace(), XINCR, max_peaks=2)
        assert r["fwhm_spread"] < 0.02

    def test_separation_reported(self):
        r = analyse_profile(two_peak_trace(), XINCR, max_peaks=2)
        assert r["separations_seconds"][0] == pytest.approx(900 * XINCR, rel=0.02)

    def test_single_peak_mode_ignores_second(self):
        r = analyse_profile(two_peak_trace(), XINCR, max_peaks=1)
        assert r["n_peaks"] == 1

    def test_max_peaks_is_capped(self):
        r = analyse_profile(two_peak_trace(), XINCR, max_peaks=99)
        assert r["n_peaks"] <= MAX_PEAKS

    def test_crossing_never_walks_into_the_neighbour(self):
        # Without the valley fence, peak 1's right-hand search would run
        # through the trough and terminate somewhere inside peak 2.
        r = analyse_profile(two_peak_trace(), XINCR, max_peaks=2)
        first = r["peaks"][0]
        assert first["right_index"] < r["peaks"][1]["index"]

    def test_overlapping_peaks_reported_unresolved_not_raised(self):
        v = two_peak_trace(centres=(1200, 1290), sigma=60)
        r = analyse_profile(v, XINCR, max_peaks=2)
        if r["n_peaks"] == 2:                    # two maxima survived
            assert r["n_resolved"] < 2
            assert any(not p["resolved"] for p in r["peaks"])
            assert any("half maximum" in p["note"] for p in r["peaks"])

    def test_negative_going_signal_is_flipped(self):
        v = [0.06 - (x - 0.03) for x in two_peak_trace()]
        r = analyse_profile(v, XINCR, max_peaks=2)
        assert r["flipped"] is True
        assert r["peaks"][0]["fwhm_seconds"] == pytest.approx(TRUE_FWHM_S, rel=0.05)

    def test_forced_polarity_overrides_auto(self):
        r = analyse_profile(two_peak_trace(), XINCR, max_peaks=2, polarity="pos")
        assert r["flipped"] is False

    def test_flat_trace_raises(self):
        with pytest.raises(FwhmError):
            analyse_profile([0.05] * N, XINCR, max_peaks=2)

    def test_noise_only_trace_raises(self):
        rng = random.Random(3)
        with pytest.raises(FwhmError):
            analyse_profile([rng.gauss(0, 0.01) for _ in range(N)], XINCR)

    def test_too_short_raises(self):
        with pytest.raises(FwhmError):
            analyse_profile([0.0, 1.0, 0.0], XINCR)

    def test_smoothing_rejects_a_noise_spike(self):
        # A two-sample spike away from the beam is taller than the beam.
        v = two_peak_trace()
        v[400] = 2.5                     # one sample, taller than the beam
        unsmoothed = analyse_profile(v, XINCR, max_peaks=1, smooth=1)
        assert unsmoothed["peaks"][0]["index"] == pytest.approx(400, abs=3)

        smoothed = analyse_profile(v, XINCR, max_peaks=1, smooth=15)
        assert abs(smoothed["peaks"][0]["index"] - 800) < 60

    def test_noise_biases_half_max_narrow_and_smoothing_fixes_it(self):
        # The measured-on-hardware failure mode, pinned down as a test.
        noisy = two_peak_trace(noise=0.018, seed=11)
        raw = analyse_profile(noisy, XINCR, max_peaks=2, smooth=1)
        smooth = analyse_profile(noisy, XINCR, max_peaks=2, smooth=15)
        assert raw["mean_fwhm_seconds"] < TRUE_FWHM_S * 0.85
        assert smooth["mean_fwhm_seconds"] == pytest.approx(TRUE_FWHM_S, rel=0.10)


# ---------------------------------------------------------------------------
# fit_gaussians / best_fwhm
# ---------------------------------------------------------------------------

class TestFitGaussians:

    def setup_method(self):
        pytest.importorskip("scipy")

    def test_fit_recovers_both_widths(self):
        r = analyse_profile(two_peak_trace(noise=0.018, seed=5), XINCR,
                            max_peaks=2, smooth=15)
        f = fit_gaussians(r, XINCR)
        assert f is not None
        assert len(f["peaks"]) == 2
        for p in f["peaks"]:
            assert p["fwhm_seconds"] == pytest.approx(TRUE_FWHM_S, rel=0.08)

    def test_two_gaussian_model_beats_one(self):
        # A single Gaussian across two peaks fits badly - this is why the
        # model is a sum, and r2 on the real beam was 0.14 before it was.
        r2 = analyse_profile(two_peak_trace(noise=0.018), XINCR, max_peaks=2,
                             smooth=15)
        r1 = analyse_profile(two_peak_trace(noise=0.018), XINCR, max_peaks=1,
                             smooth=15)
        assert fit_gaussians(r2, XINCR)["r_squared"] > \
               fit_gaussians(r1, XINCR)["r_squared"]

    def test_fit_measures_peaks_the_half_max_cannot(self):
        v = two_peak_trace(centres=(1200, 1290), sigma=60)
        r = analyse_profile(v, XINCR, max_peaks=2, smooth=9)
        f = fit_gaussians(r, XINCR)
        assert f is not None and f["r_squared"] > 0.95

    def test_best_fwhm_prefers_the_fit_when_r2_is_good(self):
        r = analyse_profile(two_peak_trace(noise=0.018), XINCR, max_peaks=2,
                            smooth=15)
        f = fit_gaussians(r, XINCR)
        value, source = best_fwhm(r, f, min_r2=0.90)
        assert source == "fit"
        assert value == pytest.approx(TRUE_FWHM_S, rel=0.10)

    def test_best_fwhm_falls_back_when_r2_is_poor(self):
        r = analyse_profile(two_peak_trace(), XINCR, max_peaks=2)
        f = fit_gaussians(r, XINCR)
        value, source = best_fwhm(r, f, min_r2=1.01)   # unreachable bar
        assert source == "half-max"
        assert value == pytest.approx(r["mean_fwhm_seconds"])

    def test_best_fwhm_without_a_fit(self):
        r = analyse_profile(two_peak_trace(), XINCR, max_peaks=2)
        value, source = best_fwhm(r, None)
        assert source == "half-max"
        assert value == pytest.approx(r["mean_fwhm_seconds"])


# ---------------------------------------------------------------------------
# Rastered beam: envelope, clipping, noise floor, axis labels
# ---------------------------------------------------------------------------

RIPPLE = 55                     # raster period in samples (~1.1 ms)
RASTER_SIGMA = 110.0
RASTER_TRUE_FWHM = 2.3548 * RASTER_SIGMA * XINCR


def rastered_trace(noise=0.01, seed=2, sigmas=(RASTER_SIGMA, RASTER_SIGMA),
                   centres=(700, 1800), floor_at_zero=False):
    """A beam chopped by a fast raster: two profiles, each a train of teeth."""
    rng = random.Random(seed)
    out = []
    for i in range(N):
        env = sum(1.2 * math.exp(-0.5 * ((i - c) / sg) ** 2)
                  for c, sg in zip(centres, sigmas))
        tooth = max(0.0, math.sin(2 * math.pi * i / RIPPLE)) ** 0.6
        y = env * (0.12 + 0.88 * tooth) + rng.gauss(0, noise)
        out.append(max(0.0, y) if floor_at_zero else y)
    return out


class TestRasterEnvelope:

    def test_raw_trace_measures_a_raster_tooth(self):
        # The failure seen on the beam line: a width an order of magnitude
        # too small, because half maximum landed on one tooth.
        r = analyse_profile(rastered_trace(), XINCR, max_peaks=2, smooth=9)
        assert r["mean_fwhm_seconds"] < RASTER_TRUE_FWHM * 0.3

    def test_envelope_recovers_the_beam_width(self):
        r = analyse_profile(rastered_trace(), XINCR, max_peaks=2, smooth=9,
                            envelope_samples=RIPPLE)
        assert r["mean_fwhm_seconds"] == pytest.approx(RASTER_TRUE_FWHM,
                                                       rel=0.10)

    def test_envelope_rescues_the_fit(self):
        raw = analyse_profile(rastered_trace(), XINCR, max_peaks=2, smooth=9)
        env = analyse_profile(rastered_trace(), XINCR, max_peaks=2, smooth=9,
                              envelope_samples=RIPPLE)
        raw_r2 = fit_gaussians(raw, XINCR)["r_squared"]
        env_r2 = fit_gaussians(env, XINCR)["r_squared"]
        assert raw_r2 < 0.80          # on the real beam this was 0.30
        assert env_r2 > 0.95
        assert env_r2 > raw_r2

    def test_window_wider_than_the_ripple_reads_high(self):
        matched = analyse_profile(rastered_trace(), XINCR, max_peaks=2,
                                  smooth=9, envelope_samples=RIPPLE)
        wide = analyse_profile(rastered_trace(), XINCR, max_peaks=2,
                               smooth=9, envelope_samples=RIPPLE * 2)
        assert wide["mean_fwhm_seconds"] > matched["mean_fwhm_seconds"]

    def test_envelope_off_by_default(self):
        r = analyse_profile(two_peak_trace(), XINCR, max_peaks=2)
        assert r["envelope_samples"] == 0

    def test_upper_envelope_preserves_length(self):
        v = rastered_trace()
        assert len(upper_envelope(v, RIPPLE)) == len(v)

    def test_upper_envelope_is_never_below_the_peaks_it_spans(self):
        v = rastered_trace(noise=0.0)
        env = upper_envelope(v, RIPPLE)
        # Every block maximum must lie on the envelope.
        for start in range(0, N - RIPPLE, RIPPLE):
            block = range(start, start + RIPPLE)
            top = max(block, key=lambda i: v[i])
            assert env[top] == pytest.approx(v[top], rel=1e-9)


class TestClippingAndNoiseFloor:

    def test_flat_bottom_is_reported_as_clipped(self):
        r = analyse_profile(rastered_trace(floor_at_zero=True), XINCR,
                            max_peaks=2, smooth=9, envelope_samples=RIPPLE)
        assert r["clipping"]["low"] is True
        assert r["clipping"]["low_fraction"] > 0.05

    def test_clean_trace_is_not_flagged(self):
        r = analyse_profile(two_peak_trace(noise=0.018), XINCR, max_peaks=2,
                            smooth=15)
        assert r["clipping"]["low"] is False
        assert r["clipping"]["high"] is False

    def test_quantum_floors_the_noise_estimate(self):
        # A quiet stretch pinned to one ADC code has zero apparent noise;
        # without a floor the signal-to-noise ratio runs to 1e12, which is
        # what the tab displayed before this existed.
        v = [0.0] * N
        for i in range(1000, 1200):
            v[i] = 1.0
        unfloored = analyse_profile(v, XINCR, max_peaks=1, quantum=0.0)
        floored = analyse_profile(v, XINCR, max_peaks=1, quantum=4e-3)
        assert unfloored["signal_to_noise"] > 1e9
        assert floored["signal_to_noise"] == pytest.approx(1.0 / 0.002, rel=0.01)


class TestAxisLabels:

    def test_peaks_are_labelled_x_and_y(self):
        r = analyse_profile(two_peak_trace(), XINCR, max_peaks=2)
        assert [p["axis"] for p in r["peaks"]] == ["X", "Y"]

    def test_per_axis_widths_are_exposed(self):
        r = analyse_profile(two_peak_trace(), XINCR, max_peaks=2)
        assert r["fwhm_x_seconds"] == pytest.approx(r["peaks"][0]["fwhm_seconds"])
        assert r["fwhm_y_seconds"] == pytest.approx(r["peaks"][1]["fwhm_seconds"])

    def test_ratio_is_one_for_equal_axes(self):
        r = analyse_profile(two_peak_trace(), XINCR, max_peaks=2)
        assert r["xy_ratio"] == pytest.approx(1.0, abs=0.03)

    def test_ratio_tracks_an_elliptical_beam(self):
        v = two_peak_trace(centres=(800,), sigma=SIGMA)
        wide = two_peak_trace(centres=(1700,), sigma=SIGMA * 2, baseline=0.0)
        combined = [a + b for a, b in zip(v, wide)]
        r = analyse_profile(combined, XINCR, max_peaks=2)
        assert r["xy_ratio"] == pytest.approx(0.5, rel=0.15)

    def test_labels_are_configurable(self):
        r = analyse_profile(two_peak_trace(), XINCR, max_peaks=2,
                            axis_labels=("Y", "X"))
        assert [p["axis"] for p in r["peaks"]] == ["Y", "X"]
