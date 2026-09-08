"""
Function Generators tab: the mirror button and the per-channel status strip.

Two problems, one screen.

MIRROR — a push-pull pair must run at ONE amplitude and ONE frequency, but the
two channels are edited on separate panels, so keeping them equal meant typing
the same two numbers twice and hoping. The Mirror button copies amplitude and
frequency onto the partner channel, and copies nothing else: the 0°/180° phase
split is what makes the pair differential, and mirroring it would collapse the
pair onto one phase.

STATUS — the Output button says what has been ASKED for. Nothing said what the
instrument was actually DOING, so fiddling with the button without pressing
Apply left no way to tell an armed channel from a dark one, on a panel that
drives ±5 kV at a plate. Both new indicators read from the instrument's own
readback, which is the whole point:
  the dot   — is this output relay closed right now?
  the badge — do the boxes on screen differ from what the instrument holds?
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication

from rbl.gui import theme
from rbl.state.beamline import Beamline


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp):
    from rbl.gui.funcgen_tab import FuncGenTab
    return FuncGenTab(Beamline())


def _readback(shape="TRI", freq=10.0, amp=0.0, offset=0.0, phase=0.0,
              output=False):
    """What DG1022Z.get_state() returns — shape as the SCPI abbreviation."""
    return {"shape": shape, "freq": freq, "amp": amp, "offset": offset,
            "phase": phase, "output": output, "load": "INF"}


# ---- Mirror ------------------------------------------------------------------

def test_mirror_copies_amplitude_and_frequency_to_the_partner(tab):
    tab.panels["A2"].spn_freq.setValue(517.0)
    tab.panels["A2"].spn_amp.setValue(4.0)

    tab._mirror_channel("A2")

    assert tab.panels["A1"].spn_freq.value() == pytest.approx(517.0)
    assert tab.panels["A1"].spn_amp.value() == pytest.approx(4.0)


def test_mirror_does_not_copy_phase(tab):
    """0° on the '+' plate and 180° on the '-' one is what makes the pair
    push-pull; copying phase would collapse both onto one and steer nothing."""
    before = tab.panels["A1"].spn_phase.value()
    tab.panels["A2"].spn_amp.setValue(3.0)
    tab._mirror_channel("A2")
    assert tab.panels["A1"].spn_phase.value() == pytest.approx(before)
    assert tab.panels["A1"].spn_phase.value() != tab.panels["A2"].spn_phase.value()


def test_mirror_writes_through_the_shared_setpoint_model(tab):
    """A mirror that only moved spinboxes would be undone by the next sync,
    and Apply would still send the old number."""
    tab.panels["B1"].spn_freq.setValue(250.0)
    tab._mirror_channel("B1")
    assert tab.beamline.funcgen_setpoints.get("B2").freq_hz == pytest.approx(250.0)


def test_mirror_stays_inside_its_own_axis(tab):
    """X and Y are separate pairs on separate units — never each other's."""
    tab.panels["A1"].spn_amp.setValue(2.0)
    tab._mirror_channel("A1")
    assert tab.panels["B1"].spn_amp.value() == pytest.approx(0.0)
    assert tab.panels["B2"].spn_amp.value() == pytest.approx(0.0)


def test_mirror_sends_nothing_to_the_instrument(tab):
    """Like every other control here, it edits a setpoint. Typing is safe;
    Apply is the commit."""
    sent = []
    tab.beamline.set_channel = lambda *a, **k: sent.append(a)
    tab.beamline.apply_all_channels = lambda *a, **k: sent.append(a)
    tab.panels["A1"].spn_amp.setValue(1.0)
    tab._mirror_channel("A1")
    assert sent == []


def test_each_panel_names_the_channel_it_mirrors_onto(tab):
    assert "X-" in tab.panels["A1"].btn_mirror.text()
    assert "X+" in tab.panels["A2"].btn_mirror.text()
    assert "Y-" in tab.panels["B1"].btn_mirror.text()
    assert "Y+" in tab.panels["B2"].btn_mirror.text()


# ---- Output status dot -------------------------------------------------------

def test_dot_is_grey_before_any_readback(tab):
    panel = tab.panels["A1"]
    assert "no readback" in panel.lbl_output_state.text()
    assert theme.MUTED in panel.lbl_output_state.styleSheet()


