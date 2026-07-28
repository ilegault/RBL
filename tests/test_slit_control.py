"""
SlitControl: the per-slit position bar + target entry + move controls used on
the Overview tab.

The widget commands nothing itself — it only emits move_requested(slit, mm).
These tests therefore capture that signal rather than any hardware call.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")
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


def test_step_buttons_move_relative_to_the_live_position(ctrl, requests):
    """Not relative to the target box: two clicks during a move in flight
    would otherwise command twice the step from a position never reached."""
    ctrl.set_position(2.0)
    ctrl.spn_target.setValue(9.0)      # stale target the operator typed earlier
    ctrl.cbo_step.setCurrentIndex(SC.SLIT_STEP_CHOICES_MM.index(0.1))

    ctrl.btn_plus.click()
    ctrl.btn_minus.click()

    assert requests[0] == ("X+", pytest.approx(2.1))
    # The second click starts from the live position again (still 2.0 — the
    # slit has not reported a new one), not from the 2.1 just commanded.
    assert requests[1] == ("X+", pytest.approx(1.9))


def test_step_button_updates_the_target_box_to_match(ctrl, requests):
    ctrl.set_position(2.0)
    ctrl.cbo_step.setCurrentIndex(SC.SLIT_STEP_CHOICES_MM.index(0.5))
    ctrl.btn_plus.click()
    assert ctrl.spn_target.value() == pytest.approx(2.5)


def test_step_is_clamped_to_the_display_range(ctrl, requests):
    ctrl.set_position(SC.SLIT_DISPLAY_MIN_MM)
    ctrl.cbo_step.setCurrentIndex(SC.SLIT_STEP_CHOICES_MM.index(1.0))
    ctrl.btn_minus.click()
    assert requests == [("X+", pytest.approx(SC.SLIT_DISPLAY_MIN_MM))]


def test_step_falls_back_to_the_target_box_when_position_unknown(ctrl, requests):
    ctrl.set_position(None, stale=True)
    ctrl.spn_target.setValue(3.0)
    ctrl.cbo_step.setCurrentIndex(SC.SLIT_STEP_CHOICES_MM.index(0.1))
    ctrl.btn_plus.click()
    assert requests == [("X+", pytest.approx(3.1))]


def test_position_drives_the_bar(ctrl):
    ctrl.set_position(SC.SLIT_DISPLAY_MAX_MM / 2.0)
    assert ctrl.bar.fraction() == pytest.approx(0.5)
    assert ctrl.bar.lbl_value.text().endswith("mm")


def test_target_box_marks_the_bar_before_anything_moves(ctrl):
    ctrl.spn_target.setValue(SC.SLIT_DISPLAY_MAX_MM / 4.0)
    assert ctrl.bar.track._target == pytest.approx(0.25)


def test_stale_position_reports_no_galil(ctrl):
    ctrl.set_position(None, stale=True)
    assert "not connected" in ctrl.lbl_state.text()
    assert ctrl.bar.fraction() is None


def test_moving_axis_is_called_out(ctrl):
    from rbl.gui import theme
    ctrl.set_position(1.0, moving=True)
    assert ctrl.lbl_state.text() == "moving"
    assert ctrl.bar.track._color == theme.WARN


def test_disabled_axis_is_called_out(ctrl):
    ctrl.set_position(1.0, enabled=False)
    assert "disabled" in ctrl.lbl_state.text()


def test_controls_start_disabled(qapp):
    from rbl.gui.widgets.slit_control import SlitControl
    fresh = SlitControl("Y-")
    assert not fresh.btn_move.isEnabled()
    assert not fresh.spn_target.isEnabled()
    assert not fresh.btn_plus.isEnabled()


def test_sync_target_preloads_the_live_position(ctrl):
    ctrl.set_position(4.25)
    ctrl.sync_target_to_position()
    assert ctrl.spn_target.value() == pytest.approx(4.25)


def test_sync_target_is_a_noop_without_a_position(ctrl):
    ctrl.spn_target.setValue(1.0)
    ctrl.set_position(None, stale=True)
    ctrl.sync_target_to_position()
    assert ctrl.spn_target.value() == pytest.approx(1.0)
