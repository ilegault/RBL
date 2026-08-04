"""
Tests for rbl/config/calibration_config.py: constants and sweep_points().

No hardware required — pure data / math.
"""
import pytest

from rbl.config.calibration_config import (
    CAL_MAX_KV, CAL_PASSES, CAL_PROFILE, CAL_UNCERTAINTY_V, LoadCondition,
    sweep_points,
)
from rbl.hardware.funcgen_driver import MAX_GEN_VOLTS
from rbl.config.labjack_stream_config import STREAM_PROFILES


class TestSweepPoints:
    def test_up_is_ascending_and_bracketed(self):
        pts = sweep_points("up")
        assert len(pts) == 43   # 41 + leading/trailing zero
        assert pts[0] == 0.0
        assert pts[-1] == 0.0
        inner = pts[1:-1]
        assert inner == sorted(inner)
        print("[OK] sweep_points('up') is ascending, length 43 (41 + leading/trailing zero)")

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
        assert len(r1) == 43
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

    def test_up_down_random_all_same_multiset(self):
        # Every pass is a permutation of the same underlying ladder.
        up = sorted(sweep_points("up")[1:-1])
        down = sorted(sweep_points("down")[1:-1])
        rnd = sorted(sweep_points("random", seed=3)[1:-1])
        assert up == down == rnd


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
