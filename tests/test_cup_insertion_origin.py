"""
test_cup_insertion_origin.py
Ticket 30: only automatic insertions count toward the dose.

The real FaradayCupTab, a real open CupLog (and so a real CupSessionWriter file and
a real DoseAccumulator) are driven by instrument snapshots only: CupActuationState
for the controller's contacts and CupFeed readings for the picoammeter. Assertions
read the insertion_summary rows from the file the operator would archive and the
dose label the operator would read.

ADR 0003 amendment C2/C4: a beam check made by hand is recorded but never credited
as dose, and its cup-in time is taken out of the next counted hold interval.
"""
import csv
import os
from datetime import datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from rbl.gui.faraday_cup_tab import FaradayCupTab
from rbl.hardware.cup_status import CupPosition
from rbl.services.cup_log import CupLog
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
    """A tab with an open cup log, plus helpers that play insertions into it."""

    def __init__(self, qapp, tmp_path):
        self.qapp = qapp
        beamline = Beamline()
        self.cup_log = CupLog(test_root=tmp_path)
        self.writer = self.cup_log.open_test(datetime(2026, 10, 8, 9, 0, 0))
        self.tab = FaradayCupTab(beamline=beamline, cup_log=self.cup_log)
        self.tab.beamline = None  # no hardware behind the Insert/Retract buttons
        self.feed = CupFeed(self.tab, beamline=beamline)
        self.tab.spn_k.setValue(1e-15)
        self.tab.spn_charge_state.setValue(1)
        self.tab.on_patch_dimensions_changed(10.0, 10.0)
        self.tab.show()
        qapp.processEvents()
        self.tab.on_cup_actuation_state(_state(0.0, CupPosition.OUT, CupPosition.OUT))
        self.feed.send_reading(0.0, timestamp=0.0, t_host=0.0)
        qapp.processEvents()

    def play_reading(self, t):
        self.feed.send_reading(CURRENT_A, timestamp=t, t_host=t)
        self.qapp.processEvents()

    def cup_in_then_out(self, t_in, t_out):
        """Confirmed IN at t_in with readings through the dwell, then confirmed OUT."""
        tab = self.tab
        tab.on_cup_actuation_state(_state(t_in, CupPosition.IN, CupPosition.IN))
        self.play_reading(t_in + 0.1)
        self.play_reading((t_in + t_out) / 2.0)
        tab.on_cup_actuation_state(_state(t_out, CupPosition.OUT, CupPosition.OUT))
        self.play_reading(t_out + 0.1)

    def automatic(self, t_in, dwell=3.0):
        """The scheduler commands the insertion at t_in; the cup confirms IN then OUT."""
        tab = self.tab
        period = tab.spn_cycle_period.value()
        tab.on_cup_actuation_state(_state(t_in - period, CupPosition.OUT, CupPosition.OUT))
        tab.btn_cycle_arm.click()
        assert tab.cycle.is_armed
        # The period boundary: _tick_cycle acts on CycleInsert here.
        tab.on_cup_actuation_state(_state(t_in, CupPosition.OUT, CupPosition.OUT))
        self.cup_in_then_out(t_in + 0.2, t_in + dwell)
        tab.btn_cycle_stop.click()
        self.qapp.processEvents()

    def manual(self, t_in, t_out):
        """The operator clicks Insert at t_in and Retract at t_out."""
        tab = self.tab
        tab.on_cup_actuation_state(_state(t_in - 0.1, CupPosition.OUT, CupPosition.OUT))
        tab.btn_insert.click()
        self.cup_in_then_out(t_in, t_out)

    def forced(self, t_start, t_stop):
        tab = self.tab
        self.play_reading(t_start - 0.1)
        tab.btn_force_start.click()
        self.play_reading(t_start + 1.0)
        tab.btn_force_stop.click()
        self.qapp.processEvents()

    def summaries(self):
        with open(self.writer.csv_path, encoding="utf-8") as f:
            rows = csv.DictReader([line for line in f if not line.startswith("#")])
            return [r for r in rows if r["record_type"] == "insertion_summary"]

    def close(self):
        self.tab.close()


@pytest.fixture
def bench(qapp, tmp_path):
    b = Bench(qapp, tmp_path)
    yield b
    b.close()


def test_manual_insertion_is_recorded_but_not_counted(bench):
    bench.automatic(1000.0)
    bench.automatic(1500.0)
    q_before = bench.tab.lbl_running_q.text()
    assert q_before.strip() != "—"

    bench.manual(1700.0, 1710.0)

    assert bench.tab.lbl_running_q.text() == q_before
    last = bench.summaries()[-1]
    assert last["origin"] == "manual"
    assert last["counted_in_dose"] == "false"


def test_automatic_insertion_is_counted_and_charge_increases(bench):
    bench.automatic(1000.0)
    first = bench.tab.lbl_running_q.text()
    bench.automatic(1500.0)

    rows = bench.summaries()
    assert [r["origin"] for r in rows] == ["automatic", "automatic"]
    assert [r["counted_in_dose"] for r in rows] == ["true", "true"]
    assert float(rows[1]["charge"]) > float(rows[0]["charge"])
    assert bench.tab.lbl_running_q.text() != first


def test_manual_cup_in_time_is_taken_out_of_next_hold_interval(qapp, tmp_path):
    plain = Bench(qapp, tmp_path / "plain")
    plain.automatic(1000.0)
    plain.automatic(1500.0)
    plain_rows = plain.summaries()
    plain.close()

    with_manual = Bench(qapp, tmp_path / "with_manual")
    with_manual.automatic(1000.0)
    with_manual.manual(1200.0, 1210.0)
    with_manual.automatic(1500.0)
    rows = with_manual.summaries()
    with_manual.close()

    assert [r["origin"] for r in rows] == ["automatic", "manual", "automatic"]
    second_auto, plain_second = rows[2], plain_rows[1]
    assert float(plain_second["beam_on_seconds"]) - float(
        second_auto["beam_on_seconds"]
    ) == pytest.approx(10.0)
    assert float(second_auto["charge"]) < float(plain_second["charge"])


def test_manual_row_carries_running_totals_unchanged(bench):
    bench.automatic(1000.0)
    bench.automatic(1500.0)
    before = bench.summaries()[-1]

    bench.manual(1700.0, 1710.0)

    manual_row = bench.summaries()[-1]
    assert manual_row["charge"] == before["charge"]
    assert manual_row["total_beam_on_s"] == before["total_beam_on_s"]


def test_forced_run_is_recorded_as_forced_and_not_counted(bench):
    bench.automatic(1000.0)
    q_before = bench.tab.lbl_running_q.text()

    bench.forced(2000.0, 2002.0)

    last = bench.summaries()[-1]
    assert last["origin"] == "forced"
    assert last["counted_in_dose"] == "false"
    assert bench.tab.lbl_running_q.text() == q_before


def test_confirmed_in_with_no_command_is_uncommanded_and_not_counted(bench):
    """e.g. the controller switched to LOCAL and someone moved the cup by hand."""
    bench.cup_in_then_out(2000.0, 2004.0)

    last = bench.summaries()[-1]
    assert last["origin"] == "uncommanded"
    assert last["counted_in_dose"] == "false"


def test_a_manual_insertion_after_an_automatic_one_is_not_mislabelled(bench):
    """The automatic mark clears on the confirmed OUT; it must not leak forward."""
    bench.automatic(1000.0)
    bench.manual(1200.0, 1210.0)
    assert [r["origin"] for r in bench.summaries()] == ["automatic", "manual"]
