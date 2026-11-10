"""
Unit tests for rbl.hardware.funcgen_safety — the interlock maths that decides
whether a channel's combined peak voltage is safe to apply. No Qt required:
this is exactly the point of having moved it out of funcgen_tab.py.
"""
from rbl.hardware.funcgen_safety import (
    CHANNEL_ROLE,
    PEAK_MAX_VOLTS,
    PEAK_WARN_VOLTS,
    channel_peak_volts,
)


class TestChannelPeakVolts:
    def test_ac_shape_combines_offset_and_half_amplitude(self):
        # |offset| + amp/2
        assert channel_peak_volts("Sine", amp_vpp=2.0, offset_v=1.0) == 2.0

    def test_dc_shape_ignores_amplitude(self):
        assert channel_peak_volts("DC", amp_vpp=100.0, offset_v=3.0) == 3.0

    def test_negative_offset_uses_magnitude(self):
        assert channel_peak_volts("Square", amp_vpp=0.0, offset_v=-4.0) == 4.0

    def test_zero_offset_is_half_amplitude(self):
        assert channel_peak_volts("Triangle", amp_vpp=6.0, offset_v=0.0) == 3.0


class TestThresholds:
    def test_warn_below_max(self):
        assert PEAK_WARN_VOLTS < PEAK_MAX_VOLTS

    def test_max_matches_generator_ceiling(self):
        from rbl.hardware.funcgen_driver import MAX_GEN_VOLTS
        assert PEAK_MAX_VOLTS == MAX_GEN_VOLTS


class TestChannelRole:
    def test_x_axis_channels_share_generator_a(self):
        x_keys = {k for k, v in CHANNEL_ROLE.items() if v in ("X+", "X-")}
        assert x_keys == {"A1", "A2"}

    def test_y_axis_channels_share_generator_b(self):
        y_keys = {k for k, v in CHANNEL_ROLE.items() if v in ("Y+", "Y-")}
        assert y_keys == {"B1", "B2"}


def test_over_limit_channels_lists_offenders_in_order():
    import pytest
    from types import SimpleNamespace
    from rbl.hardware.funcgen_safety import over_limit_channels

    # 10 Vpp at 0 V -> exactly 5.0 V -> not listed
    safe_params = SimpleNamespace(shape="Triangle", amp_vpp=10.0, offset_v=0.0)
    assert over_limit_channels({"A1": safe_params}) == []

    # 4 Vpp at +3.5 V -> 5.5 V -> listed with 5.5
    over_ac = SimpleNamespace(shape="Sine", amp_vpp=4.0, offset_v=3.5)
    res_ac = over_limit_channels({"A1": over_ac})
    assert len(res_ac) == 1
    assert res_ac[0] == ("A1", pytest.approx(5.5))

    # DC shape at -5.2 V -> 5.2 V -> listed with 5.2
    over_dc = SimpleNamespace(shape="DC", amp_vpp=0.0, offset_v=-5.2)
    res_dc = over_limit_channels({"B2": over_dc})
    assert len(res_dc) == 1
    assert res_dc[0] == ("B2", pytest.approx(5.2))

    # Two offenders across keys out of order: B1 and A2
    # Result must be ordered A1, A2, B1, B2 (so A2 first, then B1)
    ch_map = {
        "B1": over_ac,
        "A2": over_dc,
        "A1": safe_params,
    }
    ordered = over_limit_channels(ch_map)
    assert ordered == [("A2", pytest.approx(5.2)), ("B1", pytest.approx(5.5))]

