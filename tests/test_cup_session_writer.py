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
from rbl.services.cup_session_writer import CSV_COLUMNS, CupSessionWriter
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

    def test_over_range_sample_is_flagged_not_dropped(self, tmp_path):
        writer = CupSessionWriter(output_dir=tmp_path)
        writer.write_sample(
            t_host=2.0,
            t_inst=0.2,
            current=None,
            status_word=64,
            over_range=True,
            run_id=1,
        )
        writer.close()

        with open(writer.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader([line for line in f if not line.startswith("#")])
            rows = list(reader)

        assert len(rows) == 1
        row = rows[0]
        assert row["record_type"] == "sample"
        assert row["over_range"] == "True"
        assert row["status_word"] == "0x00000040"
        assert row["current_a"] == ""  # current is None for over-range sentinel
        assert row["run_id"] == "1"

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
