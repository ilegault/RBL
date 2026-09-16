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


class TestInsertionSummaryRowsAndSessionHeader:
    """Ticket 11: One summary row per insertion, and a header that makes dpa traceable."""

    def test_header_comments_carry_all_parameters_and_provenance(self, tmp_path):
        """The session header carries species, energy, charge state, area, k,

        k's depth, SRIM version, entry date, cycle period and dwell.
        """
        writer = CupSessionWriter(
            session_id="test_header_full",
            output_dir=tmp_path,
            species="Fe56",
            energy="5.0 MeV",
            charge_state=3,
            area_cm2=0.5,
            k=1.0e-15,
            k_depth="100.0 nm",
            srim_version="SRIM-2013.00",
            entry_date="2026-09-16",
            cycle_period_s=300.0,
            cycle_dwell_s=3.0,
        )
        writer.close()

        with open(writer.csv_path, encoding="utf-8") as f:
            header_lines = [line.strip() for line in f if line.startswith("#")]

        session_line = next(line for line in header_lines if "session_header:" in line)
        assert "species=Fe56" in session_line
        assert "energy=5.0 MeV" in session_line
        assert "charge_state=3" in session_line
        assert "area=0.5000" in session_line
        assert "k=1.0000e-15" in session_line
        assert "k_depth=100.0 nm" in session_line
        assert "srim_version=SRIM-2013.00" in session_line
        assert "entry_date=2026-09-16" in session_line
        assert "cycle_period=300.0" in session_line
        assert "cycle_dwell=3.0" in session_line

    def test_absent_provenance_written_as_explicit_marker(self, tmp_path):
        """Absent provenance is written as an explicit marker, never as a blank or a zero."""
        writer = CupSessionWriter(
            session_id="test_header_absent",
            output_dir=tmp_path,
            species=None,
            energy=None,
            charge_state=None,
            area_cm2=None,
            k=None,
            k_depth=None,
            srim_version="",
            entry_date="   ",
            cycle_period_s=None,
            cycle_dwell_s=None,
        )
        writer.close()

        with open(writer.csv_path, encoding="utf-8") as f:
            header_lines = [line.strip() for line in f if line.startswith("#")]

        session_line = next(line for line in header_lines if "session_header:" in line)
        assert "k_depth=NOT_SPECIFIED" in session_line
        assert "srim_version=NOT_SPECIFIED" in session_line
        assert "entry_date=NOT_SPECIFIED" in session_line
        # Ensure no blanks or zeroes for absent provenance
        assert "k_depth=0" not in session_line
        assert "k_depth= " not in session_line
        assert "srim_version= " not in session_line
        assert "entry_date= " not in session_line

    def test_summary_row_flushed_immediately(self, tmp_path):
        """Every row flushes immediately, including the insertion summary row."""
        writer = CupSessionWriter(
            session_id="test_summary_flush",
            output_dir=tmp_path,
            charge_state=3,
            area_cm2=0.5,
            k=1.0e-15,
        )
        writer.write_insertion_summary(
            run_id=1,
            commanded_timestamp=10.0,
            confirmed_timestamp=10.25,
            dwell=3.0,
            sample_count=20,
            mean_current_a=1.0e-9,
            std_current_a=5.0e-11,
            beam_on_seconds=0.0,
            charge=0.0,
        )
        # Read from disk WITHOUT calling close()
        with open(writer.csv_path, encoding="utf-8") as f:
            lines = [line.strip() for line in f if not line.startswith("#")]
        reader = csv.DictReader(lines)
        rows = list(reader)
        assert len(rows) == 1
        r = rows[0]
        assert r["record_type"] == "insertion_summary"
        assert r["run_id"] == "1"
        assert float(r["commanded_timestamp"]) == pytest.approx(10.0)
        assert float(r["confirmed_timestamp"]) == pytest.approx(10.25)
        assert float(r["dwell"]) == pytest.approx(3.0)
        assert r["sample_count"] == "20"
        assert float(r["mean_current_a"]) == pytest.approx(1.0e-9)
        assert float(r["std_current_a"]) == pytest.approx(5.0e-11)
        assert float(r["beam_on_seconds"]) == pytest.approx(0.0)
        writer.close()

    def test_three_insertion_sequence_matches_worked_example(self, tmp_path):
        """A test builds a three-insertion sequence at known currents and intervals

        and asserts the running Q, fluence and dpa columns match values computed by hand,
        using ticket 08's worked example as one of them:
        I = 1.0e-9 A held for 300.0 s gives Q = 3.0e-7 C; patch 5.0 mm x 10.0 mm gives
        A = 0.5 cm^2; q = 3 and e = 1.602176634e-19 C give phi = 1.248302e12 ions/cm^2;
        k = 1.0e-15 gives dpa = 1.248302e-3
        """
        from rbl.hardware.dose_model import DoseAccumulator

        writer = CupSessionWriter(
            session_id="test_worked_chain",
            output_dir=tmp_path,
            species="Fe56",
            energy="5.0 MeV",
            charge_state=3,
            area_cm2=0.5,
            k=1.0e-15,
            k_depth="100 nm",
            srim_version="SRIM-2013",
            entry_date="2026-09-16",
            cycle_period_s=300.0,
            cycle_dwell_s=3.0,
        )
        acc = DoseAccumulator()

        # Insertion 1: t_in=10.0, t_out=13.0, measured I = 1.0e-9 A
        t_in_1 = 10.0
        t_out_1 = 13.0
        I_1 = 1.0e-9
        acc.record_insertion(t_in=t_in_1, t_out=t_out_1, mean_current_a=I_1)
        writer.write_insertion_summary(
            run_id=1,
            commanded_timestamp=9.8,
            confirmed_timestamp=t_in_1,
            dwell=t_out_1 - t_in_1,
            sample_count=30,
            mean_current_a=I_1,
            std_current_a=1.0e-11,
            beam_on_seconds=acc.last_beam_on_s,
            charge=acc.total_charge_c,
            fluence=acc.fluence(3, 0.5),
            dpa=acc.dpa(3, 0.5, 1.0e-15),
        )

        # Insertion 2: after 300.0 s beam-on (t_in = 13.0 + 300.0 = 313.0, t_out = 316.0)
        # Measured I = 2.0e-9 A
        t_in_2 = 313.0
        t_out_2 = 316.0
        I_2 = 2.0e-9
        acc.record_insertion(t_in=t_in_2, t_out=t_out_2, mean_current_a=I_2)
        writer.write_insertion_summary(
            run_id=2,
            commanded_timestamp=312.8,
            confirmed_timestamp=t_in_2,
            dwell=t_out_2 - t_in_2,
            sample_count=30,
            mean_current_a=I_2,
            std_current_a=2.0e-11,
            beam_on_seconds=acc.last_beam_on_s,
            charge=acc.total_charge_c,
            fluence=acc.fluence(3, 0.5),
            dpa=acc.dpa(3, 0.5, 1.0e-15),
        )

        # Insertion 3: after 600.0 s beam-on (t_in = 316.0 + 600.0 = 916.0, t_out = 919.0)
        # Measured I = 1.5e-9 A
        t_in_3 = 916.0
        t_out_3 = 919.0
        I_3 = 1.5e-9
        acc.record_insertion(t_in=t_in_3, t_out=t_out_3, mean_current_a=I_3)
        writer.write_insertion_summary(
            run_id=3,
            commanded_timestamp=915.8,
            confirmed_timestamp=t_in_3,
            dwell=t_out_3 - t_in_3,
            sample_count=30,
            mean_current_a=I_3,
            std_current_a=1.5e-11,
            beam_on_seconds=acc.last_beam_on_s,
            charge=acc.total_charge_c,
            fluence=acc.fluence(3, 0.5),
            dpa=acc.dpa(3, 0.5, 1.0e-15),
        )
        writer.close()

        # Read CSV back and verify hand-calculated values
        with open(writer.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader([line for line in f if not line.startswith("#")])
            rows = [r for r in reader if r["record_type"] == "insertion_summary"]

        assert len(rows) == 3

        # Row 1: First insertion (no preceding interval)
        assert float(rows[0]["beam_on_seconds"]) == pytest.approx(0.0)
        assert float(rows[0]["charge"]) == pytest.approx(0.0)
        assert float(rows[0]["fluence"]) == pytest.approx(0.0)
        assert float(rows[0]["dpa"]) == pytest.approx(0.0)

        # Row 2: Ticket 08 worked example!
        # I=1.0e-9 A held for 300.0 s -> Q = 3.0e-7 C
        assert float(rows[1]["beam_on_seconds"]) == pytest.approx(300.0)
        assert float(rows[1]["charge"]) == pytest.approx(3.0e-7)
        # phi = 3.0e-7 / (3 * 1.602176634e-19 * 0.5) = 1.248302e12 ions/cm^2
        assert float(rows[1]["fluence"]) == pytest.approx(1.248302e12, rel=1e-5)
        # dpa = phi * 1.0e-15 = 1.248302e-3 dpa
        assert float(rows[1]["dpa"]) == pytest.approx(1.248302e-3, rel=1e-5)

        # Row 3: Preceding 600.0 s at 2.0e-9 A -> delta Q = 1.2e-6 C, total Q = 1.5e-6 C
        assert float(rows[2]["beam_on_seconds"]) == pytest.approx(600.0)
        assert float(rows[2]["charge"]) == pytest.approx(1.5e-6)
        expected_fluence_3 = 1.5e-6 / (3 * 1.602176634e-19 * 0.5)
        expected_dpa_3 = expected_fluence_3 * 1.0e-15
        assert float(rows[2]["fluence"]) == pytest.approx(expected_fluence_3, rel=1e-5)
        assert float(rows[2]["dpa"]) == pytest.approx(expected_dpa_3, rel=1e-5)

    def test_standard_deviation_reflects_post_settle_samples_only(self, tmp_path):
        """A test asserts the standard deviation column reflects the post-settle samples only,

        matching the mean's sample set and excluding autorange settling window samples.
        """
        writer = CupSessionWriter(output_dir=tmp_path)
        # Open run at t=100.0 (settle window is [100.0, 101.0])
        writer.write_run_opened(
            t_host=100.0, run_id=1, arm_threshold=1e-9, release_threshold=0.5e-9
        )

        # Samples inside settle window [100.0, 101.0] with wild readings
        for t_h, t_i, cur in [
            (100.1, 0.1, 50.0e-9),
            (100.5, 0.5, 80.0e-9),
            (100.9, 0.9, 120.0e-9),
        ]:
            writer.write_sample(
                t_host=t_h, t_inst=t_i, current=cur, status_word=0, over_range=False, run_id=1
            )

        # Post-settle samples (t >= 101.0): [1.0e-9, 1.1e-9, 0.9e-9]
        # Mean = 1.0e-9, Sample Std (ddof=1) = 0.1e-9
        for t_h, t_i, cur in [
            (101.2, 1.2, 1.0e-9),
            (101.8, 1.8, 1.1e-9),
            (102.5, 2.5, 0.9e-9),
        ]:
            writer.write_sample(
                t_host=t_h, t_inst=t_i, current=cur, status_word=0, over_range=False, run_id=1
            )

        writer.write_run_closed(t_host=103.0, run_id=1, reason="dwell_expired")

        stats = writer.last_insertion_stats
        assert stats.sample_count == 3
        assert stats.excluded_count == 3
        assert stats.mean_a == pytest.approx(1.0e-9)
        assert stats.std_a == pytest.approx(0.1e-9)

        writer.write_insertion_summary(
            run_id=1,
            commanded_timestamp=99.8,
            confirmed_timestamp=100.0,
            dwell=3.0,
            sample_count=stats.sample_count,
            mean_current_a=stats.mean_a,
            std_current_a=stats.std_a,
            beam_on_seconds=0.0,
            charge=0.0,
        )
        writer.close()

        with open(writer.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader([line for line in f if not line.startswith("#")])
            summary_row = next(r for r in reader if r["record_type"] == "insertion_summary")

        assert summary_row["sample_count"] == "3"
        assert float(summary_row["mean_current_a"]) == pytest.approx(1.0e-9)
        assert float(summary_row["std_current_a"]) == pytest.approx(0.1e-9)
        # If the pre-settle samples were included, std would be > 40e-9
        assert float(summary_row["std_current_a"]) < 1.0e-9

    def test_reconstruct_dpa_from_columns_alone(self, tmp_path):
        """A test reads a completed session file back and reconstructs the dpa from

        the charge, charge state, area and k columns alone, asserting it matches the
        recorded dpa.
        """
        from rbl.config.cup_config import ELEMENTARY_CHARGE_C

        writer = CupSessionWriter(
            output_dir=tmp_path,
            charge_state=3,
            area_cm2=0.5,
            k=1.23e-15,
        )
        # Write two insertion summaries with known charges
        writer.write_insertion_summary(
            run_id=1,
            commanded_timestamp=0.0,
            confirmed_timestamp=0.2,
            dwell=3.0,
            sample_count=25,
            mean_current_a=1.5e-9,
            std_current_a=1e-11,
            beam_on_seconds=0.0,
            charge=0.0,
        )
        writer.write_insertion_summary(
            run_id=2,
            commanded_timestamp=300.0,
            confirmed_timestamp=300.2,
            dwell=3.0,
            sample_count=25,
            mean_current_a=1.5e-9,
            std_current_a=1e-11,
            beam_on_seconds=300.0,
            charge=4.5e-7,
        )
        writer.close()

        # Read back using DictReader and reconstruct dpa solely from row columns
        with open(writer.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader([line for line in f if not line.startswith("#")])
            summary_rows = [r for r in reader if r["record_type"] == "insertion_summary"]

        assert len(summary_rows) == 2
        for row in summary_rows:
            q_c = float(row["charge"])
            cs = int(row["charge_state"])
            area = float(row["area"])
            k_val = float(row["k"])
            recorded_dpa = float(row["dpa"])

            if q_c > 0.0:
                reconstructed_fluence = q_c / (cs * ELEMENTARY_CHARGE_C * area)
                reconstructed_dpa = reconstructed_fluence * k_val
            else:
                reconstructed_dpa = 0.0

            assert reconstructed_dpa == pytest.approx(recorded_dpa, rel=1e-6)

    def test_beam_on_seconds_excludes_cup_in_beam_time(self, tmp_path):
        """Beam-on seconds measures the interval between insertions and excludes

        time the cup spent in the beam, asserted by a test over a multi-insertion sequence.
        """
        from rbl.hardware.dose_model import DoseAccumulator

        acc = DoseAccumulator()
        writer = CupSessionWriter(output_dir=tmp_path, charge_state=1, area_cm2=1.0, k=1e-15)

        # Insertion 1: cup in beam from t=10.0 to t=15.0 (5.0 s dwell)
        acc.record_insertion(t_in=10.0, t_out=15.0, mean_current_a=1.0e-9)
        writer.write_insertion_summary(
            run_id=1,
            commanded_timestamp=9.9,
            confirmed_timestamp=10.0,
            dwell=5.0,
            sample_count=20,
            mean_current_a=1.0e-9,
            std_current_a=1e-11,
            beam_on_seconds=acc.last_beam_on_s,
            charge=acc.total_charge_c,
        )

        # Insertion 2: cup in beam from t=65.0 to t=70.0 (5.0 s dwell)
        # Interval between retract (15.0) and next insert (65.0) = 50.0 s
        acc.record_insertion(t_in=65.0, t_out=70.0, mean_current_a=1.0e-9)
        writer.write_insertion_summary(
            run_id=2,
            commanded_timestamp=64.9,
            confirmed_timestamp=65.0,
            dwell=5.0,
            sample_count=20,
            mean_current_a=1.0e-9,
            std_current_a=1e-11,
            beam_on_seconds=acc.last_beam_on_s,
            charge=acc.total_charge_c,
        )

        # Insertion 3: cup in beam from t=170.0 to t=175.0 (5.0 s dwell)
        # Interval between retract (70.0) and next insert (170.0) = 100.0 s
        acc.record_insertion(t_in=170.0, t_out=175.0, mean_current_a=1.0e-9)
        writer.write_insertion_summary(
            run_id=3,
            commanded_timestamp=169.9,
            confirmed_timestamp=170.0,
            dwell=5.0,
            sample_count=20,
            mean_current_a=1.0e-9,
            std_current_a=1e-11,
            beam_on_seconds=acc.last_beam_on_s,
            charge=acc.total_charge_c,
        )
        writer.close()

        with open(writer.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader([line for line in f if not line.startswith("#")])
            summary_rows = [r for r in reader if r["record_type"] == "insertion_summary"]

        assert len(summary_rows) == 3
        # Insertion 1: 0.0 s preceding beam-on
        assert float(summary_rows[0]["beam_on_seconds"]) == pytest.approx(0.0)
        # Insertion 2: 50.0 s (65 - 15), excluding the 5 s dwell of insertion 1
        assert float(summary_rows[1]["beam_on_seconds"]) == pytest.approx(50.0)
        # Insertion 3: 100.0 s (170 - 70), excluding the 5 s dwell of insertion 2
        assert float(summary_rows[2]["beam_on_seconds"]) == pytest.approx(100.0)

