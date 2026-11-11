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
from rbl.state.setpoints import AXIS_CHANNELS, START_PHASE_DEFAULTS, FuncGenSetpoints


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


def test_axes_start_linked():
    sp = FuncGenSetpoints()
    assert sp.axis_linked("X") is True
    assert sp.axis_linked("Y") is True


def test_linked_offset_is_equal_and_opposite():
    sp = FuncGenSetpoints()
    link_events = []
    sp.link_changed.connect(lambda ax, lk: link_events.append((ax, lk)))

    sp.set_axis_offset("X", 0.4)
    assert sp.get("A1").offset_v == pytest.approx(0.4)
    assert sp.get("A2").offset_v == pytest.approx(-0.4)

    sp.set_linked("X", False)
    assert sp.get("A1").offset_v == pytest.approx(0.4)
    assert sp.get("A2").offset_v == pytest.approx(-0.4)
    assert link_events == [("X", False)]

    # A second call emits nothing
    sp.set_linked("X", False)
    assert link_events == [("X", False)]

    # While unlinked, set different offsets
    sp.update("A1", offset_v=0.3)
    sp.update("A2", offset_v=0.1)

    # Re-linking sets minus plate to negative of plus plate and emits
    sp.set_linked("X", True)
    assert sp.get("A1").offset_v == pytest.approx(0.3)
    assert sp.get("A2").offset_v == pytest.approx(-0.3)
    assert link_events == [("X", False), ("X", True)]
    assert sp.axis_linked("X") is True


def test_channel_offset_edit_unlinks_the_pair():
    sp = FuncGenSetpoints()
    sp.set_axis_offset("X", 0.4)
    assert sp.axis_linked("X") is True

    link_events = []
    sp.link_changed.connect(lambda ax, lk: link_events.append((ax, lk)))

    # Updating when still equal and opposite (|plus + minus| <= 1e-9) does not unlink
    sp.update("A2", offset_v=-0.4)
    assert sp.axis_linked("X") is True
    assert link_events == []

    # Edits to amplitude, frequency, or output never change the link
    sp.update("A1", amp_vpp=2.0)
    sp.update("A2", freq_hz=20.0)
    sp.update("A1", output_on=True)
    assert sp.axis_linked("X") is True
    assert link_events == []

    # Updating A1 so that pair is no longer equal and opposite unlinks
    sp.update("A1", offset_v=0.5)
    assert sp.axis_linked("X") is False
    assert link_events == [("X", False)]


def test_update_axis_refuses_to_touch_offset():
    sp = FuncGenSetpoints()
    sp.set_axis_offset("X", 0.4)
    sp.update_axis("X", offset_v=1.0, amp_vpp=2.0)

    assert sp.get("A1").amp_vpp == pytest.approx(2.0)
    assert sp.get("A2").amp_vpp == pytest.approx(2.0)
    assert sp.get("A1").offset_v == pytest.approx(0.4)
    assert sp.get("A2").offset_v == pytest.approx(-0.4)


def test_send_plan_sets_a_linked_triangle_and_leaves_output_alone():
    sp = FuncGenSetpoints()
    sp.set_linked("Y", False)
    sp.update("B1", shape="Sine", output_on=True, offset_v=0.8, amp_vpp=1.0, freq_hz=10.0)
    sp.update("B2", shape="Square", output_on=True, offset_v=-0.1, amp_vpp=1.5, freq_hz=10.0)

    sp.send_plan("Y", 4.0, 0.25, 517.0)

    assert sp.axis_linked("Y") is True
    for key, expected_offset in [("B1", 0.25), ("B2", -0.25)]:
        p = sp.get(key)
        assert p.amp_vpp == pytest.approx(4.0)
        assert p.freq_hz == pytest.approx(517.0)
        assert p.shape == "Triangle"
        assert p.offset_v == pytest.approx(expected_offset)
        assert p.output_on is True
        assert p.phase_deg == pytest.approx(START_PHASE_DEFAULTS[key])
        assert p.start_phase_deg == pytest.approx(START_PHASE_DEFAULTS[key])
        assert p.load == "INFinity"
    assert sp.axis_matched("Y") is True


# ---- Both screens on one model ----------------------------------------------

def test_overview_edit_reaches_the_funcgen_panels(tabs):
    """Both screens speak Vpp, so the same number must appear on both."""
    fg, ov = tabs
    ov.drives["X"].spn_amp.setValue(4.0)      # 4 Vpp
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

    assert ov.drives["Y"].spn_amp.value() == pytest.approx(6.0)       # same 6 Vpp
    assert ov.drives["Y"].spn_freq.value() == pytest.approx(0.5)


def test_the_unit_conversion_round_trips(tabs):
    """No conversion exists between the screens any more: amplitude is Vpp on
    both, so a value typed on either arrives unchanged on the other. Any
    factor reappearing (a halving or doubling) would show here."""
    fg, ov = tabs
    ov.drives["X"].spn_amp.setValue(3.5)
    assert fg.panels["A1"].spn_amp.value() == pytest.approx(3.5)
    assert ov.drives["X"].spn_amp.value() == pytest.approx(3.5)

    fg.panels["A1"].spn_amp.setValue(3.5)
    assert ov.drives["X"].spn_amp.value() == pytest.approx(3.5)


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

    ov.drives["X"].spn_amp.setValue(2.0)

    assert fg.beamline.funcgen_setpoints.get("A1").shape == "Sine"
    assert fg.beamline.funcgen_setpoints.get("A1").offset_v == pytest.approx(1.0)
    assert fg.beamline.funcgen_setpoints.get("A1").amp_vpp == pytest.approx(2.0)


def test_funcgen_tab_still_applies_from_its_own_widgets(tabs):
    """The panels remain the source of truth for what Apply sends; the shared
    model is a mirror, not a second copy that could disagree with the boxes."""
    fg, ov = tabs
    ov.drives["Y"].spn_amp.setValue(3.0)

    params = fg.panels["B1"].get_params()
    assert params["amp"] == pytest.approx(3.0)   # same Vpp as typed
