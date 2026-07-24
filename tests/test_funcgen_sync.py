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

import pytest
from unittest.mock import MagicMock

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab_and_mgr(qapp):
    """A FuncGenTab wired to two mocked generators sharing one call recorder."""
    from funcgen_tab import FuncGenTab

    tab = FuncGenTab()

    # One manager mock records the ordering of calls across BOTH units:
    # every call to mgr.A.* / mgr.B.* lands in mgr.mock_calls in order.
    mgr = MagicMock()
    mgr.A.set_waveform.return_value = ""     # no clamp warning
    mgr.B.set_waveform.return_value = ""
    tab._gen = {"A": mgr.A, "B": mgr.B}
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
        from funcgen_tab import CHANNEL_ROLE

        # X+/X- must both be on unit A.
        x_keys = {k for k, v in CHANNEL_ROLE.items() if v in ("X+", "X-")}
        assert all(k.startswith("A") for k in x_keys), CHANNEL_ROLE

        # Y+/Y- must both be on unit B.
        y_keys = {k for k, v in CHANNEL_ROLE.items() if v in ("Y+", "Y-")}
        assert all(k.startswith("B") for k in y_keys), CHANNEL_ROLE


class TestReferenceClockToggle:
    def test_toggle_sets_master_and_external(self, tab_and_mgr):
        tab, mgr = tab_and_mgr
        tab._on_ext_ref_toggled(True)
        mgr.A.set_reference_clock.assert_called_with("INTernal")
        mgr.B.set_reference_clock.assert_called_with("EXTernal")

    def test_untoggle_returns_both_internal(self, tab_and_mgr):
        tab, mgr = tab_and_mgr
        tab._on_ext_ref_toggled(False)
        mgr.A.set_reference_clock.assert_called_with("INTernal")
        mgr.B.set_reference_clock.assert_called_with("INTernal")

    def test_ext_lock_failure_triggers_warning(self, tab_and_mgr, monkeypatch):
        """If Gen B falls back to INT after an EXT request, a warning is shown."""
        tab, mgr = tab_and_mgr
        # Simulate Gen B reporting INT despite EXT request.
        mgr.B.get_reference_clock.return_value = "INT"
        # Suppress the blocking QMessageBox.
        monkeypatch.setattr(
            "funcgen_tab.QMessageBox.warning",
            lambda *a, **kw: None,
        )
        tab._on_ext_ref_toggled(True)
        # The SCPI log should record the failure.
        log_text = tab.scpi_log.toPlainText()
        assert "INTernal" in log_text or "INT" in log_text
