"""
test_cup_stop_restart.py
Ticket 31: Stop asks first, restart asks about the gap, and Start/Stop are two buttons.

The real FaradayCupTab with a real open CupLog (so a real CupSessionWriter file and a
real DoseAccumulator) is driven by CupActuationState and CupFeed snapshots. Only the two
blocking dialogs are faked (`_confirm_stop_automatic`, `_ask_restart`), because a modal
cannot be clicked under offscreen Qt. Assertions read the rows of the file the operator
would archive and the scheduler's own timing.

ADR 0003 decision 3 and its 2026-10-05 amendment (C3, C4); ADR 0002 amendment A6, A8.
"""
import csv
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from rbl.config.cup_config import CUP_MOVE_CONFIRMATION_TIMEOUT_S
from rbl.gui.faraday_cup_tab import FaradayCupTab
from rbl.gui.widgets.restart_dialog import RestartDialog
from rbl.hardware.cup_status import CupPosition
from rbl.services.cup_log import CupLog, RestartChoice
from rbl.snapshots import CupActuationState
from rbl.state.beamline import Beamline
from tests.payloads import CupFeed

CURRENT_A = 2.0e-6


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _state(t, commanded, confirmed):
    return CupActuationState(
        connected=True,
        commanded=commanded,
        confirmed=confirmed,
        auto_mode=True,
        stale=False,
        last_transition_t=t,
        t=t,
    )


class Bench:
    """A tab with an open cup log whose two dialogs are recorded and scripted."""

    def __init__(self, qapp, tmp_path, monkeypatch):
        self.qapp = qapp
        beamline = Beamline()
        self.cup_log = CupLog(test_root=tmp_path)
        self.writer = self.cup_log.open_test(datetime(2026, 10, 8, 9, 0, 0))
        self.tab = FaradayCupTab(beamline=beamline, cup_log=self.cup_log)
        self.hardware = MagicMock()
        self.tab.beamline = self.hardware  # records the commands the buttons send
        self.feed = CupFeed(self.tab, beamline=beamline)
        self.tab.spn_k.setValue(1e-15)
        self.tab.spn_charge_state.setValue(1)
        self.tab.on_patch_dimensions_changed(10.0, 10.0)
        self.tab.show()
        qapp.processEvents()
        self.stop_asked = 0
        self.stop_answer = True
        self.restart_asked = []
        self.restart_answer = RestartChoice(resume_schedule=True, beam_on_during_gap=True)
        monkeypatch.setattr(self.tab, "_confirm_stop_automatic", self.confirm_stop)
        monkeypatch.setattr(self.tab, "_ask_restart", self.ask_restart)
        self.state(0.0, CupPosition.OUT, CupPosition.OUT)
        self.feed.send_reading(0.0, timestamp=0.0, t_host=0.0)
        qapp.processEvents()

    def confirm_stop(self):
        self.stop_asked += 1
        return self.stop_answer

    def ask_restart(self, gap_start_t, now_t, saved_boundary_t):
        self.restart_asked.append((gap_start_t, now_t, saved_boundary_t))
        return self.restart_answer

    def state(self, t, commanded, confirmed):
        self.tab.on_cup_actuation_state(_state(t, commanded, confirmed))
        self.qapp.processEvents()

    def reading(self, t):
        self.feed.send_reading(CURRENT_A, timestamp=t, t_host=t)
        self.qapp.processEvents()

    def start_at(self, t):
        self.state(t, CupPosition.OUT, CupPosition.OUT)
        self.tab.btn_cycle_arm.click()
        self.qapp.processEvents()

    def insertion(self, t_in, t_out):
        """The tick at t_in commands IN; the cup confirms IN, dwells, confirms OUT."""
        self.state(t_in, CupPosition.OUT, CupPosition.OUT)
        self.state(t_in + 0.2, CupPosition.IN, CupPosition.IN)
        self.reading(t_in + 0.3)
        self.reading((t_in + t_out) / 2.0)
        self.state(t_out, CupPosition.OUT, CupPosition.OUT)
        self.reading(t_out + 0.1)

    def stop(self):
        self.tab.btn_cycle_stop.click()
        self.qapp.processEvents()

    def rows(self, record_type):
        with open(self.writer.csv_path, encoding="utf-8") as f:
            rows = csv.DictReader([line for line in f if not line.startswith("#")])
            return [r for r in rows if r["record_type"] == record_type]

    def close(self):
        self.tab.close()


