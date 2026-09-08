"""
Unit tests for the display widgets used by the Overview tab
(rbl.gui.widgets.mini). Each widget only renders a value it's given, so these
tests check that set()/push()/set_target() produce the expected label text and
the expected position on the widget's own scale.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class TestValueTile:
    def test_initial_value_is_placeholder(self, qapp):
        from rbl.gui.widgets.mini import ValueTile
        tile = ValueTile("X+", "mm")
        assert tile.lbl_value.text() == "—"

    def test_set_formats_value_with_unit(self, qapp):
        from rbl.gui.widgets.mini import ValueTile
        tile = ValueTile("X+", "mm")
        tile.set(12.3456)
        assert tile.lbl_value.text() == "12.346 mm"

    def test_set_none_shows_placeholder(self, qapp):
        from rbl.gui.widgets.mini import ValueTile
        tile = ValueTile("X+", "mm")
        tile.set(5.0)
        tile.set(None)
        assert tile.lbl_value.text() == "—"

    def test_nan_shows_placeholder_not_a_number(self, qapp):
        from rbl.gui.widgets.mini import ValueTile
        tile = ValueTile("X+", "mm")
        tile.set(float("nan"))
        assert tile.lbl_value.text() == "—"

    def test_stale_changes_style(self, qapp):
        from rbl.gui import theme
        from rbl.gui.widgets.mini import ValueTile
        tile = ValueTile("X+", "mm")
        tile.set(1.0, stale=True)
        assert theme.MUTED in tile.lbl_value.styleSheet()


class TestMiniBar:
    def test_value_within_range_sets_fraction(self, qapp):
        from rbl.gui.widgets.mini import MiniBar
        bar = MiniBar("Amp A1", 0.0, 10.0)
        bar.set(5.0)
        assert bar.fraction() == pytest.approx(0.5)
        assert bar.track._frac == pytest.approx(0.5)

    def test_value_clamped_below_minimum(self, qapp):
        from rbl.gui.widgets.mini import MiniBar
        bar = MiniBar("Amp A1", 0.0, 10.0)
        bar.set(-5.0)
        assert bar.fraction() == 0.0

    def test_value_clamped_above_maximum(self, qapp):
        from rbl.gui.widgets.mini import MiniBar
        bar = MiniBar("Amp A1", 0.0, 10.0)
        bar.set(50.0)
        assert bar.fraction() == 1.0

    def test_none_value_has_no_fraction(self, qapp):
        from rbl.gui.widgets.mini import MiniBar
        bar = MiniBar("Amp A1", 0.0, 10.0)
        bar.set(5.0)
        bar.set(None)
        assert bar.fraction() is None
        assert bar.track._frac is None
        assert bar.lbl_value.text() == "—"

    def test_nan_value_has_no_fraction(self, qapp):
        """NaN means 'channel not sampled this window' — drawing it as zero
        would read as a measurement of zero."""
        from rbl.gui.widgets.mini import MiniBar
        bar = MiniBar("Amp A1", 0.0, 10.0)
        bar.set(float("nan"))
        assert bar.fraction() is None
        assert bar.lbl_value.text() == "—"

    def test_value_label_carries_unit_and_decimals(self, qapp):
        from rbl.gui.widgets.mini import MiniBar
        bar = MiniBar("X+ position", 0.0, 10.0, unit="mm", decimals=3)
        bar.set(1.23456)
        assert bar.lbl_value.text() == "1.235 mm"

    def test_stale_mutes_the_bar_colour(self, qapp):
        from rbl.gui import theme
        from rbl.gui.widgets.mini import MiniBar
        bar = MiniBar("Amp A1", 0.0, 10.0)
        bar.set(5.0, stale=True)
        assert bar.track._color == theme.MUTED

    def test_role_overrides_the_bar_colour(self, qapp):
        from rbl.gui import theme
        from rbl.gui.widgets.mini import MiniBar
        bar = MiniBar("Amp A1", 0.0, 10.0)
        bar.set(5.0, role=theme.WARN)
        assert bar.track._color == theme.WARN

    def test_target_marks_a_setpoint_on_the_same_scale(self, qapp):
        from rbl.gui.widgets.mini import MiniBar
        bar = MiniBar("X+ position", 0.0, 10.0)
        bar.set_target(2.5)
        assert bar.track._target == pytest.approx(0.25)

    def test_target_none_clears_the_marker(self, qapp):
        from rbl.gui.widgets.mini import MiniBar
        bar = MiniBar("X+ position", 0.0, 10.0)
        bar.set_target(2.5)
        bar.set_target(None)
        assert bar.track._target is None

    def test_log_scale_places_a_value_by_decade(self, qapp):
        """Three of six decades up (1 µA on a 1 nA–1 mA log amp) is half way
        along the bar; on a linear scale it would be invisible at the left."""
        from rbl.gui.widgets.mini import MiniBar
        bar = MiniBar("X+ current", 1e-9, 1e-3, log_scale=True)
        bar.set(1e-6)
        assert bar.fraction() == pytest.approx(0.5)

    def test_log_scale_clamps_below_the_floor(self, qapp):
        from rbl.gui.widgets.mini import MiniBar
        bar = MiniBar("X+ current", 1e-9, 1e-3, log_scale=True)
        bar.set(1e-12)
        assert bar.fraction() == 0.0

    def test_log_scale_handles_zero_and_negative(self, qapp):
        """A log amp reading at or below zero is 'no signal', not a maths error."""
        from rbl.gui.widgets.mini import MiniBar
        bar = MiniBar("X+ current", 1e-9, 1e-3, log_scale=True)
        bar.set(0.0)
        assert bar.fraction() == 0.0
        bar.set(-1e-9)
        assert bar.fraction() == 0.0

    def test_custom_formatter_owns_the_value_text(self, qapp):
        from rbl.gui.widgets.mini import MiniBar
        bar = MiniBar("X+ current", 1e-9, 1e-3, log_scale=True,
                      fmt=lambda v: f"{v * 1e9:.0f} nA")
        bar.set(3.2e-7)
        assert bar.lbl_value.text() == "320 nA"

    def test_scale_captions_label_both_ends_and_the_middle(self, qapp):
        from rbl.gui.widgets.mini import MiniBar
        bar = MiniBar("X+ position", 0.0, 10.0, ticks=5)
        labels = bar.track._tick_labels
        assert labels[0] == "0"
        assert labels[-1] == "10"
        assert labels[len(labels) // 2] == "5"


class TestSparkline:
    def test_initial_value_is_placeholder(self, qapp):
        from rbl.gui.widgets.mini import Sparkline
        spark = Sparkline("HV X+")
        assert spark.lbl_value.text() == "—"

    def test_push_updates_label_and_history(self, qapp):
        from rbl.gui.widgets.mini import Sparkline
        spark = Sparkline("HV X+")
        spark.push(3.14159)
        assert spark.lbl_value.text() == "3.14"
        assert spark._values == [3.14159]

    def test_push_nan_shows_placeholder(self, qapp):
        from rbl.gui.widgets.mini import Sparkline
        spark = Sparkline("HV X+")
        spark.push(float("nan"))
        assert spark.lbl_value.text() == "—"

    def test_history_caps_at_max_points(self, qapp):
        from rbl.gui.widgets.mini import Sparkline
        spark = Sparkline("HV X+")
        spark._max_points = 3
        for v in range(5):
            spark.push(float(v))
        assert spark._values == [2.0, 3.0, 4.0]

    def test_trace_sees_the_same_history(self, qapp):
        from rbl.gui.widgets.mini import Sparkline
        spark = Sparkline("HV X+")
        for v in (1.0, 2.0, 3.0):
            spark.push(v)
        assert spark.trace._values == [1.0, 2.0, 3.0]
