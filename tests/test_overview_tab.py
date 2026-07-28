"""
Overview tab: every subsystem's live snapshot on one screen, plus the slit
motion controls.

These tests exercise the redraw path directly (bypassing the QTimer and
show()/hide() event delivery, which is unreliable under the offscreen
platform without a running event loop) plus the showEvent/hideEvent methods
themselves, called directly the way Qt would call them.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QShowEvent, QHideEvent

from rbl.state.beamline import Beamline
from rbl.state.snapshots import (
    MotorState, AxisSnapshot, LogAmpState, AmpState, AmpChannelSnapshot,
    FuncGenState, ChannelSnapshot,
)
from rbl.gui.overview_tab import OverviewTab


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp):
    beamline = Beamline()
    t = OverviewTab(beamline)
    t._visible = True   # bypass real show()/hide() event delivery
    return t


def _axes(**positions) -> dict:
    """MotorState.axes for the given slits, e.g. _axes(**{"X+": 2.5})."""
    return {slit: AxisSnapshot(pos_counts=int(mm * 1000), pos_mm=mm,
                               moving=False, enabled=True, switches={})
            for slit, mm in positions.items()}


def _connect_motors(tab, zeroed=True, **positions):
    tab.beamline.motors_changed.emit(
        MotorState(connected=True, zeroed=zeroed, axes=_axes(**positions)))
    tab._redraw()


# ---- Read-out path -----------------------------------------------------------

def test_caches_motor_state_and_redraws_slit_bar(tab):
    _connect_motors(tab, **{"X+": 2.5})
    assert tab.slits["X+"].bar.lbl_value.text() == "2.500 mm"


def test_slit_bar_blank_when_motors_disconnected(tab):
    tab.beamline.motors_changed.emit(MotorState(connected=False, zeroed=False, axes={}))
    tab._redraw()
    assert tab.slits["X+"].bar.lbl_value.text() == "—"
    assert tab.slits["X+"].bar.fraction() is None


def test_gap_is_the_sum_of_both_slit_positions(tab):
    _connect_motors(tab, **{"X+": 1.5, "X-": 2.0})
    assert "3.500 mm" in tab.lbl_gaps.text()


def test_gap_unknown_while_disconnected(tab):
    tab.beamline.motors_changed.emit(MotorState(connected=False, zeroed=False, axes={}))
    tab._redraw()
    assert tab.lbl_gaps.text() == "Gap  X: —   Y: —"


def test_logamp_state_feeds_the_beam_indicator(tab):
    tab.beamline.logamps_changed.emit(LogAmpState(connected=True, currents={"X+": 1e-6}))
    tab._redraw()
    assert tab.beam._currents.get("X+") == 1e-6


def test_logamp_state_feeds_the_per_slit_current_bars(tab):
    tab.beamline.logamps_changed.emit(LogAmpState(connected=True, currents={"X+": 1e-6}))
    tab._redraw()
    # 1 µA is three of the log amp's six decades up: half way along the bar.
    assert tab.currents["X+"].fraction() == pytest.approx(0.5)
    assert "µA" in tab.currents["X+"].lbl_value.text()


def test_current_bar_blank_for_an_unsampled_channel(tab):
    """NaN means the profile didn't sample that channel this window — drawing
    it as zero would read as a measured zero."""
    tab.beamline.logamps_changed.emit(
        LogAmpState(connected=True, currents={"X+": float("nan")}))
    tab._redraw()
    assert tab.currents["X+"].fraction() is None
    assert tab.currents["X+"].lbl_value.text() == "—"


def test_amp_state_feeds_hv_sparkline_and_bar(tab):
    channels = {"X+": AmpChannelSnapshot(peak_kv=3.0, pkpk_kv=6.0, rms_kv=2.1,
                                          rms_ma=0.5, raw_v=3.0, raw_i=0.1)}
    tab.beamline.amps_changed.emit(AmpState(connected=True, channels=channels))
    tab._redraw()
    assert tab.hv["X+"].lbl_value.text() == "3.00"
    assert tab.hv_bars["X+"].fraction() == pytest.approx(3.0 / 5.0)


def test_funcgen_state_feeds_amp_bar(tab):
    channels = {"A1": ChannelSnapshot(shape="Sine", freq_hz=1000.0, amp_vpp=1.5,
                                       offset_v=0.0, phase_deg=0.0, output_on=True)}
    tab.beamline.funcgens_changed.emit(
        FuncGenState(connected={"A": True, "B": False}, timebase={}, channels=channels)
    )
    tab._redraw()
    # MiniBar range is 0..MAX_AMP_VPP; 1.5 Vpp should read as a non-zero bar.
    assert tab.amps["A1"].fraction() > 0
    assert "OUT ON" in tab.amps["A1"].lbl_name.text()


def test_funcgen_bar_blank_when_generator_not_connected(tab):
    tab.beamline.funcgens_changed.emit(
        FuncGenState(connected={"A": False, "B": False}, timebase={}, channels={})
    )
    tab._redraw()
    assert tab.amps["A1"].fraction() is None
    assert "OUT OFF" in tab.amps["A1"].lbl_name.text()


def test_connection_pills_track_each_subsystem(tab):
    from rbl.gui import theme
    _connect_motors(tab, **{"X+": 1.0})
    assert theme.OK in tab.pills["motors"].styleSheet()
    assert theme.OK not in tab.pills["amps"].styleSheet()


def test_command_failed_updates_the_status_label(tab):
    tab.beamline.command_failed.emit("funcgen", "A1: combined peak 5.5 V exceeds limit")
    assert "A1" in tab.lbl_failure.text()
    assert "funcgen" in tab.lbl_failure.text()


# ---- Control path ------------------------------------------------------------

def test_move_goes_through_beamline(tab):
    """Every command leaves through Beamline's command surface — the tab
    holds no driver and does no unit conversion of its own."""
    sent = []
    tab.beamline.move_slit = lambda slit, mm: sent.append((slit, mm)) or True
    _connect_motors(tab, **{"X+": 1.0})

    tab.slits["X+"].spn_target.setValue(2.0)
    tab.slits["X+"].btn_move.click()

    assert sent == [("X+", pytest.approx(2.0))]
    assert "Commanded X+" in tab.lbl_motion.text()


def test_move_marks_the_commanded_target_on_the_bar(tab):
    tab.beamline.move_slit = lambda slit, mm: True
    _connect_motors(tab, **{"X+": 1.0})
    tab._on_move_requested("X+", 5.0)
    assert tab.slits["X+"].bar.track._target == pytest.approx(0.5)


def test_move_refused_and_reported_while_disconnected(tab):
    sent = []
    tab.beamline.move_slit = lambda slit, mm: sent.append((slit, mm)) or True
    tab.beamline.motors_changed.emit(MotorState(connected=False, zeroed=False, axes={}))
    tab._redraw()

    tab._on_move_requested("X+", 2.0)

    assert sent == []
    assert "not connected" in tab.lbl_failure.text()


def test_unzeroed_move_needs_confirmation(tab):
    """mm are meaningless until an axis is referenced, so an unzeroed move is
    a move of unknown size toward the beam."""
    sent = []
    tab.beamline.move_slit = lambda slit, mm: sent.append((slit, mm)) or True
    _connect_motors(tab, zeroed=False, **{"X+": 1.0})

    tab._confirm_unzeroed_move = lambda slit, mm: False
    tab._on_move_requested("X+", 2.0)
    assert sent == []

    tab._confirm_unzeroed_move = lambda slit, mm: True
    tab._on_move_requested("X+", 2.0)
    assert sent == [("X+", pytest.approx(2.0))]


def test_zeroed_move_asks_nothing(tab):
    sent = []
    tab.beamline.move_slit = lambda slit, mm: sent.append((slit, mm)) or True
    tab._confirm_unzeroed_move = lambda slit, mm: pytest.fail(
        "a zeroed axis must not prompt")
    _connect_motors(tab, zeroed=True, **{"X+": 1.0})

    tab._on_move_requested("X+", 2.0)
    assert sent == [("X+", pytest.approx(2.0))]


def test_unzeroed_state_is_warned_about_on_screen(tab):
    _connect_motors(tab, zeroed=False, **{"X+": 1.0})
    assert "not zeroed" in tab.lbl_motion.text()


def test_command_note_expires_and_the_warning_returns(tab):
    """An unreferenced axis stays unreferenced — the warning must come back on
    its own rather than wait for the next click to remind anybody."""
    tab.beamline.move_slit = lambda slit, mm: True
    tab._confirm_unzeroed_move = lambda slit, mm: True
    _connect_motors(tab, zeroed=False, **{"X+": 1.0})
    tab._on_move_requested("X+", 2.0)
    assert "Commanded X+" in tab.lbl_motion.text()

    for _ in range(tab._NOTE_FRAMES + 1):
        tab._redraw()
    assert "not zeroed" in tab.lbl_motion.text()


def test_stop_aborts_all_motion_without_confirmation(tab):
    stopped = []
    tab.beamline.emergency_stop = lambda: stopped.append(True)
    _connect_motors(tab, **{"X+": 1.0})

    tab.btn_stop.click()

    assert stopped == [True]
    assert "STOP sent" in tab.lbl_motion.text()


def test_slit_controls_follow_the_galil_connection(tab):
    tab.beamline.motors_changed.emit(MotorState(connected=False, zeroed=False, axes={}))
    tab._redraw()
    assert not tab.slits["X+"].btn_move.isEnabled()
    assert not tab.btn_stop.isEnabled()

    _connect_motors(tab, **{"X+": 1.0})
    assert tab.slits["X+"].btn_move.isEnabled()
    assert tab.btn_stop.isEnabled()


def test_target_boxes_preload_the_live_position_on_connect(tab):
    """So a Move right after connecting can't fling a slit somewhere from a
    leftover target value."""
    _connect_motors(tab, **{"X+": 3.75})
    assert tab.slits["X+"].spn_target.value() == pytest.approx(3.75)


def test_connected_redraws_do_not_fight_the_operator(tab):
    _connect_motors(tab, **{"X+": 3.75})
    tab.slits["X+"].spn_target.setValue(1.0)
    _connect_motors(tab, **{"X+": 3.80})
    assert tab.slits["X+"].spn_target.value() == pytest.approx(1.0)


def test_only_slit_motion_is_actuated_from_here(tab):
    """The generators and amplifiers stay read-only on the Overview screen:
    raising a voltage is deliberately a trip to the Function Generators tab,
    where the full interlock context is on screen."""
    import inspect
    from rbl.gui.overview_tab import OverviewTab as OT

    source = inspect.getsource(OT)
    for forbidden in ("set_channel", "apply_all_channels", "all_outputs_off"):
        assert f".{forbidden}(" not in source, f"OverviewTab must not call {forbidden}()"


# ---- Redraw gating -----------------------------------------------------------

def test_redraw_is_a_noop_while_hidden(tab):
    _connect_motors(tab, **{"X+": 2.5})
    assert tab.slits["X+"].bar.lbl_value.text() == "2.500 mm"

    # New data arrives while hidden: cached, but not painted.
    tab._visible = False
    tab.beamline.motors_changed.emit(
        MotorState(connected=True, zeroed=True, axes=_axes(**{"X+": 5.0})))
    tab._redraw()
    assert tab.slits["X+"].bar.lbl_value.text() == "2.500 mm"


def test_show_event_starts_timer_and_repaints_immediately(tab):
    tab._visible = False
    tab._redraw_timer.stop()
    tab.beamline.motors_changed.emit(
        MotorState(connected=True, zeroed=True, axes=_axes(**{"X+": 7.0})))

    tab.showEvent(QShowEvent())

    assert tab._visible is True
    assert tab._redraw_timer.isActive()
    assert tab.slits["X+"].bar.lbl_value.text() == "7.000 mm"

    tab.hideEvent(QHideEvent())
    assert tab._visible is False
    assert not tab._redraw_timer.isActive()