@pytest.fixture
def bench(qapp, tmp_path, monkeypatch):
    b = Bench(qapp, tmp_path, monkeypatch)
    yield b
    b.close()


def test_declined_stop_changes_nothing_and_confirmed_stop_withdraws(bench):
    bench.start_at(700.0)
    bench.state(1000.0, CupPosition.OUT, CupPosition.OUT)  # scheduler commands IN
    bench.state(1000.2, CupPosition.IN, CupPosition.IN)
    bench.hardware.reset_mock()
    disarm_rows_before = len(bench.rows("cycle_disarmed"))

    bench.stop_answer = False
    bench.stop()

    assert bench.stop_asked == 1
    assert bench.tab.cycle.is_armed is True
    bench.hardware.command_cup_out.assert_not_called()
    assert len(bench.rows("cycle_disarmed")) == disarm_rows_before

    bench.stop_answer = True
    bench.stop()

    assert bench.stop_asked == 2
    assert bench.tab.cycle.is_armed is False
    bench.hardware.command_cup_out.assert_called_once()
    disarmed = bench.rows("cycle_disarmed")
    assert len(disarmed) == disarm_rows_before + 1
    assert "reason=stop" in disarmed[-1]["details"]


def test_fault_stop_does_not_ask(bench):
    bench.start_at(700.0)
    bench.state(1000.0, CupPosition.OUT, CupPosition.OUT)  # commands IN
    assert bench.tab.cycle.is_armed is True

    # The move never confirms: the fault path disarms (ADR 0003 decision 3).
    bench.state(1000.0 + CUP_MOVE_CONFIRMATION_TIMEOUT_S + 0.5,
                CupPosition.IN, CupPosition.OUT)

    assert bench.tab.cycle.is_armed is False
    assert "did not confirm" in bench.tab.lbl_cycle_fault.text()
    assert bench.stop_asked == 0


def test_closing_the_cup_log_stops_without_asking(bench):
    bench.start_at(700.0)

    bench.cup_log.close()
    bench.qapp.processEvents()

    assert bench.tab.cycle.is_armed is False
    assert bench.stop_asked == 0


def test_start_is_start_only_and_does_nothing_while_running(bench):
    bench.start_at(700.0)
    armed_rows = len(bench.rows("cycle_armed"))

    assert bench.tab.btn_cycle_arm.isEnabled() is False
    bench.tab.btn_cycle_arm.click()  # disabled: a click must change nothing

    assert bench.tab.cycle.is_armed is True
    assert len(bench.rows("cycle_armed")) == armed_rows
    assert len(bench.rows("cycle_disarmed")) == 0
    bench.stop()
    assert bench.tab.btn_cycle_arm.isEnabled() is True


def test_first_start_in_a_cup_log_asks_nothing(bench):
    bench.start_at(700.0)

    assert bench.tab.cycle.is_armed is True
    assert bench.restart_asked == []


def test_a_new_cup_log_has_no_stop_gap(bench):
    bench.start_at(700.0)
    bench.stop()
    bench.cup_log.close()
    bench.writer = bench.cup_log.open_test(datetime(2026, 10, 8, 10, 0, 0))
    bench.qapp.processEvents()

    bench.start_at(2000.0)

    assert bench.tab.cycle.is_armed is True
    assert bench.restart_asked == []


