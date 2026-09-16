"""
test_cup_session_writer.py
Tests for Faraday cup session logging (CupSessionWriter) and integration with FaradayCupTab.

WHY THIS EXISTS
---------------
ADR 0002 and Ticket 08:
- Every sample inside an acquisition run is written to a session CSV.
- Idle periods are recorded as periodic heartbeat markers, not sample rows.
- Run transitions, connection, and disconnection are recorded as markers.
- An idle gap and a disconnected gap are unambiguous and distinguishable.
- Over-range readings are logged and flagged, never dropped.
- File is flushed after every write for crash-resilience.
"""
import csv
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from rbl.config.cup_config import (
    CUP_ARM_THRESHOLD_A,
    CUP_RELEASE_THRESHOLD_A,
)
from rbl.gui.faraday_cup_tab import FaradayCupTab
from rbl.hardware.cup_status import CupPosition
from rbl.services.cup_session_writer import CSV_COLUMNS, CupSessionWriter
from rbl.snapshots import CupActuationState
from rbl.state.beamline import Beamline
from tests.payloads import CupFeed


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


class TestCupSessionWriterDirect:
    """Direct unit tests for CupSessionWriter service without GUI or Qt."""

    def test_creates_session_files_in_output_dir(self, tmp_path):
        writer = CupSessionWriter(session_id="test_session_001", output_dir=tmp_path)
        csv_file = tmp_path / "test_session_001.csv"
        meta_file = tmp_path / "test_session_001.json"

        assert csv_file.exists()
        assert not meta_file.exists()  # JSON sidecar written at close()

        # Write sample and close
        writer.write_sample(
            t_host=100.0,
            t_inst=1.5,
            current=1.23e-6,
            status_word=0,
            over_range=False,
            run_id=1,
        )
        writer.close()

        assert meta_file.exists()
        with open(meta_file, encoding="utf-8") as f:
            meta = json.load(f)
        assert meta["session_id"] == "test_session_001"
        assert meta["total_samples"] == 1
        assert meta["total_runs"] == 1

    def test_header_comments_and_columns(self, tmp_path):
        writer = CupSessionWriter(output_dir=tmp_path)
        writer.close()

        with open(writer.csv_path, encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]

        # Check '#' comment lines
        comment_lines = [line for line in lines if line.startswith("#")]
        assert len(comment_lines) >= 2
        assert any("cup_session_writer" in line for line in comment_lines)
        assert any("thresholds:" in line for line in comment_lines)

        # Check column header line
        non_comment_lines = [line for line in lines if not line.startswith("#")]
        header_row = non_comment_lines[0].split(",")
        assert header_row == CSV_COLUMNS

    def test_flush_after_every_row(self, tmp_path):
        writer = CupSessionWriter(output_dir=tmp_path)
        writer.write_sample(
            t_host=1.0,
            t_inst=0.1,
            current=2.5e-6,
            status_word=0,
            over_range=False,
            run_id=1,
        )

        # Read directly from disk without closing writer
        with open(writer.csv_path, encoding="utf-8") as f:
            content = f.read()
        assert "2.50000000e-06" in content
        assert "sample" in content

        writer.close()

    def test_over_range_samples_logged_and_flagged(self, tmp_path):
        writer = CupSessionWriter(output_dir=tmp_path)
        writer.write_run_opened(
            t_host=10.0,
            run_id=1,
            arm_threshold=0.5e-6,
            release_threshold=0.25e-6,
        )
        writer.write_sample(
            t_host=11.0,
            t_inst=1.0,
            current=None,
            status_word=0x00000040,
            over_range=True,
            run_id=1,
            details="over_range",
        )
        writer.close()

        with open(writer.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader([line for line in f if not line.startswith("#")])
            rows = list(reader)

        samples = [r for r in rows if r["record_type"] == "sample"]
        assert len(samples) == 1
        assert samples[0]["over_range"] == "True"
        assert samples[0]["status_word"] == "0x00000040"
        assert samples[0]["run_id"] == "1"

    def test_active_run_stats_calculation(self, tmp_path):
        """active_run_stats computes duration, sample counts, and running average."""
        writer = CupSessionWriter(output_dir=tmp_path)
        assert writer.active_run_stats.run_id is None
        assert writer.active_run_stats.total_samples == 0

        writer.write_run_opened(
            t_host=10.0,
            run_id=1,
            arm_threshold=0.5e-6,
            release_threshold=0.25e-6,
        )
        stats0 = writer.active_run_stats
        assert stats0.run_id == 1
        assert stats0.total_samples == 0
        assert stats0.valid_samples == 0
        assert stats0.over_range_samples == 0
        assert stats0.average_current_a is None

        # Write sample 1: 1.0 µA
        writer.write_sample(
            t_host=11.0,
            t_inst=1.0,
            current=1.0e-6,
            status_word=0,
            over_range=False,
            run_id=1,
        )
        stats1 = writer.active_run_stats
        assert stats1.total_samples == 1
        assert stats1.valid_samples == 1
        assert stats1.over_range_samples == 0
        assert stats1.duration_s == pytest.approx(1.0)
        assert stats1.average_current_a == pytest.approx(1.0e-6)

        # Write sample 2: 3.0 µA
        writer.write_sample(
            t_host=13.0,
            t_inst=3.0,
            current=3.0e-6,
            status_word=0,
            over_range=False,
            run_id=1,
        )
        stats2 = writer.active_run_stats
        assert stats2.total_samples == 2
        assert stats2.valid_samples == 2
        assert stats2.over_range_samples == 0
        assert stats2.duration_s == pytest.approx(3.0)
        assert stats2.average_current_a == pytest.approx(2.0e-6)

        # Write sample 3: over-range
        writer.write_sample(
            t_host=14.0,
            t_inst=4.0,
            current=None,
            status_word=0x40,
            over_range=True,
            run_id=1,
        )
        stats3 = writer.active_run_stats
        assert stats3.total_samples == 3
        assert stats3.valid_samples == 2
        assert stats3.over_range_samples == 1
        assert stats3.duration_s == pytest.approx(4.0)
        # Average remains 2.0 µA because over-range is excluded
        assert stats3.average_current_a == pytest.approx(2.0e-6)

        # Close run
        writer.write_run_closed(t_host=15.0, run_id=1, reason="released")
        writer.close()

    def test_all_marker_types_written_correctly(self, tmp_path):
        writer = CupSessionWriter(output_dir=tmp_path)
        writer.write_connected(t_host=1.0, ident="Keithley 6482", resource="GPIB0::14::INSTR")
        writer.write_idle_heartbeat(t_host=5.0, details="watching")
        writer.write_run_opened(
            t_host=10.0,
            run_id=1,
            arm_threshold=0.5e-6,
            release_threshold=0.25e-6,
            forced=False,
        )
        writer.write_sample(
            t_host=11.0,
            t_inst=1.0,
            current=1.0e-6,
            status_word=0,
            over_range=False,
            run_id=1,
        )
        writer.write_run_closed(
            t_host=20.0,
            run_id=1,
            reason="released",
            sample_count=1,
            duration_s=10.0,
        )
        writer.write_disconnected(t_host=30.0, reason="user_unplugged")
        writer.close()

        with open(writer.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader([line for line in f if not line.startswith("#")])
            rows = list(reader)

        assert len(rows) == 6
        types = [r["record_type"] for r in rows]
        assert types == [
            "connected",
            "idle_heartbeat",
            "run_opened",
            "sample",
            "run_closed",
            "disconnected",
        ]

        # Check details in run_opened and run_closed
        assert "arm=5.000e-07" in rows[2]["details"]
        assert "release=2.500e-07" in rows[2]["details"]
        assert "reason=released" in rows[4]["details"]
        assert "reason=user_unplugged" in rows[5]["details"]

    def test_position_transition_written_and_flushed(self, tmp_path):
        writer = CupSessionWriter(output_dir=tmp_path)
        writer.write_position_transition(
            t_host=10.123456, position=CupPosition.IN, details="in_confirmed"
        )
        writer.write_position_transition(
            t_host=15.654321, position=CupPosition.OUT, details="out_confirmed"
        )
        writer.close()

        with open(writer.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader([line for line in f if not line.startswith("#")])
            rows = list(reader)

        transitions = [r for r in rows if r["record_type"] == "position_transition"]
        assert len(transitions) == 2
        assert transitions[0]["host_timestamp"] == "10.123456"
        assert transitions[0]["position"] == "IN"
        assert transitions[0]["details"] == "in_confirmed"
        assert transitions[1]["host_timestamp"] == "15.654321"
        assert transitions[1]["position"] == "OUT"
        assert transitions[1]["details"] == "out_confirmed"

    def test_all_four_fault_kinds_distinguishable_by_record_type(self, tmp_path):
        writer = CupSessionWriter(output_dir=tmp_path)
        writer.write_fault_move_not_confirmed(
            t_host=1.0, commanded=CupPosition.IN, timeout_s=2.0
        )
        writer.write_fault_controller_not_in_auto(
            t_host=2.0, details="controller in LOCAL mode"
        )
        writer.write_fault_impossible_status(
            t_host=3.0, raw_status=0x0C, details="both IN and OUT asserted"
        )
        writer.write_fault_disagreement(
            t_host=4.0, position=CupPosition.IN, current=0.0, details="position IN but current 0"
        )
        writer.close()

        with open(writer.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader([line for line in f if not line.startswith("#")])
            rows = list(reader)

        fault_types = [r["record_type"] for r in rows]
        # Each must be distinct and non-empty
        assert len(set(fault_types)) == 4
        assert fault_types == [
            "fault_move_not_confirmed",
            "fault_controller_not_in_auto",
            "fault_impossible_status",
            "fault_disagreement",
        ]

        # Check disagreement row carries confirmed position and measured current
        disag_row = next(r for r in rows if r["record_type"] == "fault_disagreement")
        assert disag_row["position"] == "IN"
        assert float(disag_row["current_a"]) == 0.0
        assert "0.00000000e+00" in disag_row["current_a"]

        # Check move fault names commanded move
        move_row = next(r for r in rows if r["record_type"] == "fault_move_not_confirmed")
        assert "IN" in move_row["position"] or "IN" in move_row["details"]

        # Check impossible status carries status_word
        imp_row = next(r for r in rows if r["record_type"] == "fault_impossible_status")
        assert imp_row["status_word"] == "0x0000000C" or "0x0000000C" in imp_row["details"]

    def test_transition_and_sample_same_clock_and_format(self, tmp_path):
        writer = CupSessionWriter(output_dir=tmp_path)
        t_trans = 100.123456
        t_samp = 100.223456
        writer.write_position_transition(t_host=t_trans, position=CupPosition.IN)
        writer.write_sample(
            t_host=t_samp,
            t_inst=0.1,
            current=1.0e-6,
            status_word=0,
            over_range=False,
            run_id=1,
        )

        # Read unclosed file directly (verifying immediate flush)
        with open(writer.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader([line for line in f if not line.startswith("#")])
            rows = list(reader)

        assert len(rows) == 2
        trans_row = rows[0]
        samp_row = rows[1]

        # Both formatted to exactly 6 decimal places (same clock format)
        assert trans_row["host_timestamp"] == "100.123456"
        assert samp_row["host_timestamp"] == "100.223456"
        delta = float(samp_row["host_timestamp"]) - float(trans_row["host_timestamp"])
        assert delta == pytest.approx(0.1, abs=1e-6)

        writer.close()

    def test_file_survives_simulated_mid_run_abort(self, tmp_path):
        writer = CupSessionWriter(output_dir=tmp_path)
        writer.write_connected(t_host=1.0, ident="Keithley 6482")
        writer.write_position_transition(t_host=2.0, position=CupPosition.IN)
        writer.write_run_opened(
            t_host=2.0, run_id=1, arm_threshold=1e-6, release_threshold=0.5e-6
        )
        writer.write_sample(
            t_host=3.0,
            t_inst=1.0,
            current=2.0e-6,
            status_word=0,
            over_range=False,
            run_id=1,
        )
        writer.write_fault_disagreement(
            t_host=4.0, position=CupPosition.OUT, current=2.0e-6
        )

        # DO NOT call writer.close() — simulate crash/abort!
        # The file on disk must be completely valid CSV with all 5 rows intact
        with open(writer.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader([line for line in f if not line.startswith("#")])
            rows = list(reader)

        assert len(rows) == 5
        types = [r["record_type"] for r in rows]
        assert types == [
            "connected",
            "position_transition",
            "run_opened",
            "sample",
            "fault_disagreement",
        ]

        # Clean up file descriptor via public close()
        writer.close()

    def test_csv_comment_header_describes_new_row_kinds(self, tmp_path):
        writer = CupSessionWriter(output_dir=tmp_path)
        writer.close()

        with open(writer.csv_path, encoding="utf-8") as f:
            comment_lines = [line.strip() for line in f if line.startswith("#")]

        header_text = "\n".join(comment_lines)
        assert "position_transition" in header_text
        assert "fault" in header_text.lower()


class TestFaradayCupTabSessionIntegration:
    """Integration tests driving FaradayCupTab through CupFeed and verifying session log."""

    def test_full_insertion_lifecycle_recorded(self, qapp, tmp_path):
        """Drive a full insertion through CupFeed and assert on the session CSV file."""
        beamline = Beamline()
        session_writer = CupSessionWriter(output_dir=tmp_path)
        tab = FaradayCupTab(beamline=beamline, session_writer=session_writer)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        # 1. Idle baseline at t=0.0 (0.0 A) -> triggers connected marker
        feed.send_reading(0.0, timestamp=0.0, t_host=0.0)
        qapp.processEvents()

        # 2. Insertion begins at t=1.0 with 1.0 µA
        feed.send_reading(1.0e-6, timestamp=1.0, t_host=1.0)
        qapp.processEvents()

        # 3. Debounce completed at t=2.0 -> Run 1 opens!
        feed.send_reading(1.0e-6, timestamp=2.0, t_host=2.0)
        qapp.processEvents()

        # 4. Samples during run
        feed.send_reading(1.2e-6, timestamp=3.0, t_host=3.0)
        feed.send_reading(1.1e-6, timestamp=4.0, t_host=4.0)
        qapp.processEvents()

        # 5. Cup withdrawn at t=10.0 (0.0 A)
        feed.send_reading(0.0, timestamp=10.0, t_host=10.0)
        qapp.processEvents()

        # 6. Release interval elapsed at t=13.0 -> Run 1 closes!
        feed.send_reading(0.0, timestamp=13.0, t_host=13.0)
        qapp.processEvents()

        tab.close()
        qapp.processEvents()

        # Inspect CSV rows
        with open(session_writer.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader([line for line in f if not line.startswith("#")])
            rows = list(reader)

        row_types = [r["record_type"] for r in rows]
        assert "connected" in row_types
        assert "run_opened" in row_types
        assert "sample" in row_types
        assert "run_closed" in row_types

        # Find run_opened marker
        run_open_row = next(r for r in rows if r["record_type"] == "run_opened")
        assert run_open_row["run_id"] == "1"
        assert f"arm={CUP_ARM_THRESHOLD_A:.3e}" in run_open_row["details"]
        assert f"release={CUP_RELEASE_THRESHOLD_A:.3e}" in run_open_row["details"]

        # Find sample rows
        sample_rows = [r for r in rows if r["record_type"] == "sample"]
        assert len(sample_rows) >= 3
        for s in sample_rows:
            assert s["run_id"] == "1"
            assert float(s["current_a"]) >= 0.0

        # Samples taken while beam was present (t=2.0, 3.0, 4.0) have positive current
        beam_samples = [s for s in sample_rows if float(s["host_timestamp"]) < 10.0]
        assert len(beam_samples) >= 3
        for s in beam_samples:
            assert float(s["current_a"]) > 0.5e-6

        # Find run_closed marker
        run_close_row = next(r for r in rows if r["record_type"] == "run_closed")
        assert run_close_row["run_id"] == "1"
        assert "reason=released" in run_close_row["details"]

    def test_idle_gap_vs_disconnected_gap_are_distinguishable(self, qapp, tmp_path):
        """Verify that an idle gap (with heartbeats) is distinguishable from a disconnected gap."""
        beamline = Beamline()
        session_writer = CupSessionWriter(output_dir=tmp_path)
        tab = FaradayCupTab(beamline=beamline, session_writer=session_writer)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        # Connect and run Run 1
        feed.send_reading(0.0, timestamp=0.0, t_host=0.0)
        feed.send_reading(1.0e-6, timestamp=1.0, t_host=1.0)
        feed.send_reading(1.0e-6, timestamp=2.0, t_host=2.0)  # Run 1 open
        feed.send_reading(0.0, timestamp=5.0, t_host=5.0)
        feed.send_reading(0.0, timestamp=8.0, t_host=8.0)  # Run 1 close
        qapp.processEvents()

        # --- IDLE GAP: Polling continues at baseline, heartbeat triggers at >= 10s interval ---
        # t=8.0 closed, at t=18.0 (> 10s from last heartbeat/connect at 0.0), heartbeat is emitted
        feed.send_reading(0.0, timestamp=18.5, t_host=18.5)
        feed.send_reading(0.0, timestamp=29.0, t_host=29.0)
        qapp.processEvents()

        # Run 2
        feed.send_reading(1.0e-6, timestamp=30.0, t_host=30.0)
        feed.send_reading(1.0e-6, timestamp=31.0, t_host=31.0)  # Run 2 open
        feed.send_reading(0.0, timestamp=35.0, t_host=35.0)
        feed.send_reading(0.0, timestamp=38.0, t_host=38.0)  # Run 2 close
        qapp.processEvents()

        # --- DISCONNECTED GAP: Picoammeter disconnects at t=40.0 ---
        beamline.disconnect_picoammeter()
        qapp.processEvents()

        # Reconnect at t=60.0 and start Run 3
        feed.send_reading(1.0e-6, timestamp=60.0, t_host=60.0)
        feed.send_reading(1.0e-6, timestamp=61.0, t_host=61.0)  # Run 3 open
        feed.send_reading(0.0, timestamp=65.0, t_host=65.0)
        feed.send_reading(0.0, timestamp=68.0, t_host=68.0)  # Run 3 close
        qapp.processEvents()

        tab.close()
        qapp.processEvents()

        with open(session_writer.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader([line for line in f if not line.startswith("#")])
            rows = list(reader)

        row_types = [r["record_type"] for r in rows]

        # 1. Idle gap contains idle_heartbeat markers
        assert "idle_heartbeat" in row_types

        # 2. Disconnected gap contains disconnected followed by connected
        assert "disconnected" in row_types
        disc_idx = row_types.index("disconnected")
        reconn_idx = disc_idx + 1 + row_types[disc_idx + 1:].index("connected")
        assert reconn_idx > disc_idx

        # Between disc_idx and reconn_idx there are NO sample rows or idle heartbeats
        gap_types = row_types[disc_idx + 1:reconn_idx]
        assert "sample" not in gap_types
        assert "idle_heartbeat" not in gap_types

    def test_force_start_and_stop_markers_recorded(self, qapp, tmp_path):
        """Force start and stop buttons write run_opened (forced) and run_closed markers."""
        beamline = Beamline()
        session_writer = CupSessionWriter(output_dir=tmp_path)
        tab = FaradayCupTab(beamline=beamline, session_writer=session_writer)
        feed = CupFeed(tab, beamline=beamline)
        tab.show()
        qapp.processEvents()

        feed.send_reading(0.0, timestamp=0.0, t_host=0.0)
        qapp.processEvents()

        # Force start
        tab.btn_force_start.click()
        qapp.processEvents()

        # Send sample
        feed.send_reading(0.05e-6, timestamp=1.0, t_host=1.0)
        qapp.processEvents()

        # Force stop
        tab.btn_force_stop.click()
        qapp.processEvents()

        tab.close()
        qapp.processEvents()

        with open(session_writer.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader([line for line in f if not line.startswith("#")])
            rows = list(reader)

        opened = [r for r in rows if r["record_type"] == "run_opened"]
        closed = [r for r in rows if r["record_type"] == "run_closed"]
        samples = [r for r in rows if r["record_type"] == "sample"]

        assert len(opened) == 1
        assert "forced=True" in opened[0]["details"]
        assert len(samples) == 1
        assert len(closed) == 1
        assert "reason=forced_stop" in closed[0]["details"]

    def test_tab_logs_position_transitions_and_faults(self, qapp, tmp_path):
        """Verify FaradayCupTab writes position transitions and faults via CupActuationState."""
        beamline = Beamline()
        session_writer = CupSessionWriter(output_dir=tmp_path)
        tab = FaradayCupTab(beamline=beamline, session_writer=session_writer)
        tab.show()
        qapp.processEvents()

        # Connect actuation with confirmed IN
        tab.on_cup_actuation_state(
            CupActuationState(
                connected=True,
                commanded=CupPosition.IN,
                confirmed=CupPosition.IN,
                auto_mode=True,
                stale=False,
                last_transition_t=10.0,
                t=10.0,
            )
        )
        qapp.processEvents()

        # Transition to OUT
        tab.on_cup_actuation_state(
            CupActuationState(
                connected=True,
                commanded=CupPosition.OUT,
                confirmed=CupPosition.OUT,
                auto_mode=True,
                stale=False,
                last_transition_t=20.0,
                t=20.0,
            )
        )
        qapp.processEvents()

        # Indeterminate fault
        tab.on_cup_actuation_state(
            CupActuationState(
                connected=True,
                commanded=CupPosition.OUT,
                confirmed=CupPosition.INDETERMINATE,
                auto_mode=True,
                stale=False,
                last_transition_t=25.0,
                t=25.0,
            )
        )
        qapp.processEvents()

        tab.close()
        qapp.processEvents()

        with open(session_writer.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader([line for line in f if not line.startswith("#")])
            rows = list(reader)

        row_types = [r["record_type"] for r in rows]
        assert "position_transition" in row_types
        assert "fault_impossible_status" in row_types

        # Check that transition rows carry position and timestamps
        transitions = [r for r in rows if r["record_type"] == "position_transition"]
        assert any(
            t["position"] == "IN" and float(t["host_timestamp"]) == 10.0
            for t in transitions
        )
        assert any(
            t["position"] == "OUT" and float(t["host_timestamp"]) == 20.0
            for t in transitions
        )


class TestWriteCycleInsertionSkipped:
    """write_cycle_insertion_skipped records a skip marker in the session file."""

    def test_skip_row_written(self, tmp_path):
        from rbl.services.cup_session_writer import CupSessionWriter

        sw = CupSessionWriter(session_id="test_skip", output_dir=tmp_path)
        sw.write_cycle_insertion_skipped(t_host=300.0, insertion_due_t=300.0)
        sw.close()

        with open(sw.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader([line for line in f if not line.startswith("#")])
            rows = list(reader)

        skip_rows = [r for r in rows if r["record_type"] == "cycle_insertion_skipped"]
        assert len(skip_rows) == 1
        assert float(skip_rows[0]["host_timestamp"]) == pytest.approx(300.0)
        assert "300.000" in skip_rows[0]["details"]
        assert "manual run was open" in skip_rows[0]["details"]

    def test_skip_row_with_details(self, tmp_path):
        from rbl.services.cup_session_writer import CupSessionWriter

        sw = CupSessionWriter(session_id="test_skip2", output_dir=tmp_path)
        sw.write_cycle_insertion_skipped(
            t_host=600.0, insertion_due_t=600.0, details="extra context"
        )
        sw.close()

        with open(sw.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader([line for line in f if not line.startswith("#")])
            rows = list(reader)

        skip_rows = [r for r in rows if r["record_type"] == "cycle_insertion_skipped"]
        assert "extra context" in skip_rows[0]["details"]

    def test_skip_does_not_affect_run_stats(self, tmp_path):
        """A skip marker must not increment sample count or affect active-run stats."""
        from rbl.services.cup_session_writer import CupSessionWriter

        sw = CupSessionWriter(session_id="test_skip3", output_dir=tmp_path)
        sw.write_run_opened(t_host=0.0, run_id=1, arm_threshold=0.5e-6, release_threshold=0.25e-6)
        sw.write_cycle_insertion_skipped(t_host=300.0, insertion_due_t=300.0)
        assert sw.sample_count == 0
        sw.close()

