"""
tests/test_width_levels.py
Offline tests for the width ladder in rbl/hardware/profile_fwhm.py.

WHAT THIS IS FOR
----------------
FWHM describes the core of a profile and says nothing about where the beam
ends. On this beam line that gap is load-bearing: `slit_raster_model` derives
the overscan needed for a given edge droop by inverting a NORMAL CDF against
the FWHM alone, so every droop figure the raster planner prints is only as
true as the beam being Gaussian.

The ladder measures 50 %, 13.5 % (1/e²) and 10 % of each peak's own height,
independently, every shot. For a true Gaussian the widths are locked to each
other by sqrt(ln(1/f)/ln 2), so the MEASURED ratio is that assumption on
trial.

The failures pinned here:

  * a ratio that comes out right on a Gaussian and WRONG on a beam that is
    not one - a test that only ever sees Gaussians proves nothing;
  * a level quietly TRUNCATED at the fence between two peaks, which reads as
    a narrow beam;
  * a level measured DOWN IN THE NOISE, which reads as whatever the noise
    did that shot;
  * and the subtle one: a tail ratio taken from the Gaussian FIT. That
    number is 1.8226 every single time, because a Gaussian fit is Gaussian
    by construction. Displayed beside the word "tails" it reads as
    "perfectly Gaussian" when it means "nobody measured". It must not exist.
"""
import math
import random

import pytest

from rbl.hardware.profile_fwhm import (
    DEFAULT_LEVELS,
    LEVEL_1_OVER_E2,
    LEVEL_FWHM,
    LEVEL_FWTM,
    FwhmError,
    analyse_profile,
    gaussian_width_from_sigma,
    gaussian_width_ratio,
    level_label,
    measure_width_levels,
)

N     = 2500
XINCR = 2e-5
SIGMA = 60.0


def _trace(fn, noise=0.002, seed=3):
    rng = random.Random(seed)
    return [fn(i) + (rng.gauss(0.0, noise) if noise else 0.0)
            for i in range(N)]


def _gauss(i, c, s, a=1.0):
    return a * math.exp(-0.5 * ((i - c) / s) ** 2)


def _ladder(volts, *, smooth=15, max_peaks=2, fit=None):
    result = analyse_profile(volts, XINCR, smooth=smooth, max_peaks=max_peaks)
    return result, measure_width_levels(result, XINCR, fit=fit)


# ---------------------------------------------------------------------------
# The yardstick
# ---------------------------------------------------------------------------

def test_the_gaussian_ratios_are_what_the_maths_says():
    """sqrt(ln(1/f)/ln 2). These constants are the whole comparison."""
    assert gaussian_width_ratio(LEVEL_FWHM) == pytest.approx(1.0)
    assert gaussian_width_ratio(LEVEL_1_OVER_E2) == pytest.approx(1.698644, abs=1e-6)
    assert gaussian_width_ratio(LEVEL_FWTM) == pytest.approx(1.822615, abs=1e-6)


def test_the_1_over_e2_width_is_exactly_four_sigma():
    """Why 13.5 % is on the ladder at all: it is the optics convention."""
    assert gaussian_width_from_sigma(1.0, LEVEL_1_OVER_E2) == pytest.approx(4.0)
    assert gaussian_width_from_sigma(3.0, LEVEL_1_OVER_E2) == pytest.approx(12.0)


def test_the_half_maximum_width_from_sigma_is_the_usual_2_3548():
    assert gaussian_width_from_sigma(1.0, LEVEL_FWHM) == pytest.approx(2.35482,
                                                                       abs=1e-5)


@pytest.mark.parametrize("level", [0.0, 1.0, -0.1, 2.0])
def test_a_level_outside_zero_to_one_is_refused(level):
    with pytest.raises(FwhmError):
        gaussian_width_ratio(level)


def test_levels_are_named_the_way_an_operator_would():
    assert level_label(LEVEL_FWHM) == "FWHM"
    assert level_label(LEVEL_FWTM) == "FWTM"
    assert level_label(LEVEL_1_OVER_E2) == "FW1/e²"
    assert level_label(0.25) == "FW25%"


# ---------------------------------------------------------------------------
# A real Gaussian must come out Gaussian
# ---------------------------------------------------------------------------

