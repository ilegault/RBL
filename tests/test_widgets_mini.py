"""
Unit tests for the dumb display widgets used by the Overview tab
(rbl.gui.widgets.mini). Each widget only renders a value it's given, so these
tests just check set()/push() produce the expected label text and state.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")
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

    def test_stale_changes_style(self, qapp):
        from rbl.gui.widgets.mini import ValueTile
        from rbl.gui import theme
        tile = ValueTile("X+", "mm")
        tile.set(1.0, stale=True)
        assert theme.MUTED in tile.lbl_value.styleSheet()


class TestMiniBar:
    def test_value_within_range_sets_bar(self, qapp):
        from rbl.gui.widgets.mini import MiniBar
        bar = MiniBar("Amp A1", 0.0, 10.0)
        bar.set(5.0)
        assert bar.bar.value() == 500

    def test_value_clamped_below_minimum(self, qapp):
        from rbl.gui.widgets.mini import MiniBar
        bar = MiniBar("Amp A1", 0.0, 10.0)
        bar.set(-5.0)
        assert bar.bar.value() == 0

    def test_value_clamped_above_maximum(self, qapp):
        from rbl.gui.widgets.mini import MiniBar
        bar = MiniBar("Amp A1", 0.0, 10.0)
        bar.set(50.0)
        assert bar.bar.value() == 1000

    def test_none_value_shows_zero(self, qapp):
        from rbl.gui.widgets.mini import MiniBar
        bar = MiniBar("Amp A1", 0.0, 10.0)
        bar.set(None)
        assert bar.bar.value() == 0


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

    def test_history_caps_at_max_points(self, qapp):
        from rbl.gui.widgets.mini import Sparkline
        spark = Sparkline("HV X+")
        spark._max_points = 3
        for v in range(5):
            spark.push(float(v))
        assert spark._values == [2.0, 3.0, 4.0]
