"""
FuncGenSetpoints: one shared setpoint model behind two editing screens.

The Function Generators tab edits per channel and every field; the Overview
tab edits per axis and only amplitude, frequency and output. Both are views on
the same object, so the point of these tests is that neither can show a stale
value and neither can silently overwrite the other's numbers.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication

from rbl.state.beamline import Beamline
from rbl.state.setpoints import AXIS_CHANNELS, FuncGenSetpoints


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tabs(qapp):
    """A funcgen tab and an overview tab on ONE Beamline, as the app builds them."""
    from rbl.gui.funcgen_tab import FuncGenTab
    from rbl.gui.overview_tab import OverviewTab

    beamline = Beamline()
    return FuncGenTab(beamline), OverviewTab(beamline)


# ---- The model itself --------------------------------------------------------

def test_update_signals_only_on_a_real_change():
    """Emitting on every write would let the two screens ping-pong a value
    between each other on every keystroke."""
    sp = FuncGenSetpoints()
    seen = []
    sp.changed.connect(lambda key, params: seen.append(key))

    assert sp.update("A1", amp_vpp=2.0) is True
    assert sp.update("A1", amp_vpp=2.0) is False
    assert seen == ["A1"]


def test_update_axis_refuses_to_touch_phase():
    sp = FuncGenSetpoints()
    sp.update_axis("X", amp_vpp=3.0, phase_deg=90.0, start_phase_deg=90.0)

    assert sp.get("A1").start_phase_deg == pytest.approx(0.0)
    assert sp.get("A2").start_phase_deg == pytest.approx(180.0)
    assert sp.get("A2").amp_vpp == pytest.approx(3.0)


def test_axis_matched_reports_a_split_pair():
    sp = FuncGenSetpoints()
    assert sp.axis_matched("X")
    sp.update("A1", amp_vpp=3.0)
    assert not sp.axis_matched("X")
    sp.update("A2", amp_vpp=3.0)
    assert sp.axis_matched("X")


def test_defaults_are_a_safe_triangle_at_no_volts():
    sp = FuncGenSetpoints()
    for key in ("A1", "A2", "B1", "B2"):
        params = sp.get(key)
        assert params.shape == "Triangle"
        assert params.amp_vpp == 0.0
        assert params.offset_v == 0.0
        assert params.output_on is False


# ---- Both screens on one model ----------------------------------------------

def test_overview_edit_reaches_the_funcgen_panels(tabs):
    """The Overview edits in PEAK volts and the FG tab shows peak-to-peak, so
    the same setpoint has to arrive doubled — and only once."""
    fg, ov = tabs
    ov.drives["X"].spn_amp.setValue(2.0)      # 2 V peak
    ov.drives["X"].spn_freq.setValue(25.0)

    for key in AXIS_CHANNELS["X"]:
        assert fg.panels[key].spn_amp.value() == pytest.approx(4.0)   # 4 Vpp
        assert fg.panels[key].spn_freq.value() == pytest.approx(25.0)


def test_funcgen_edit_reaches_the_overview_boxes(tabs):
    fg, ov = tabs
    fg.panels["B1"].spn_amp.setValue(6.0)     # 6 Vpp
    fg.panels["B2"].spn_amp.setValue(6.0)
    fg.panels["B1"].spn_freq.setValue(0.5)
    fg.panels["B2"].spn_freq.setValue(0.5)

    assert ov.drives["Y"].spn_amp.value() == pytest.approx(3.0)       # 3 V peak
    assert ov.drives["Y"].spn_freq.value() == pytest.approx(0.5)


def test_the_unit_conversion_round_trips(tabs):
    """A value typed on either screen must survive the trip to the other and
    back unchanged — a conversion applied twice, or in the wrong direction,
    would show up here as a factor of four."""
    fg, ov = tabs
    ov.drives["X"].spn_amp.setValue(1.75)
    assert fg.panels["A1"].spn_amp.value() == pytest.approx(3.5)
    assert ov.drives["X"].spn_amp.value() == pytest.approx(1.75)

    fg.panels["A1"].spn_amp.setValue(3.5)
    assert ov.drives["X"].spn_amp.value() == pytest.approx(1.75)


def test_a_sync_does_not_echo_back_as_an_edit(tabs):
    """Both directions are wired, so an unguarded sync would bounce the value
    between the screens forever."""
    fg, ov = tabs
    seen = []
    fg.beamline.funcgen_setpoints.changed.connect(lambda k, p: seen.append(k))

    ov.drives["X"].spn_amp.setValue(4.0)

    # Exactly one emission per channel of the edited axis, and nothing more.
    assert sorted(seen) == ["A1", "A2"]


def test_offset_and_shape_survive_an_overview_edit(tabs):
    """The Overview offers neither, so it must inherit both rather than
    stamping its own defaults over what the FG tab has set."""
    fg, ov = tabs
    fg.panels["A1"].cbo_shape.setCurrentText("Sine")
    fg.panels["A1"].spn_offset.setValue(1.0)

    ov.drives["X"].spn_amp.setValue(1.0)

    assert fg.beamline.funcgen_setpoints.get("A1").shape == "Sine"
    assert fg.beamline.funcgen_setpoints.get("A1").offset_v == pytest.approx(1.0)
    assert fg.beamline.funcgen_setpoints.get("A1").amp_vpp == pytest.approx(2.0)


def test_funcgen_tab_still_applies_from_its_own_widgets(tabs):
    """The panels remain the source of truth for what Apply sends; the shared
    model is a mirror, not a second copy that could disagree with the boxes."""
    fg, ov = tabs
    ov.drives["Y"].spn_amp.setValue(3.0)

    params = fg.panels["B1"].get_params()
    assert params["amp"] == pytest.approx(6.0)   # native Vpp
