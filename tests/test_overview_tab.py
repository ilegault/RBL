"""
Overview tab: a read-only composition of every subsystem's live snapshot.

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


def test_no_hardware_actuating_controls():
    """Phase 9's explicit gate: no control here moves a jaw or raises a
    voltage until the repo owner signs off. Assert the tab never CALLS one
    of Beamline's command methods (mentioning them in a docstring, as this
    module does to explain the gate, is fine)."""
    import inspect
    from rbl.gui.overview_tab import OverviewTab as OT

    source = inspect.getsource(OT)
    for forbidden in ("move_jaw", "emergency_stop", "set_channel",
                      "apply_all_channels", "all_outputs_off"):
        assert f".{forbidden}(" not in source, f"OverviewTab must not call {forbidden}()"


def test_caches_motor_state_and_redraws_jaw_tile(tab):
    axes = {"X+": AxisSnapshot(pos_counts=1000, pos_mm=2.5, moving=False,
                                enabled=True, switches={})}
    tab.beamline.motors_changed.emit(MotorState(connected=True, zeroed=True, axes=axes))
    tab._redraw()
    assert tab.jaws["X+"].lbl_value.text() == "2.500 mm"


def test_jaw_tile_stale_when_motors_disconnected(tab):
    tab.beamline.motors_changed.emit(MotorState(connected=False, zeroed=False, axes={}))
    tab._redraw()
    assert tab.jaws["X+"].lbl_value.text() == "—"


def test_logamp_state_feeds_the_beam_indicator(tab):
    tab.beamline.logamps_changed.emit(LogAmpState(connected=True, currents={"X+": 1e-6}))
    tab._redraw()
    assert tab.beam._currents.get("X+") == 1e-6


def test_amp_state_feeds_hv_sparkline(tab):
    channels = {"X+": AmpChannelSnapshot(peak_kv=3.0, pkpk_kv=6.0, rms_kv=2.1,
                                          rms_ma=0.5, raw_v=3.0, raw_i=0.1)}
    tab.beamline.amps_changed.emit(AmpState(connected=True, channels=channels))
    tab._redraw()
    assert tab.hv["X+"].lbl_value.text() == "3.00"


def test_funcgen_state_feeds_amp_bar(tab):
    channels = {"A1": ChannelSnapshot(shape="Sine", freq_hz=1000.0, amp_vpp=1.5,
                                       offset_v=0.0, phase_deg=0.0, output_on=True)}
    tab.beamline.funcgens_changed.emit(
        FuncGenState(connected={"A": True, "B": False}, timebase={}, channels=channels)
    )
    tab._redraw()
    # MiniBar range is 0..MAX_AMP_VPP; 1.5 Vpp should read as a non-zero bar.
    assert tab.amps["A1"].bar.value() > 0


def test_funcgen_bar_stale_when_generator_not_connected(tab):
    tab.beamline.funcgens_changed.emit(
        FuncGenState(connected={"A": False, "B": False}, timebase={}, channels={})
    )
    tab._redraw()
    assert tab.amps["A1"].bar.value() == 0


def test_command_failed_updates_the_status_label(tab):
    tab.beamline.command_failed.emit("funcgen", "A1: combined peak 5.5 V exceeds limit")
    assert "A1" in tab.lbl_failure.text()
    assert "funcgen" in tab.lbl_failure.text()


def test_redraw_is_a_noop_while_hidden(tab):
    axes = {"X+": AxisSnapshot(pos_counts=1000, pos_mm=2.5, moving=False,
                                enabled=True, switches={})}
    tab.beamline.motors_changed.emit(MotorState(connected=True, zeroed=True, axes=axes))
    tab._redraw()
    assert tab.jaws["X+"].lbl_value.text() == "2.500 mm"

    # New data arrives while hidden: cached, but not painted.
    tab._visible = False
    axes2 = {"X+": AxisSnapshot(pos_counts=2000, pos_mm=5.0, moving=False,
                                 enabled=True, switches={})}
    tab.beamline.motors_changed.emit(MotorState(connected=True, zeroed=True, axes=axes2))
    tab._redraw()
    assert tab.jaws["X+"].lbl_value.text() == "2.500 mm"


def test_show_event_starts_timer_and_repaints_immediately(tab):
    tab._visible = False
    tab._redraw_timer.stop()
    axes = {"X+": AxisSnapshot(pos_counts=1000, pos_mm=7.0, moving=False,
                                enabled=True, switches={})}
    tab.beamline.motors_changed.emit(MotorState(connected=True, zeroed=True, axes=axes))

    tab.showEvent(QShowEvent())

    assert tab._visible is True
    assert tab._redraw_timer.isActive()
    assert tab.jaws["X+"].lbl_value.text() == "7.000 mm"

    tab.hideEvent(QHideEvent())
    assert tab._visible is False
    assert not tab._redraw_timer.isActive()
