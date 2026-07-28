"""
Overview tab: every subsystem's live snapshot on one screen, plus the slit
motion controls and the per-axis raster drive.

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


def _amp(peak_kv, wave=()):
    return AmpChannelSnapshot(peak_kv=peak_kv, pkpk_kv=2 * peak_kv,
                              rms_kv=peak_kv * 0.7, rms_ma=0.5,
                              raw_v=peak_kv, raw_i=0.1, wave_kv=tuple(wave))


def _triangle(peak, n=48, invert=False):
    """One cycle of a triangle, as the deflection monitors would sample it."""
    out = []
    for i in range(n):
        f = (i % n) / n
        v = 4 * f - 1 if f < 0.5 else 3 - 4 * f
        out.append(peak * (-v if invert else v))
    return out


def test_amp_state_feeds_the_hv_peak_bars(tab):
    tab.beamline.amps_changed.emit(
        AmpState(connected=True, channels={"X+": _amp(3.0)}))
    tab._redraw()
    assert tab.hv_bars["X+"].lbl_value.text() == "3.00 kV"
    assert tab.hv_bars["X+"].fraction() == pytest.approx(3.0 / 5.0)


def test_a_push_pull_pair_reads_as_locked(tab):
    """X+ and X- as mirror images correlate at -1 — the check the overlaid
    traces exist to make, in a number."""
    from rbl.gui import theme
    tab.beamline.amps_changed.emit(AmpState(connected=True, channels={
        "X+": _amp(2.0, _triangle(2.0)),
        "X-": _amp(2.0, _triangle(2.0, invert=True)),
    }))
    tab._redraw()
    assert "locked" in tab.hv_phase["X"].text()
    assert theme.OK in tab.hv_phase["X"].styleSheet()


def test_two_in_phase_channels_are_flagged(tab):
    """Both plates swinging the same way is not a differential drive — it
    steers nothing and doubles the common-mode voltage."""
    from rbl.gui import theme
    tab.beamline.amps_changed.emit(AmpState(connected=True, channels={
        "X+": _amp(2.0, _triangle(2.0)),
        "X-": _amp(2.0, _triangle(2.0)),
    }))
    tab._redraw()
    assert "NOT anti-phase" in tab.hv_phase["X"].text()
    assert theme.FAULT in tab.hv_phase["X"].styleSheet()


def test_no_waveform_is_reported_as_absent_not_as_a_fault(tab):
    """Some stream profiles do not sample the amplifier monitors. That is not
    a phase problem and must not be drawn as one."""
    tab.beamline.amps_changed.emit(AmpState(connected=True, channels={
        "X+": _amp(2.0), "X-": _amp(2.0),
    }))
    tab._redraw()
    assert "no waveform" in tab.hv_phase["X"].text()


def test_pair_trace_gets_both_channels_on_one_scale(tab):
    tab.beamline.amps_changed.emit(AmpState(connected=True, channels={
        "Y+": _amp(1.0, _triangle(1.0)),
        "Y-": _amp(1.0, _triangle(1.0, invert=True)),
    }))
    tab._redraw()
    assert len(tab.hv_traces["Y"]._a) == 48
    assert len(tab.hv_traces["Y"]._b) == 48


def _funcgen_pair(amp=1.5, freq=10.0, output=True, **overrides):
    """Both channels of both axes reading the same thing."""
    def snap(**kw):
        base = dict(shape="Triangle", freq_hz=freq, amp_vpp=amp,
                    offset_v=0.0, phase_deg=0.0, output_on=output)
        base.update(kw)
        return ChannelSnapshot(**base)
    channels = {k: snap() for k in ("A1", "A2", "B1", "B2")}
    for key, kw in overrides.items():
        channels[key] = snap(**kw)
    return channels


def test_funcgen_readback_feeds_the_axis_amplitude_bar(tab):
    tab.beamline.funcgens_changed.emit(FuncGenState(
        connected={"A": True, "B": False}, timebase={},
        channels=_funcgen_pair(amp=1.5)))
    tab._redraw()
    # MiniBar range is 0..MAX_AMP_VPP; 1.5 Vpp should read as a non-zero bar.
    assert tab.drives["X"].bar.fraction() > 0
    assert "0.75" in tab.drives["X"].lbl_readback.text()   # 1.5 Vpp = 0.75 V pk
    assert "out=ON" in tab.drives["X"].lbl_readback.text()


def test_axis_bar_blank_when_generator_not_connected(tab):
    tab.beamline.funcgens_changed.emit(
        FuncGenState(connected={"A": False, "B": False}, timebase={}, channels={})
    )
    tab._redraw()
    assert tab.drives["X"].bar.fraction() is None
    assert "not connected" in tab.drives["X"].lbl_readback.text()
    assert not tab.drives["X"].spn_amp.isEnabled()
    assert not tab.btn_apply_all.isEnabled()


def test_a_split_pair_is_called_out_on_the_live_line(tab):
    """X+ and X- at different amplitudes are not a differential pair, whatever
    the setpoint boxes say."""
    from rbl.gui import theme
    tab.beamline.funcgens_changed.emit(FuncGenState(
        connected={"A": True, "B": True}, timebase={},
        channels=_funcgen_pair(amp=1.5, A2={"amp_vpp": 0.5})))
    tab._redraw()
    assert theme.WARN in tab.drives["X"].lbl_readback.styleSheet()
    assert theme.WARN not in tab.drives["Y"].lbl_readback.styleSheet()


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


def test_no_stop_button_on_this_screen(tab):
    """The abort lives on the Stepper Motors tab, with the limit-switch and
    per-axis state that says what was actually stopped."""
    assert not hasattr(tab, "btn_stop")


def test_slit_controls_follow_the_galil_connection(tab):
    tab.beamline.motors_changed.emit(MotorState(connected=False, zeroed=False, axes={}))
    tab._redraw()
    assert not tab.slits["X+"].btn_move.isEnabled()

    _connect_motors(tab, **{"X+": 1.0})
    assert tab.slits["X+"].btn_move.isEnabled()


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


def test_hv_amplifiers_stay_read_only(tab):
    """Slits and the raster drive are actuated from here; the HV amplifiers
    are not — nothing on this screen commands them."""
    import inspect
    from rbl.gui.overview_tab import OverviewTab as OT

    source = inspect.getsource(OT)
    assert ".all_outputs_off(" not in source
    # Per-channel apply belongs to the Function Generators tab; the Overview
    # only ever applies the whole raster at once, so its channels cannot come
    # up in a half-configured state.
    assert ".set_channel(" not in source


def test_the_tab_holds_no_driver(tab):
    """Every command still leaves through Beamline. A widget that reached a
    driver directly would bypass the +/-5 V interlock that lives there."""
    import inspect
    from rbl.gui.overview_tab import OverviewTab as OT

    source = inspect.getsource(OT)
    for forbidden in ("dg_a", "dg_b", ".galil", ".lj"):
        assert forbidden not in source, f"OverviewTab must not touch {forbidden}"


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


# ---- Raster drive: setpoints and Apply ---------------------------------------

def _connect_gens(tab, a=True, b=True):
    tab.beamline.funcgens_changed.emit(
        FuncGenState(connected={"A": a, "B": b}, timebase={}, channels={}))
    tab._redraw()


def test_axis_edit_writes_both_channels_of_that_axis(tab):
    """X+ and X- are one push-pull pair — an axis-level amplitude that landed
    on only one of them would break the differential drive, not halve it."""
    tab.drives["X"].spn_amp.setValue(2.0)     # peak volts on screen
    tab.drives["X"].spn_freq.setValue(25.0)

    setpoints = tab.beamline.funcgen_setpoints
    for key in ("A1", "A2"):
        # Stored peak-to-peak: the model and every driver call below it speak
        # the instrument's units, and only the widget's face is in peak volts.
        assert setpoints.get(key).amp_vpp == pytest.approx(4.0)
        assert setpoints.get(key).freq_hz == pytest.approx(25.0)
    # The other axis is untouched.
    assert setpoints.get("B1").amp_vpp == pytest.approx(0.0)


def test_axis_edit_never_flattens_the_push_pull_phase(tab):
    """0 deg / 180 deg is the whole reason the pair exists; no axis-level edit
    may collapse both channels onto the same phase."""
    tab.drives["X"].spn_amp.setValue(2.0)
    tab.drives["Y"].spn_freq.setValue(0.5)

    setpoints = tab.beamline.funcgen_setpoints
    assert setpoints.get("A1").start_phase_deg == pytest.approx(0.0)
    assert setpoints.get("A2").start_phase_deg == pytest.approx(180.0)
    assert setpoints.get("B1").start_phase_deg == pytest.approx(0.0)
    assert setpoints.get("B2").start_phase_deg == pytest.approx(180.0)


def test_output_toggle_is_intent_not_a_command(tab):
    """Same contract as the Function Generators tab: typing and toggling are
    safe, Apply is the commit."""
    applied = []
    tab.beamline.apply_all_channels = lambda p: applied.append(p) or True
    _connect_gens(tab)

    tab.drives["X"].btn_output.setChecked(True)

    assert applied == []
    assert tab.beamline.funcgen_setpoints.get("A1").output_on is True
    assert tab.beamline.funcgen_setpoints.get("A2").output_on is True


def test_apply_all_sends_every_channel_through_beamline(tab):
    applied = []
    tab.beamline.apply_all_channels = lambda p: applied.append(p) or True
    _connect_gens(tab)
    tab.drives["X"].spn_amp.setValue(2.0)
    tab.drives["Y"].spn_amp.setValue(3.0)

    tab.btn_apply_all.click()

    assert len(applied) == 1
    sent = applied[0]
    assert set(sent) == {"A1", "A2", "B1", "B2"}
    assert sent["A1"].amp_vpp == pytest.approx(4.0)
    assert sent["B2"].amp_vpp == pytest.approx(6.0)
    assert sent["A2"].start_phase_deg == pytest.approx(180.0)


def test_apply_all_asks_before_a_high_peak(tab):
    """|offset| + amp/2 above the advisory threshold gets one confirmation;
    the hard ceiling is Beamline's call, not this dialog's."""
    applied = []
    tab.beamline.apply_all_channels = lambda p: applied.append(p) or True
    _connect_gens(tab)
    tab.drives["X"].spn_amp.setValue(4.5)   # peak 4.5 V, past the 4 V advisory

    tab._confirm_high_peak = lambda warned: False
    tab.btn_apply_all.click()
    assert applied == []

    tab._confirm_high_peak = lambda warned: True
    tab.btn_apply_all.click()
    assert len(applied) == 1


def test_apply_all_does_not_ask_below_the_advisory_threshold(tab):
    applied = []
    tab.beamline.apply_all_channels = lambda p: applied.append(p) or True
    tab._confirm_high_peak = lambda warned: pytest.fail(
        "an in-spec amplitude must not prompt")
    _connect_gens(tab)
    tab.drives["X"].spn_amp.setValue(2.0)   # peak 2 V

    tab.btn_apply_all.click()
    assert len(applied) == 1


def test_apply_all_is_disabled_until_a_generator_is_connected(tab):
    _connect_gens(tab, a=False, b=False)
    assert not tab.btn_apply_all.isEnabled()
    _connect_gens(tab, a=True, b=False)
    assert tab.btn_apply_all.isEnabled()


def test_redraw_does_not_fight_the_operator_typing_an_amplitude(tab):
    """Readback runs at 10 Hz; the setpoint boxes are driven by the shared
    model, so a redraw must never overwrite a half-typed value."""
    _connect_gens(tab)
    tab.drives["X"].spn_amp.setValue(2.0)

    tab.beamline.funcgens_changed.emit(FuncGenState(
        connected={"A": True, "B": True}, timebase={},
        channels=_funcgen_pair(amp=0.5)))
    tab._redraw()

    assert tab.drives["X"].spn_amp.value() == pytest.approx(2.0)
    # Readback 0.5 Vpp = 0.25 V peak on a 0..5 V peak scale.
    assert tab.drives["X"].bar.fraction() == pytest.approx(0.05)


def test_setpoint_change_from_elsewhere_lands_in_the_axis_boxes(tab):
    """An edit made on the Function Generators tab reaches this screen through
    the shared model — neither tab holds a copy."""
    tab.beamline.funcgen_setpoints.update("A1", amp_vpp=7.5, freq_hz=2.0)

    assert tab.drives["X"].spn_amp.value() == pytest.approx(3.75)
    assert tab.drives["X"].spn_freq.value() == pytest.approx(2.0)
    # A1 alone moved, so the pair no longer matches — say so rather than show
    # one channel's number as if it were both.
    assert "pair differs" in tab.drives["X"].lbl_hv.text()


# ---- 10 MHz timebase ---------------------------------------------------------

def test_timebase_checkbox_needs_both_generators(tab):
    """There is nothing to lock one generator to."""
    _connect_gens(tab, a=True, b=False)
    assert not tab.chk_ext_ref.isEnabled()
    _connect_gens(tab, a=True, b=True)
    assert tab.chk_ext_ref.isEnabled()


def test_timebase_toggle_goes_through_beamline(tab):
    """The both-EXT guard protects the instruments, so it lives on the single
    path to the drivers — this tab must not reimplement it."""
    calls = []
    tab.beamline.set_shared_timebase = lambda on: calls.append(on) or (True, "Locked: ok")
    tab.beamline.read_timebase = lambda: {"A": "INT", "B": "EXT"}
    _connect_gens(tab)

    tab.chk_ext_ref.setChecked(True)

    assert calls == [True]
    assert "EXT" in tab.lbl_timebase.text()


def test_a_failed_lock_leaves_the_box_unchecked(tab):
    """Never show a lock that is not there."""
    tab.beamline.set_shared_timebase = lambda on: (False, "Lock FAILED: reports 'INT'")
    tab.beamline.read_timebase = lambda: {"A": "INT", "B": "INT"}
    warned = []
    tab._warn = lambda title, msg: warned.append(title)
    _connect_gens(tab)

    tab.chk_ext_ref.setChecked(True)

    assert not tab.chk_ext_ref.isChecked()
    assert "FAILED" in tab.lbl_drive_note.text()
    assert warned == ["Reference clock"]


def test_the_lock_state_follows_the_other_tab(tab):
    """Toggled from the Function Generators tab, the box here must agree."""
    from rbl.gui import theme
    tab._on_timebase_changed({"A": "INT", "B": "EXT"})
    assert tab.chk_ext_ref.isChecked()
    assert theme.OK in tab.lbl_timebase.styleSheet()

    tab._on_timebase_changed({"A": "INT", "B": "INT"})
    assert not tab.chk_ext_ref.isChecked()


def test_lock_state_arrives_on_the_funcgen_snapshot(tab):
    tab.beamline.funcgens_changed.emit(FuncGenState(
        connected={"A": True, "B": True},
        timebase={"A": "INT", "B": "EXT"}, channels={}))
    tab._redraw()
    assert tab.chk_ext_ref.isChecked()