def _stop_after_first_insertion(bench):
    """Counted insertion at 1000 (armed at 700, period 300), operator stop at 1003."""
    bench.start_at(700.0)
    bench.insertion(1000.0, 1003.0)
    bench.stop()
    assert bench.tab.cycle.saved_boundary_t == pytest.approx(1300.0)


def test_restart_resuming_with_no_beam_excludes_the_gap(bench):
    _stop_after_first_insertion(bench)
    bench.restart_answer = RestartChoice(resume_schedule=True, beam_on_during_gap=False)

    bench.start_at(1100.0)

    assert bench.restart_asked == [(1003.0, 1100.0, 1300.0)]
    assert bench.tab.cycle.is_armed is True
    assert bench.tab.cycle.time_to_next_insertion(1100.0) == pytest.approx(200.0)
    restarted = bench.rows("automatic_insertion_restarted")
    assert len(restarted) == 1
    assert "mode=resumed" in restarted[0]["details"]
    assert "beam_on_during_gap=false" in restarted[0]["details"]
    assert "gap_start_t=1003.000000" in restarted[0]["details"]
    assert "gap_end_t=1100.000000" in restarted[0]["details"]
    assert bench.rows("cycle_armed")[-1]["details"].count("mode=resumed") == 1


def test_next_counted_row_excludes_the_gap_by_exactly_its_length(qapp, tmp_path, monkeypatch):
    held = Bench(qapp, tmp_path / "held", monkeypatch)
    excluded = Bench(qapp, tmp_path / "excluded", monkeypatch)
    try:
        for b, beam_on in ((held, True), (excluded, False)):
            _stop_after_first_insertion(b)
            b.restart_answer = RestartChoice(
                resume_schedule=True, beam_on_during_gap=beam_on
            )
            b.start_at(1100.0)
            b.insertion(1300.0, 1303.0)  # lands on the saved boundary
        held_rows = held.rows("insertion_summary")
        excl_rows = excluded.rows("insertion_summary")
        held_row, excl_row = held_rows[-1], excl_rows[-1]
        # The hold interval opens when the first run closed; the excluded gap is the
        # part of the pause that lies inside it, up to the restart at 1100.
        first_run_closed_t = float(held_rows[0]["host_timestamp"])

        assert held_row["counted_in_dose"] == excl_row["counted_in_dose"] == "true"
        assert float(held_row["beam_on_seconds"]) - float(
            excl_row["beam_on_seconds"]
        ) == pytest.approx(1100.0 - first_run_closed_t)
        assert float(excl_row["charge"]) < float(held_row["charge"])
    finally:
        held.close()
        excluded.close()


def test_restart_fresh_with_beam_on_holds_the_gap(bench):
    _stop_after_first_insertion(bench)
    bench.restart_answer = RestartChoice(resume_schedule=False, beam_on_during_gap=True)

    bench.start_at(1100.0)

    assert bench.tab.cycle.time_to_next_insertion(1100.0) == pytest.approx(300.0)
    restarted = bench.rows("automatic_insertion_restarted")
    assert len(restarted) == 1
    assert "mode=fresh" in restarted[0]["details"]
    assert "beam_on_during_gap=true" in restarted[0]["details"]


def test_cancelled_restart_starts_nothing_and_writes_nothing(bench):
    _stop_after_first_insertion(bench)
    armed_rows = len(bench.rows("cycle_armed"))
    bench.restart_answer = None

    bench.start_at(1100.0)

    assert bench.restart_asked == [(1003.0, 1100.0, 1300.0)]
    assert bench.tab.cycle.is_armed is False
    assert len(bench.rows("cycle_armed")) == armed_rows
    assert bench.rows("automatic_insertion_restarted") == []

    # The gap is still open: the next Start asks again, about the same gap.
    bench.restart_answer = RestartChoice(resume_schedule=False, beam_on_during_gap=True)
    bench.start_at(1200.0)
    assert bench.restart_asked[-1] == (1003.0, 1200.0, 1300.0)
    assert bench.tab.cycle.is_armed is True