def test_a_gaussian_beam_recovers_the_gaussian_ratios():
    volts = _trace(lambda i: _gauss(i, 700, SIGMA) + _gauss(i, 1800, SIGMA))
    _result, ladder = _ladder(volts)
    assert len(ladder) == 2
    for rung in ladder:
        lv = rung["levels"]
        fwhm = lv["FWHM"]["width_seconds"]
        assert lv["FWTM"]["width_seconds"] / fwhm == pytest.approx(1.8226,
                                                                   rel=0.01)
        assert lv["FW1/e²"]["width_seconds"] / fwhm == pytest.approx(1.6986,
                                                                     rel=0.01)
        assert rung["tail_ratio_source"] == "measured"
        assert abs(rung["tail_excess"]) < 0.02


def test_every_level_is_measured_not_derived_from_the_fwhm():
    """Each level runs its own crossing search.

    If a level were computed from the FWHM by the Gaussian ratio, a
    non-Gaussian beam would still report 1.8226 and the whole ladder would be
    decoration. Two traces with the SAME FWHM and different tails must give
    different FWTM."""
    narrow = _trace(lambda i: _gauss(i, 1250, SIGMA))
    haloed = _trace(lambda i: _gauss(i, 1250, SIGMA)
                    + 0.12 * _gauss(i, 1250, SIGMA * 3))
    _r1, l1 = _ladder(narrow, max_peaks=1)
    _r2, l2 = _ladder(haloed, max_peaks=1)
    fwhm1 = l1[0]["levels"]["FWHM"]["width_seconds"]
    fwhm2 = l2[0]["levels"]["FWHM"]["width_seconds"]
    assert fwhm2 == pytest.approx(fwhm1, rel=0.10)      # cores agree
    assert (l2[0]["levels"]["FWTM"]["width_seconds"]
            > l1[0]["levels"]["FWTM"]["width_seconds"] * 1.10)   # tails do not


# ---------------------------------------------------------------------------
# ...and a beam that is not Gaussian must come out not Gaussian
# ---------------------------------------------------------------------------

def test_a_haloed_beam_reads_heavier_than_gaussian():
    """The consequential direction: heavier tails put more current outside
    the painted field than the planner's normal-CDF droop allows for."""
    volts = _trace(lambda i: _gauss(i, 700, SIGMA)
                   + 0.12 * _gauss(i, 700, SIGMA * 3)
                   + _gauss(i, 1800, SIGMA)
                   + 0.12 * _gauss(i, 1800, SIGMA * 3))
    _result, ladder = _ladder(volts)
    assert ladder[0]["tail_ratio"] > 1.90
    assert ladder[0]["tail_excess"] > 0.05
    assert ladder[0]["tail_ratio_source"] == "measured"


def test_a_flat_topped_beam_reads_lighter_than_gaussian():
    """A scraped profile. Reads the other way, and means something else."""
    def flat(i, c, s):
        d = abs(i - c)
        return 1.0 if d < s else math.exp(-0.5 * ((d - s) / (s * 0.35)) ** 2)

    volts = _trace(lambda i: flat(i, 700, SIGMA) + flat(i, 1800, SIGMA))
    _result, ladder = _ladder(volts)
    assert ladder[0]["tail_ratio"] < 1.70
    assert ladder[0]["tail_excess"] < -0.05


# ---------------------------------------------------------------------------
# Overlapping skirts
# ---------------------------------------------------------------------------

def test_a_low_level_that_meets_the_neighbour_is_unresolved_not_truncated():
    """The failure this guards.

    Two peaks close relative to their width never fall to 10 % before
    meeting. Stopping the search at the fence and calling the result a width
    would report a NARROW beam - a plausible number, and wrong. FWHM is
    still perfectly measurable on the same trace, which is what makes the
    silent version so easy to miss."""
    volts = _trace(lambda i: _gauss(i, 1120, SIGMA) + _gauss(i, 1380, SIGMA))
    result, ladder = _ladder(volts)
    assert result["n_peaks"] == 2
    lv = ladder[0]["levels"]
    assert lv["FWHM"]["resolved"]                       # the core is fine
    assert not lv["FWTM"]["resolved"]
    assert math.isnan(lv["FWTM"]["width_seconds"])
    assert "skirts overlap" in lv["FWTM"]["note"]


