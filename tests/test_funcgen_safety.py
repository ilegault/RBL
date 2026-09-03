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
