"""
Tests for rbl/config/calibration_config.py: constants and sweep_points().

No hardware required — pure data / math.
"""
import pytest

from rbl.config.calibration_config import (
    CAL_MAX_KV,
    CAL_PASSES,
    CAL_PROFILE,
    CAL_STEP_KV,
    CAL_UNCERTAINTY_V,
    LoadCondition,
    sweep_points,
)
from rbl.config.labjack_stream_config import STREAM_PROFILES
from rbl.hardware.funcgen_driver import MAX_GEN_VOLTS


class TestSweepPoints:
    def test_up_is_ascending_and_bracketed(self):
        pts = sweep_points("up")
        # "up" ramps 0 -> +CAL_MAX_KV, back through 0, then 0 -> -CAL_MAX_KV,
        # back to 0: each polarity's half-ladder is visited out and back, plus
        # the middle zero and the bracketing zeros at each end.
        half_len = round(CAL_MAX_KV / CAL_STEP_KV)
        assert len(pts) == 4 * half_len + 3
        assert pts[0] == 0.0
        assert pts[-1] == 0.0
        first_nonzero = next(v for v in pts if v != 0.0)
        assert first_nonzero > 0   # ramps positive first
        max_step = max(abs(pts[i + 1] - pts[i]) for i in range(len(pts) - 1))
        assert max_step <= CAL_STEP_KV + 1e-9
        assert max(pts) == pytest.approx(CAL_MAX_KV)
        assert min(pts) == pytest.approx(-CAL_MAX_KV)
        print(f"[OK] sweep_points('up') starts positive, bracketed by 0.0, length {len(pts)}")

    def test_down_is_reverse_of_up_modulo_zeros(self):
        up = sweep_points("up")
        down = sweep_points("down")
        assert down[0] == 0.0 and down[-1] == 0.0
        assert down[1:-1] == list(reversed(up[1:-1]))
        print("[OK] sweep_points('down') == reversed(up) modulo the bracketing zeros")

    def test_random_seed_is_deterministic(self):
        r1 = sweep_points("random", seed=42)
        r2 = sweep_points("random", seed=42)
        assert r1 == r2
        # random visits the base ladder once (no out-and-back), so its length
        # is the ladder plus the bracketing zeros — not "up"'s length, which
        # visits every rung twice.
        n_ladder = round(2 * CAL_MAX_KV / CAL_STEP_KV) + 1
        assert len(r1) == n_ladder + 2
        print("[OK] sweep_points('random', seed=42) is deterministic across two calls")

    def test_random_different_seed_differs(self):
        r1 = sweep_points("random", seed=1)
        r2 = sweep_points("random", seed=2)
        assert r1 != r2

    @pytest.mark.parametrize("pass_type", CAL_PASSES)
    def test_every_pass_brackets_zero(self, pass_type):
        pts = sweep_points(pass_type, seed=7)
        assert pts[0] == 0.0
        assert pts[-1] == 0.0
        print(f"[OK] {pass_type} pass starts and ends at 0.0")

    @pytest.mark.parametrize("pass_type", CAL_PASSES)
    def test_no_point_exceeds_cal_max_kv(self, pass_type):
        pts = sweep_points(pass_type, seed=7)
        assert all(abs(p) <= CAL_MAX_KV + 1e-9 for p in pts)
        print(f"[OK] {pass_type} pass stays within CAL_MAX_KV")

    def test_unknown_pass_type_raises(self):
        with pytest.raises(ValueError):
            sweep_points("sideways")

    def test_up_down_visit_twice_random_visits_once(self):
        # up and down each visit every rung twice (out and back on each
        # polarity) so their inner points form the same multiset; random
        # visits every rung exactly once. All three still cover the same
        # set of rungs.
        up = sorted(sweep_points("up")[1:-1])
        down = sorted(sweep_points("down")[1:-1])
        rnd = sorted(sweep_points("random", seed=3)[1:-1])
        assert up == down
        assert set(up) == set(down) == set(rnd)
        assert len(rnd) == len(set(rnd))
        # Every nonzero rung is visited twice (out and back); the single
        # 0.0 crossing between the positive and negative halves is not.
        nonzero_rungs = len(set(up)) - 1
        assert len(up) == 2 * nonzero_rungs + 1


class TestConstants:
    def test_cal_max_kv_within_gen_ceiling(self):
        assert CAL_MAX_KV <= MAX_GEN_VOLTS
        print(f"[OK] CAL_MAX_KV ({CAL_MAX_KV}) <= MAX_GEN_VOLTS ({MAX_GEN_VOLTS})")

    def test_uncertainty_budget_is_positive(self):
        assert CAL_UNCERTAINTY_V > 0

    def test_cal_profile_exists_and_carries_all_amp_ains(self):
        from rbl.config.labjack_stream_config import AMP_CHANNELS
        assert CAL_PROFILE in STREAM_PROFILES
        assert set(AMP_CHANNELS).issubset(set(STREAM_PROFILES[CAL_PROFILE]["scan_list"]))

    def test_load_condition_enum_has_exactly_two_members(self):
        assert {m.name for m in LoadCondition} == {"DISCONNECTED", "ON_PLATES"}