def test_an_unmeasurable_level_is_offered_the_fit_and_labelled_as_such():
    volts = _trace(lambda i: _gauss(i, 1120, SIGMA) + _gauss(i, 1380, SIGMA))
    fake_fit = {"peaks": [{"sigma_samples": SIGMA}] * 2, "r_squared": 0.99}
    _result, ladder = _ladder(volts, fit=fake_fit)
    rec = ladder[0]["levels"]["FWTM"]
    assert rec["source"] == "fit"
    assert not rec["resolved"]
    assert rec["fit_width_seconds"] == pytest.approx(
        gaussian_width_from_sigma(SIGMA, LEVEL_FWTM) * XINCR)


def test_the_tail_ratio_has_no_fit_fallback():
    """The subtle one, and the reason it is written down.

    A sum-of-Gaussians fit will happily supply both widths, and their ratio
    is 1.8226 EVERY time - it is a property of the model, not of the beam.
    Shown next to the word "tails" that reads as "perfectly Gaussian" when
    what happened is that nobody measured. So it must be absent, and the
    note must say why."""
    volts = _trace(lambda i: _gauss(i, 1120, SIGMA) + _gauss(i, 1380, SIGMA))
    fake_fit = {"peaks": [{"sigma_samples": SIGMA}] * 2, "r_squared": 0.99}
    _result, ladder = _ladder(volts, fit=fake_fit)
    rung = ladder[0]
    assert math.isnan(rung["tail_ratio"])
    assert rung["tail_ratio_source"] == "none"
    assert "1.8226 by construction" in rung["tail_note"]


# ---------------------------------------------------------------------------
# The noise floor
# ---------------------------------------------------------------------------

def test_a_level_under_the_noise_floor_is_refused_with_a_reason():
    """A crossing found below a few sigma of the noise is a crossing of the
    noise. It would move shot to shot and look like a beam that breathes."""
    volts = _trace(lambda i: _gauss(i, 1250, SIGMA), noise=0.05)
    result, ladder = _ladder(volts, smooth=1, max_peaks=1)
    rec = ladder[0]["levels"]["FWTM"]
    assert not rec["resolved"]
    assert "noise" in rec["note"]
    assert ladder[0]["levels"]["FWHM"]["resolved"]      # the core still is


def test_a_generous_noise_guard_refuses_more_levels():
    volts = _trace(lambda i: _gauss(i, 1250, SIGMA), noise=0.01)
    result = analyse_profile(volts, XINCR, smooth=1, max_peaks=1)
    lenient = measure_width_levels(result, XINCR, noise_guard=0.0)
    strict  = measure_width_levels(result, XINCR, noise_guard=50.0)
    assert lenient[0]["levels"]["FWTM"]["resolved"]
    assert not strict[0]["levels"]["FWTM"]["resolved"]


# ---------------------------------------------------------------------------
# Nothing is carried between shots
# ---------------------------------------------------------------------------

def test_measuring_does_not_mutate_the_profile_result():
    """The ladder reads the analysis; it must not write to it.

    With quadrupole focusing the profile is not the same shape twice, so
    anything left behind on a shared structure would describe the previous
    beam."""
    volts = _trace(lambda i: _gauss(i, 700, SIGMA) + _gauss(i, 1800, SIGMA))
    result = analyse_profile(volts, XINCR, smooth=15, max_peaks=2)
    before = [dict(p) for p in result["peaks"]]
    corrected_before = list(result["corrected"])
    measure_width_levels(result, XINCR)
    assert [dict(p) for p in result["peaks"]] == before
    assert result["corrected"] == corrected_before


def test_two_traces_measured_in_a_row_do_not_influence_each_other():
    wide   = _trace(lambda i: _gauss(i, 1250, SIGMA * 2), seed=1)
    narrow = _trace(lambda i: _gauss(i, 1250, SIGMA), seed=1)
    _rw, lw = _ladder(wide, max_peaks=1)
    _rn, ln = _ladder(narrow, max_peaks=1)
    _rw2, lw2 = _ladder(wide, max_peaks=1)
    assert (lw2[0]["levels"]["FWTM"]["width_seconds"]
            == pytest.approx(lw[0]["levels"]["FWTM"]["width_seconds"]))
    assert (ln[0]["levels"]["FWTM"]["width_seconds"]
            < lw[0]["levels"]["FWTM"]["width_seconds"] * 0.75)


def test_the_default_levels_are_the_three_on_the_tab():
    assert DEFAULT_LEVELS == (LEVEL_FWHM, LEVEL_1_OVER_E2, LEVEL_FWTM)
    labels = [level_label(level) for level in DEFAULT_LEVELS]
    assert labels == ["FWHM", "FW1/e²", "FWTM"]
