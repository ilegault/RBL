"""
tests/test_e2e_session.py
End-to-end session test through the full MainWindow.

Builds the real MainWindow with no hardware attached, drives a realistic
operational session (motor polls, LabJack stream windows, FuncGen readbacks,
vacuum readings, scope waveforms) through the real Beamline and worker
pipelines, and asserts on what an operator would see on screen across key tabs:
  * Overview
  * Beam Current
  * HV Amplifiers
  * Stepper Motors
  * Vacuum
  * Beam Profiler
"""
import os
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from rbl.config import hardware_config as SC
from rbl.gui.app import MainWindow
from rbl.hardware.vgc083_driver import VgcReading
from rbl.hardware.xgs600_driver import XgsChannel, XgsReading
from rbl.snapshots import (
    ChannelSnapshot,
    FuncGenState,
    ScopeState,
    VacuumState,
)
from tests.payloads import window_payload


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """Stop QMessageBox.* from blocking the test on modal dialogs."""
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(
            QMessageBox,
            name,
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok),
        )


def test_end_to_end_session_through_main_window(qapp):
    """Drive a realistic run through MainWindow with no hardware attached and
    assert on rendered, operator-visible text across all key screens."""
    win = MainWindow()
    win.resize(1440, 920)
    win.show()
    qapp.processEvents()

    try:
        # ── 1. Stepper Motors Poll ───────────────────────────────────────────
        # Mark axes zeroed and drive 4-axis motor poll (A=X+, B=X-, C=Y+, D=Y-)
        for p in win.motor_tab.axes.values():
            p.zeroed = True
        pos_a = SC.mm_to_counts("A", 2.5)
        pos_b = SC.mm_to_counts("B", 1.5)
        pos_c = SC.mm_to_counts("C", 3.0)
        pos_d = SC.mm_to_counts("D", 2.0)
        no_switches = {
            "forward_switch": False,
            "reverse_switch": False,
            "home_switch": False,
        }
        poll_snapshot = {
            "A": {
                "pos": pos_a,
                "moving": False,
                "switches": no_switches,
                "enabled": True,
            },
            "B": {
                "pos": pos_b,
                "moving": False,
                "switches": no_switches,
                "enabled": True,
            },
            "C": {
                "pos": pos_c,
                "moving": False,
                "switches": no_switches,
                "enabled": True,
            },
            "D": {
                "pos": pos_d,
                "moving": False,
                "switches": no_switches,
                "enabled": True,
            },
        }
        win.motor_tab._on_state(poll_snapshot)

        # ── 2. Function Generator Readbacks ──────────────────────────────────
        # Push differential drive readbacks for X (A1/A2 at 250 Hz) and Y (B1/B2 at 100 Hz)
        funcgen_state = FuncGenState(
            connected={"A": True, "B": True},
            timebase={"A": "INT", "B": "EXT"},
            channels={
                "A1": ChannelSnapshot(
                    shape="SIN", freq_hz=250.0, amp_vpp=6.0, offset_v=0.0,
                    phase_deg=0.0, output_on=True,
                ),
                "A2": ChannelSnapshot(
                    shape="SIN", freq_hz=250.0, amp_vpp=6.0, offset_v=0.0,
                    phase_deg=180.0, output_on=True,
                ),
                "B1": ChannelSnapshot(
                    shape="SIN", freq_hz=100.0, amp_vpp=4.0, offset_v=0.0,
                    phase_deg=0.0, output_on=True,
                ),
                "B2": ChannelSnapshot(
                    shape="SIN", freq_hz=100.0, amp_vpp=4.0, offset_v=0.0,
                    phase_deg=180.0, output_on=True,
                ),
            },
        )
        win.beamline.funcgens_changed.emit(funcgen_state)

        # ── 3. Vacuum Readings & Interlock Designation ───────────────────────
        # Operator designates Chamber as the active interlock gauge
        win.beamline.set_interlock_gauges({"xgs600:Chamber"})

        now = time.time()
        xgs_state = VacuumState(
            timestamp=now,
            xgs_readings=[
                XgsReading(
                    channel=XgsChannel(
                        index=0, slot=0, board="CNV", label="Chamber", sensor_code="CNV1"
                    ),
                    pressure=1.2e-6,
                    raw="1.2E-06",
                    state="OK",
                )
            ],
            xgs_connected=True,
            units_xgs="Torr",
        )
        vgc_state = VacuumState(
            timestamp=now,
            vgc_readings=[
                VgcReading(
                    channel="Beamline",
                    pressure=3.4e-7,
                    raw="3.4E-07",
                    state="OK",
                )
            ],
            vgc_connected=True,
            units_vgc="Torr",
        )
        win.beamline._on_xgs_readings(xgs_state)
        win.beamline._on_vgc_readings(vgc_state)

        # ── 4. LabJack Stream Window ─────────────────────────────────────────
        # 3.0 V on log amps -> 1 µA; HV monitors: X+ at 3 kV / 10 mA (250 Hz anti-phase)
        fs = 50_000.0
        sample_period = 1.0 / fs
        n_samples = 5000
        t_arr = np.arange(n_samples) / fs

        volts = {
            "AIN0": 3.0, "AIN1": 3.0, "AIN2": 3.0, "AIN3": 3.0,
            "AIN12": 1.0, "AIN13": 3.0,  # X+: 10 mA, 3 kV
            "AIN10": 1.0, "AIN11": 3.0,  # X-: 10 mA, 3 kV
            "AIN8":  0.5, "AIN9":  2.0,  # Y+: 5 mA,  2 kV
            "AIN6":  0.5, "AIN7":  2.0,  # Y-: 5 mA,  2 kV
        }
        waveforms = {
            "AIN13": 3.0 * np.sin(2 * np.pi * 250.0 * t_arr),
            "AIN11": -3.0 * np.sin(2 * np.pi * 250.0 * t_arr),
            "AIN9":  2.0 * np.sin(2 * np.pi * 100.0 * t_arr),
            "AIN7":  -2.0 * np.sin(2 * np.pi * 100.0 * t_arr),
        }
        payload = window_payload(
            volts=volts,
            waveforms=waveforms,
            t=1.0,
            samples=n_samples,
            sample_period=sample_period,
        )
        win.beamline.ingest_labjack_window(payload)

        # ── 5. Scope / Profiler Waveform ─────────────────────────────────────
        scope_state = ScopeState(
            timestamp=now,
            connected=True,
            mean_fwhm_seconds=2.5e-3,
            fwhm_x_seconds=2.4e-3,
            fwhm_y_seconds=2.6e-3,
            xy_ratio=2.4 / 2.6,
            fwhm_source="fit",
            fit_r_squared=0.99,
            corrected_downsampled=list(np.sin(np.linspace(0, np.pi, 100))),
            xincr_downsampled=1e-5,
            peaks=[
                {"axis": "X", "fwhm_seconds": 2.4e-3, "resolved": True},
                {"axis": "Y", "fwhm_seconds": 2.6e-3, "resolved": True},
            ],
            n_peaks=2,
            n_resolved=2,
        )
        win.beamline._on_scope_waveform(scope_state)

        # ── 6. Trigger Redraws and Event Loop ────────────────────────────────
        qapp.processEvents()
        win.overview_tab.redraw()
        win.vacuum_tab._redraw()
        win.profiler_tab._redraw_readout()
        qapp.processEvents()

        # ── 7. Operator-Visible Assertions Across Screens ────────────────────

        # A. Overview Tab
        # Slit positions and gap
        assert win.overview_tab.slits["X+"].bar.lbl_value.text() == "2.500 mm"
        assert win.overview_tab.slits["X-"].bar.lbl_value.text() == "1.500 mm"
        assert "4.000 mm" in win.overview_tab.lbl_gaps.text()
        # Log-amp currents
        assert "µA" in win.overview_tab.currents["X+"].lbl_value.text()
        # HV Plate peak kV
        assert "3.00 kV" in win.overview_tab.hv_bars["X+"].lbl_value.text()
        # HV phase lock and frequency caption
        assert "locked" in win.overview_tab.hv_phase["X"].text().lower()
        assert "250 Hz" in win.overview_tab.hv_window["X"].text()
        # FuncGen readback
        assert "out=ON" in win.overview_tab.drives["X"].lbl_readback.text()
        # HV Interlock status
        assert "OK" in win.overview_tab.lbl_hv_interlock.text()

        # B. Beam Current Tab
        assert "3.00" in win.current_tab.lbl_v["AIN0"].text()
        assert "µA" in win.current_tab.lbl_i["AIN0"].text()

        # C. HV Amplifiers Tab
        assert "3.00" in win.amp_tab.lbl_meas["X+"].text()
        assert "6.00" in win.amp_tab.lbl_pp["X+"].text()
        assert "3.00" in win.amp_tab.lbl_cmd["X+"].text()

        # D. Stepper Motors Tab
        assert f"{pos_a:,} cts" in win.motor_tab.axes["A"].lbl_pos.text()
        assert f"{SC.counts_to_mm('A', pos_a):+.4f} mm" in win.motor_tab.axes["A"].lbl_pos.text()
        assert "Idle" in win.motor_tab.axes["A"].lbl_status.text()

        # E. Vacuum Tab
        assert win.vacuum_tab._table.rowCount() >= 2
        vacuum_texts = [
            win.vacuum_tab._table.item(row, col).text()
            for row in range(win.vacuum_tab._table.rowCount())
            for col in range(win.vacuum_tab._table.columnCount())
            if win.vacuum_tab._table.item(row, col) is not None
        ]
        assert any("1.200e-06" in text for text in vacuum_texts)
        assert any("3.400e-07" in text for text in vacuum_texts)
        assert any("XGS-600" in text for text in vacuum_texts)
        assert any("VGC083" in text for text in vacuum_texts)

        # F. Beam Profiler Tab
        assert win.profiler_tab._lbl_axis_value["X"].text() != "—"
        assert win.profiler_tab._lbl_axis_value["Y"].text() != "—"
        assert "2.4" in win.profiler_tab._lbl_axis_value["X"].text()
        assert "2.6" in win.profiler_tab._lbl_axis_value["Y"].text()

    finally:
        win.close()
        qapp.processEvents()
