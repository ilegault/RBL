"""
SlitControl: the per-slit position bar + target entry + Move button used on
the Overview tab.

Target-only by design: there is no step size and no -/+ nudge. Relative
motion lives on the Stepper Motors tab, which has the limit-switch context
that makes it safe, so the absence of those controls is asserted here rather
than merely untested.

The widget commands nothing itself — it only emits move_requested(slit, mm).
These tests therefore capture that signal rather than any hardware call.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from rbl.config import hardware_config as SC


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ctrl(qapp):
    from rbl.gui.widgets.slit_control import SlitControl
    c = SlitControl("X+")
    c.set_enabled(True)
    return c


@pytest.fixture
def requests(ctrl):
    seen = []
    ctrl.move_requested.connect(lambda slit, mm: seen.append((slit, mm)))
    return seen


def test_move_requests_the_target_box_value(ctrl, requests):
    ctrl.spn_target.setValue(1.5)
    ctrl.btn_move.click()
    assert requests == [("X+", pytest.approx(1.5))]


def test_no_step_controls_on_this_widget(ctrl):
    """The Overview is target-only. Nudge buttons and a step-size box would
    put relative motion on a screen with no limit-switch readout."""
    for attr in ("btn_plus", "btn_minus", "cbo_step", "step_mm"):
        assert not hasattr(ctrl, attr), f"SlitControl still exposes {attr}"


def test_position_drives_the_bar(ctrl):
    ctrl.set_position(SC.SLIT_DISPLAY_MAX_MM / 2.0)
    assert ctrl.bar.fraction() == pytest.approx(0.5)
    assert ctrl.bar.lbl_value.text().endswith("mm")


def test_target_box_marks_the_bar_before_anything_moves(ctrl):
    ctrl.spn_target.setValue(SC.SLIT_DISPLAY_MAX_MM / 4.0)
    assert ctrl.bar.target_fraction == pytest.approx(0.25)


def test_stale_position_reports_no_galil(ctrl):
    ctrl.set_position(None, stale=True)
    assert "not connected" in ctrl.lbl_state.text()
    assert ctrl.bar.fraction() is None


def test_moving_axis_is_called_out(ctrl):
    from rbl.gui import theme
    ctrl.set_position(1.0, moving=True)
    assert ctrl.lbl_state.text() == "moving"
    assert ctrl.bar.track.color == theme.WARN


def test_disabled_axis_is_called_out(ctrl):
    ctrl.set_position(1.0, enabled=False)
    assert "disabled" in ctrl.lbl_state.text()


def test_controls_start_disabled(qapp):
    from rbl.gui.widgets.slit_control import SlitControl
    fresh = SlitControl("Y-")
    assert not fresh.btn_move.isEnabled()
    assert not fresh.spn_target.isEnabled()


def test_sync_target_preloads_the_live_position(ctrl):
    ctrl.set_position(4.25)
    ctrl.sync_target_to_position()
    assert ctrl.spn_target.value() == pytest.approx(4.25)


def test_sync_target_is_a_noop_without_a_position(ctrl):
    ctrl.spn_target.setValue(1.0)
    ctrl.set_position(None, stale=True)
    ctrl.sync_target_to_position()
    assert ctrl.spn_target.value() == pytest.approx(1.0)
