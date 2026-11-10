"""Ticket 33: the words on the cup screens say what they do.

The state machine still calls its two ends ``arm`` and ``disarm`` (sampling_cycle.py is
untouched, and analysis scripts read the CSV column names), but a person reading the
Faraday cup tab or the cup panel never sees those words.
"""

from __future__ import annotations

import re
from datetime import datetime

import pytest
from PySide6.QtWidgets import QApplication, QGroupBox, QLabel, QPushButton, QWidget

from rbl.gui.faraday_cup_tab import FaradayCupTab
from rbl.gui.widgets.cup_panel import CupPanel
from rbl.services.cup_log import CupLog
from rbl.services.cup_session_writer import CSV_COLUMNS
from rbl.state.beamline import Beamline
from tests.test_faraday_cup_tab import arm_cycle_helper, make_connected_cup_tab

OLD_WORDS = re.compile(r"\b(arm|armed|disarm|disarmed|release threshold)\b", re.IGNORECASE)


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _texts(root: QWidget) -> list[str]:
    """Every string a person can read on `root` and its children: text, titles, tooltips."""
    out: list[str] = []
    for w in [root, *root.findChildren(QWidget)]:
        if isinstance(w, (QPushButton, QLabel)):
            out.append(w.text())
        if isinstance(w, QGroupBox):
            out.append(w.title())
        out.append(w.toolTip())
    return [t for t in out if t]


def _offenders(root: QWidget) -> list[str]:
    return [t for t in _texts(root) if OLD_WORDS.search(t)]


@pytest.fixture
def rig(qapp, tmp_path):
    beamline = Beamline()
    cup_log = CupLog(test_root=tmp_path / "cup")
    tab = FaradayCupTab(beamline=beamline, cup_log=cup_log)
    panel = CupPanel(cup_log)
    tab.cup_view_changed.connect(panel.on_cup_view)
    tab.show()
    panel.show()
    qapp.processEvents()
    yield tab, panel
    cup_log.close()
    panel.close()
    tab.close()


def test_no_old_words_on_the_idle_tab_or_panel(rig):
    tab, panel = rig
    assert _offenders(tab) == []
    assert _offenders(panel) == []


def test_no_old_words_while_automatic_insertion_is_running(qapp, tmp_path):
    tab, _feed = make_connected_cup_tab(qapp, tmp_path)
    arm_cycle_helper(tab, qapp)
    assert tab.automatic_insertion_running()
    assert _offenders(tab) == []
    tab.close()


def test_no_old_words_in_a_refusal_message(qapp, tmp_path):
    tab, _feed = make_connected_cup_tab(qapp, tmp_path)
    tab.btn_cycle_arm.click()  # coefficient, charge state and area are unset: refused
    qapp.processEvents()
    assert tab.lbl_cycle_fault.text().startswith("Cannot start automatic cup insertion: ")
    assert _offenders(tab) == []
    tab.close()


def test_the_table_strings_each_appear_on_their_widget(qapp, tmp_path):
    tab, _feed = make_connected_cup_tab(qapp, tmp_path)
    assert tab.btn_cycle_arm.text() == "Start automatic cup insertion"
    assert tab.btn_cycle_stop.text() == "Stop automatic cup insertion"
    titles = [g.title() for g in tab.findChildren(QGroupBox)]
    assert "Automatic cup insertion" in titles
    assert "Sampling Cycle" not in titles
    labels = [lbl.text() for lbl in tab.settings_group.findChildren(QLabel)]
    assert "Run start current:" in labels
    assert "Run end current:" in labels
    assert tab.lbl_cycle_state.text() == "Stopped"
    arm_cycle_helper(tab, qapp)
    assert tab.lbl_cycle_state.text().startswith("Running")
    tab.close()


def test_threshold_tooltips_use_the_new_words(rig):
    tab, _panel = rig
    group = tab.settings_group
    assert "run start current" in group.spn_arm_threshold.toolTip().lower()
    assert "run end current" in group.spn_release_threshold.toolTip().lower()


def test_cup_log_header_and_run_marker_use_the_new_words(tmp_path):
    log = CupLog(test_root=tmp_path)
    writer = log.open_test(datetime(2026, 10, 8, 9, 0, 0))
    log.close()
    text = open(writer.csv_path, encoding="utf-8").read()
    header = [ln for ln in text.splitlines() if ln.startswith("# thresholds:")][0]
    assert "run start current" in header
    assert "run end current" in header
    assert "arm threshold" not in text.lower()
    assert "release threshold" not in text.lower()
    # identifiers and CSV columns keep their names: analysis scripts read them
    assert "# columns: " + ", ".join(CSV_COLUMNS) in text


def test_identifiers_are_untouched():
    from rbl.services import sampling_cycle

    scheduler = sampling_cycle.SamplingCycleScheduler
    assert callable(getattr(scheduler, "arm"))
    assert callable(getattr(scheduler, "disarm"))
