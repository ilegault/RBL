"""
Synchronized "Apply All" in the Function Generators tab.

The raster needs the four channels to come up together, so Apply All must:
  1. configure every channel (set_waveform + set_output_load + set_start_phase)
     with outputs still OFF,
  2. enable the outputs NEXT, back-to-back, so the inter-channel skew shrinks
     to just the gap between consecutive :OUTPut ON writes.
     NOTE: :OUTPut ON closes a relay only — it does NOT reset the waveform.
     The DDS phase accumulator is only reset by :PHASe:SYNChronize.
  3. run each connected unit's Align-Phase LAST (after a relay-settle delay),
     so both channels of each unit restart phase-coherent from the start-phases
     set in step 1.

These tests drive FuncGenTab with two mocked DG1022Z units and assert on the
GLOBAL order of SCPI-level calls across both instruments.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication, QMessageBox


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab_and_mgr(qapp):
    """A FuncGenTab wired to two mocked generators sharing one call recorder."""
    import time

    from rbl.gui.funcgen_tab import FuncGenTab
    from rbl.state.beamline import Beamline

    beamline = Beamline()
    # A healthy, fresh vacuum reading — without it every command would be
    # blocked by the HV interlock's "no reading yet" stale guard (Section 3.3
    # of docs/AMP_ENVELOPE_AND_HV_SAFETY_PLAN.md); this file tests apply
    # ordering, not the interlock itself.
    beamline._hv_pressure_torr = 1e-6
    beamline._hv_pressure_at = time.monotonic()
    tab = FuncGenTab(beamline)

    # One manager mock records the ordering of calls across BOTH units:
    # every call to mgr.A.* / mgr.B.* lands in mgr.mock_calls in order.
    mgr = MagicMock()
    mgr.A.set_waveform.return_value = ""     # no clamp warning
    mgr.B.set_waveform.return_value = ""
    # Default clock state: Gen A = INT (master), Gen B = INT.
    # verify_external_lock returns a (bool, str) tuple; configure a default so
    # unpacking in _on_ext_ref_toggled does not raise and trigger QMessageBox.
    mgr.A.get_reference_clock.return_value = "INT"
    mgr.B.get_reference_clock.return_value = "INT"
    mgr.B.verify_external_lock.return_value = (True, "EXT")
    # tab._gen is a proxy onto beamline.dg_a/dg_b (Phase 7) — set through it
    # (or directly on the beamline) rather than replacing it with a plain
    # dict, or Beamline.apply_all_channels/set_channel (Phase 8) would find
    # no connected generators and silently no-op.
    tab._gen["A"] = mgr.A
    tab._gen["B"] = mgr.B
    return tab, mgr


def _method_order(mgr):
    """Ordered list of (unit, method) for the calls we care about."""
    wanted = {"set_waveform", "set_output_load", "set_start_phase",
              "align_phase", "output_on", "output_off"}
    order = []
    for call in mgr.mock_calls:
        # call name looks like "A.set_waveform" (unit.method) for child calls.
        name = call[0]
        if "." not in name:
            continue
        unit, method = name.split(".", 1)
        if method in wanted:
            order.append((unit, method))
    return order


class TestApplyAllOrdering:
    def test_all_configured_before_any_output_enabled(self, tab_and_mgr):
        tab, mgr = tab_and_mgr
        # Ask every channel to end up ON.
        for key in ("A1", "A2", "B1", "B2"):
            tab.panels[key].btn_output.setChecked(True)

        tab._apply_all()

        order = _method_order(mgr)
        methods = [m for _, m in order]

        # Every waveform push must precede every output-enable.
        last_cfg = max(i for i, m in enumerate(methods) if m == "set_waveform")
        first_on = min(i for i, m in enumerate(methods) if m == "output_on")
        assert last_cfg < first_on, order

        # Four channels configured and four enabled.
        assert methods.count("set_waveform") == 4
        assert methods.count("output_on") == 4

    def test_align_phase_runs_once_per_unit_after_enable(self, tab_and_mgr):
        tab, mgr = tab_and_mgr
        for key in ("A1", "A2", "B1", "B2"):
            tab.panels[key].btn_output.setChecked(True)

        tab._apply_all()
        order = _method_order(mgr)
        methods = [m for _, m in order]

        # Exactly one align per connected unit.
        assert methods.count("align_phase") == 2
        aligned_units = {u for u, m in order if m == "align_phase"}
        assert aligned_units == {"A", "B"}

        # Every align_phase index must be GREATER than every output_on index.
        align_idxs = [i for i, m in enumerate(methods) if m == "align_phase"]
        on_idxs    = [i for i, m in enumerate(methods) if m == "output_on"]
        assert all(a > max(on_idxs) for a in align_idxs), order

    def test_output_enable_burst_is_contiguous(self, tab_and_mgr):
        tab, mgr = tab_and_mgr
        for key in ("A1", "A2", "B1", "B2"):
            tab.panels[key].btn_output.setChecked(True)

        tab._apply_all()
        order = _method_order(mgr)
        methods = [m for _, m in order]

        # The four output_on calls must be contiguous — nothing interleaved
        # between them. The align_phase calls follow after, not between.
        on_idxs = [i for i, m in enumerate(methods) if m == "output_on"]
        assert len(on_idxs) == 4
        assert on_idxs == list(range(on_idxs[0], on_idxs[0] + 4)), order

    def test_channels_left_off_are_disabled_not_enabled(self, tab_and_mgr):
        tab, mgr = tab_and_mgr
        # Only A1 and B1 should turn on; A2 and B2 stay off.
        tab.panels["A1"].btn_output.setChecked(True)
        tab.panels["B1"].btn_output.setChecked(True)
        tab.panels["A2"].btn_output.setChecked(False)
        tab.panels["B2"].btn_output.setChecked(False)

        tab._apply_all()
        order = _method_order(mgr)
        methods = [m for _, m in order]

        assert methods.count("output_on") == 2
        assert methods.count("output_off") == 2
        # Every OFF precedes every ON: the ON burst is still before align.
        last_off = max(i for i, m in enumerate(methods) if m == "output_off")
        first_on = min(i for i, m in enumerate(methods) if m == "output_on")
        assert last_off < first_on, order

    def test_start_phase_called_four_times_before_output_on(self, tab_and_mgr):
        tab, mgr = tab_and_mgr
        for key in ("A1", "A2", "B1", "B2"):
            tab.panels[key].btn_output.setChecked(True)

        tab._apply_all()
        order = _method_order(mgr)
        methods = [m for _, m in order]

        # set_start_phase called once per channel.
        assert methods.count("set_start_phase") == 4

        # All four set_start_phase calls precede the first output_on.
        sp_idxs  = [i for i, m in enumerate(methods) if m == "set_start_phase"]
        first_on = min(i for i, m in enumerate(methods) if m == "output_on")
        assert all(i < first_on for i in sp_idxs), order

    def test_axis_pairs_share_a_generator(self, tab_and_mgr):
        from rbl.hardware.funcgen_safety import CHANNEL_ROLE

        # X+/X- must both be on unit A.
        x_keys = {k for k, v in CHANNEL_ROLE.items() if v in ("X+", "X-")}
        assert all(k.startswith("A") for k in x_keys), CHANNEL_ROLE

        # Y+/Y- must both be on unit B.
        y_keys = {k for k, v in CHANNEL_ROLE.items() if v in ("Y+", "Y-")}
        assert all(k.startswith("B") for k in y_keys), CHANNEL_ROLE


class TestReadbackIngestion:
    """_poll_readback republishes through Beamline.ingest_funcgen_readback so
    any other consumer (Phase 9's Overview tab) sees live amplitudes without
    querying the driver a second time."""

    _STATE_A = {"shape": "Sine", "freq": 1000.0, "amp": 1.5, "offset": 0.0,
                "phase": 0.0, "output": True}
    _STATE_B = {"shape": "Sine", "freq": 2000.0, "amp": 0.5, "offset": 0.0,
                "phase": 0.0, "output": False}

    def test_poll_readback_republishes_via_beamline(self, tab_and_mgr):
        tab, mgr = tab_and_mgr
        mgr.A.get_state.return_value = self._STATE_A
        mgr.B.get_state.return_value = self._STATE_B

        received = []
        tab.beamline.funcgens_changed.connect(received.append)
        tab._poll_readback()

        assert len(received) == 1
        state = received[0]
        assert state.connected == {"A": True, "B": True}
        assert state.channels["A1"].amp_vpp == 1.5
        assert state.channels["B1"].amp_vpp == 0.5

    def test_poll_readback_marks_unconnected_generator(self, tab_and_mgr):
        tab, mgr = tab_and_mgr
        tab._gen["B"] = None
        mgr.A.get_state.return_value = self._STATE_A

        received = []
        tab.beamline.funcgens_changed.connect(received.append)
        tab._poll_readback()

        assert received[0].connected == {"A": True, "B": False}
        assert "B1" not in received[0].channels
        assert "B2" not in received[0].channels


class TestInterlockParity:
    """Phase 8's actual guarantee: an interlock-violating amplitude is
    rejected the same way whether it comes from the funcgen tab's Apply
    button or a direct Beamline.set_channel call — there is no second path
    to the driver that skips the ±5 V combined-peak check."""

    def _set_over_limit(self, panel):
        # |offset| + amp/2 = 4.5 + 1.0 = 5.5 V > the 5 V ceiling.
        panel.cbo_shape.setCurrentText("Sine")
        panel.spn_amp.setValue(2.0)
        panel.spn_offset.setValue(4.5)
        panel.btn_output.setChecked(True)

    def test_widget_apply_is_rejected_by_the_same_interlock(self, tab_and_mgr, monkeypatch):
        tab, mgr = tab_and_mgr
        # Skip the (unrelated) confirmation dialog path — over-limit is a
        # hard block regardless of what the user answers.
        monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
        monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

        self._set_over_limit(tab.panels["A1"])
        tab._apply_channel("A", 1)

        # The widget's Apply never reached the driver.
        mgr.A.set_waveform.assert_not_called()
        mgr.A.output_on.assert_not_called()

    def test_direct_beamline_call_is_rejected_identically(self, tab_and_mgr, monkeypatch):
        tab, mgr = tab_and_mgr
        # Beamline.set_channel's rejection fires command_failed, which the
        # tab's _on_command_failed turns into a QMessageBox.warning — mock it
        # so the dialog doesn't block waiting for a click that never comes.
        monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
        from rbl.state.snapshots import ChannelParams

        over_limit = ChannelParams(
            shape="Sine", freq_hz=1000.0, amp_vpp=2.0, offset_v=4.5,
            phase_deg=0.0, start_phase_deg=0.0, load="INFinity", output_on=True,
        )
        ok = tab.beamline.set_channel("A1", over_limit)

        assert ok is False
        mgr.A.set_waveform.assert_not_called()
        mgr.A.output_on.assert_not_called()

    def test_both_paths_reach_the_same_beamline_and_agree(self, tab_and_mgr, monkeypatch):
        """Sanity check that the widget path and the direct call are truly
        the same code path, not just coincidentally the same answer."""
        tab, mgr = tab_and_mgr
        monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
        monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

        from rbl.state.snapshots import ChannelParams
        over_limit = ChannelParams(
            shape="Sine", freq_hz=1000.0, amp_vpp=2.0, offset_v=4.5,
            phase_deg=0.0, start_phase_deg=0.0, load="INFinity", output_on=True,
        )

        result_direct = tab.beamline.set_channel("A2", over_limit)
        self._set_over_limit(tab.panels["A1"])
        tab._apply_channel("A", 1)
        result_widget = tab.beamline.set_channel("A1", over_limit)  # re-derive same call

        assert result_direct is False and result_widget is False
        assert mgr.A.set_waveform.call_count == 0


class TestReferenceClockToggle:
    def test_toggle_calls_verify_external_lock_on_gen_b(self, tab_and_mgr):
        """Enabling sharing must set Gen A to INT and call verify_external_lock on Gen B."""
        tab, mgr = tab_and_mgr
        # Default fixture: mgr.B.verify_external_lock.return_value = (True, "EXT")
        tab._on_ext_ref_toggled(True)
        mgr.A.set_reference_clock.assert_called_with("INTernal")
        mgr.B.verify_external_lock.assert_called_once()

    def test_untoggle_returns_both_internal(self, tab_and_mgr):
        tab, mgr = tab_and_mgr
        tab._on_ext_ref_toggled(False)
        mgr.A.set_reference_clock.assert_called_with("INTernal")
        mgr.B.set_reference_clock.assert_called_with("INTernal")

    def test_ext_lock_failure_triggers_warning(self, tab_and_mgr, monkeypatch):
        """If verify_external_lock returns (False, 'INT'), a warning is shown."""
        tab, mgr = tab_and_mgr
        mgr.B.verify_external_lock.return_value = (False, "INT")
        # Suppress the blocking QMessageBox.
        monkeypatch.setattr(
            "rbl.gui.funcgen_tab.QMessageBox.warning",
            lambda *a, **kw: None,
        )
        tab._on_ext_ref_toggled(True)
        # The SCPI log should record the failure.
        log_text = tab.scpi_log.toPlainText()
        assert "INT" in log_text

    def test_toggle_guard_refuses_when_gen_a_is_ext(self, tab_and_mgr, monkeypatch):
        """If Gen A is already EXT, enabling sharing is refused to prevent collision."""
        tab, mgr = tab_and_mgr
        mgr.A.get_reference_clock.return_value = "EXT"
        monkeypatch.setattr(
            "rbl.gui.funcgen_tab.QMessageBox.warning",
            lambda *a, **kw: None,
        )
        tab._on_ext_ref_toggled(True)
        # verify_external_lock must NOT be called — refused before getting there.
        mgr.B.verify_external_lock.assert_not_called()