def test_dot_goes_green_when_the_instrument_reports_the_output_on(tab):
    panel = tab.panels["A1"]
    panel.update_readback(_readback(output=True))
    assert "ON" in panel.lbl_output_state.text()
    assert theme.OK in panel.lbl_output_state.styleSheet()


def test_dot_follows_the_instrument_not_the_output_button(tab):
    """This is the bug: clicking Output ON without applying used to look
    identical to a channel that was actually driving."""
    panel = tab.panels["A1"]
    panel.update_readback(_readback(output=False))
    panel.btn_output.setChecked(True)
    assert theme.OK not in panel.lbl_output_state.styleSheet()
    assert "off" in panel.lbl_output_state.text()


def test_a_dropped_session_clears_the_dot(tab):
    """A stale green dot on a disconnected channel is worse than no dot."""
    panel = tab.panels["A1"]
    panel.update_readback(_readback(output=True))
    panel.set_connected(False)
    assert "no readback" in panel.lbl_output_state.text()


def test_a_read_error_is_not_shown_as_output_off(tab):
    panel = tab.panels["A1"]
    panel.update_readback({"error": "timeout"})
    assert "no readback" in panel.lbl_output_state.text()


# ---- "not applied" badge -----------------------------------------------------

def test_no_badge_when_the_boxes_match_the_instrument(tab):
    panel = tab.panels["A1"]
    panel.spn_freq.setValue(10.0)
    panel.spn_amp.setValue(2.0)
    panel.update_readback(_readback(freq=10.0, amp=2.0, phase=0.0))
    assert panel.lbl_pending.text() == ""


def test_badge_names_the_field_that_has_not_been_applied(tab):
    panel = tab.panels["A1"]
    panel.update_readback(_readback(freq=10.0, amp=2.0))
    panel.spn_amp.setValue(4.0)
    assert "amplitude" in panel.lbl_pending.text()
    assert "not applied" in panel.lbl_pending.text()


def test_badge_catches_an_output_intent_that_was_never_applied(tab):
    """The exact case that caused the confusion: the button says ON, the
    instrument says off, and until now nothing said so."""
    panel = tab.panels["A1"]
    panel.update_readback(_readback(output=False))
    panel.btn_output.setChecked(True)
    assert "output" in panel.lbl_pending.text()


def test_no_badge_before_there_is_anything_to_compare_against(tab):
    """With no readback, "differs from the instrument" has no meaning — and a
    warning nobody can act on is noise."""
    panel = tab.panels["A1"]
    panel.spn_amp.setValue(4.0)
    assert panel.lbl_pending.text() == ""


def test_shape_is_compared_across_the_scpi_abbreviation(tab):
    """The instrument answers "TRI"; the panel says "Triangle". Comparing them
    naively would flag every channel as unapplied, forever."""
    panel = tab.panels["A1"]
    panel.cbo_shape.setCurrentText("Triangle")
    panel.update_readback(_readback(shape="TRI"))
    assert "shape" not in panel.lbl_pending.text()

    panel.cbo_shape.setCurrentText("Square")
    assert "shape" in panel.lbl_pending.text()


def test_dc_mode_hides_each_units_label_with_its_box(tab):
    """A unit is part of its box. Left behind, "Hz" floats in an empty row."""
    panel = tab.panels["A1"]
    panel.cbo_shape.setCurrentText("DC")
    assert not panel.spn_freq.isVisible()
    assert not panel.spn_freq.unit_label.isVisible()
    assert not panel.spn_amp.unit_label.isVisible()
    assert not panel.spn_phase.unit_label.isVisible()

    panel.cbo_shape.setCurrentText("Triangle")
    assert panel.spn_freq.unit_label.isVisibleTo(panel)


def test_a_high_frequency_is_not_flagged_by_float_dust(tab):
    """A relative tolerance, because the DG1022Z reports 5 MHz to the same
    significant figures as 0.5 Hz."""
    panel = tab.panels["A1"]
    panel.spn_freq.setValue(5_000_000.0)
    panel.update_readback(_readback(freq=5_000_000.0001))
    assert "frequency" not in panel.lbl_pending.text()