def test_a_second_restart_is_asked_about_the_new_gap_only(bench):
    _stop_after_first_insertion(bench)
    bench.start_at(1100.0)
    bench.state(1150.0, CupPosition.OUT, CupPosition.OUT)
    bench.stop()

    bench.start_at(1200.0)

    assert bench.restart_asked[-1][:2] == (1150.0, 1200.0)


def test_refused_start_does_not_ask(bench):
    _stop_after_first_insertion(bench)
    bench.tab.spn_k.setValue(0.0)  # dose chain incomplete: arming is refused

    bench.start_at(1100.0)

    assert bench.tab.cycle.is_armed is False
    assert bench.restart_asked == []


def test_tab_has_no_keep_schedule_checkbox():
    import rbl.gui.faraday_cup_tab as module

    assert "chk_keep_schedule" not in Path(module.__file__).read_text(encoding="utf-8")


# ── The restart dialog on its own ─────────────────────────────────────────────


def test_restart_dialog_has_no_default_beam_answer_and_blocks_ok(qapp):
    dlg = RestartDialog("10:00:03", "10:01:40", resume_available=True)

    assert dlg.rb_beam_yes.isChecked() is False
    assert dlg.rb_beam_no.isChecked() is False
    assert dlg.btn_ok.isEnabled() is False
    assert dlg.choice() is None
    assert "10:00:03" in dlg.lbl_question.text() and "10:01:40" in dlg.lbl_question.text()
    dlg.close()


def test_restart_dialog_returns_the_operators_answers(qapp):
    dlg = RestartDialog("a", "b", resume_available=True)

    dlg.rb_beam_no.setChecked(True)
    assert dlg.btn_ok.isEnabled() is True
    assert dlg.rb_resume.isEnabled() is True
    dlg.rb_resume.setChecked(True)
    assert dlg.choice() == RestartChoice(resume_schedule=True, beam_on_during_gap=False)

    dlg.rb_beam_yes.setChecked(True)
    dlg.rb_fresh.setChecked(True)
    assert dlg.choice() == RestartChoice(resume_schedule=False, beam_on_during_gap=True)
    dlg.close()


def test_restart_dialog_cannot_resume_a_boundary_that_has_passed(qapp):
    dlg = RestartDialog("a", "b", resume_available=False)

    assert dlg.rb_resume.isEnabled() is False
    assert dlg.rb_fresh.isChecked() is True
    dlg.rb_beam_yes.setChecked(True)
    assert dlg.choice() == RestartChoice(resume_schedule=False, beam_on_during_gap=True)
    dlg.close()


def test_held_gap_is_not_excluded_even_when_the_schedule_is_fresh(qapp, tmp_path, monkeypatch):
    """beam_on_during_gap=True keeps the gap in the beam-on time whatever the schedule."""
    resumed = Bench(qapp, tmp_path / "resumed", monkeypatch)
    fresh = Bench(qapp, tmp_path / "fresh", monkeypatch)
    try:
        for b, resume, t_next in ((resumed, True, 1300.0), (fresh, False, 1400.0)):
            _stop_after_first_insertion(b)
            b.restart_answer = RestartChoice(
                resume_schedule=resume, beam_on_during_gap=True
            )
            b.start_at(1100.0)
            b.insertion(t_next, t_next + 3.0)
        # Nothing was taken out of either hold interval: beam-on time is simply the
        # span from the first run's close to the second insertion's entry.
        for b, t_next in ((resumed, 1300.0), (fresh, 1400.0)):
            rows = b.rows("insertion_summary")
            first_run_closed_t = float(rows[0]["host_timestamp"])
            assert float(rows[-1]["beam_on_seconds"]) == pytest.approx(
                t_next + 0.3 - first_run_closed_t  # run opens on the first reading in
            )
    finally:
        resumed.close()
        fresh.close()
