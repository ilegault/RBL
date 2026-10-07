"""
Ticket 34: the suite cannot write to the operator's real new stores, and the
cable-only load condition exists end to end.

Behaviour asserted: where the two new stores resolve inside a test, the
enum's values, what the Load Characterization tab returns when the new radio
button is checked, and what the pre-run checklist tells the operator.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QCheckBox

from rbl.config import paths
from rbl.config.calibration_config import LoadCondition
from rbl.gui.calibration_tab import _PreRunChecklistDialog
from rbl.gui.load_characterization_tab import LoadCharacterizationTab
from rbl.state.beamline import Beamline


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_new_stores_resolve_under_tmp_path_not_home(tmp_path):
    # Not "under Path.home()": on Windows the temp directory itself lives under
    # the user's home (AppData/Local/Temp), so that is true even when the
    # redirect works. What must never be true is being under the operator's
    # real store locations (the unpatched defaults in rbl.config.paths).
    real_roots = (Path.home() / ".config" / "rbl",
                  Path.home() / "Desktop" / "RBL_log")
    for p in (paths.AMPLIFIER_ASSIGNMENTS_STORE, paths.CHARACTERIZATION_DIR):
        assert Path(tmp_path) in Path(p).parents
        for real in real_roots:
            assert real not in Path(p).parents


def test_load_condition_values_and_round_trip():
    assert [c.value for c in LoadCondition] == [
        "DISCONNECTED", "ON_PLATES", "CABLE_ONLY"]
    assert LoadCondition("CABLE_ONLY") is LoadCondition.CABLE_ONLY


class _FakeCharacterizer(QObject):
    """Stands in for LoadCharacterizer and records the condition it was given."""
    point_measured = Signal(dict)
    finished = Signal(str)
    error = Signal(str)
    progress = Signal(int, int, str)
    seen = []

    def __init__(self, funcgen_map, load_condition, **_kw):
        super().__init__()
        _FakeCharacterizer.seen.append(load_condition)

    def start_mode_a(self, *_a):
        pass


@pytest.mark.parametrize("radio, expected", [
    ("rb_cable_only", LoadCondition.CABLE_ONLY),
    ("rb_disconnected", LoadCondition.DISCONNECTED),
    ("rb_on_plates", LoadCondition.ON_PLATES),
])
def test_run_uses_the_condition_the_radio_buttons_show(
        qapp, monkeypatch, radio, expected):
    from rbl.gui import load_characterization_tab as mod
    _FakeCharacterizer.seen = []
    monkeypatch.setattr(mod, "LoadCharacterizer", _FakeCharacterizer)
    monkeypatch.setattr(mod, "RampEngine", lambda *_a, **_k: object())
    beamline = Beamline()
    tab = LoadCharacterizationTab(beamline)
    tab.on_labjack_connected("T7-12345")
    label = tab.cb_channel.currentText()
    monkeypatch.setattr(beamline, "build_funcgen_map",
                        lambda: {label: (object(), 1)})
    getattr(tab, radio).setChecked(True)
    tab.btn_run.click()
    assert _FakeCharacterizer.seen == [expected]


def test_cable_only_radio_label(qapp):
    tab = LoadCharacterizationTab(Beamline())
    assert tab.rb_cable_only.text() == "Cable only (far end open)"


def test_cable_only_checklist_asks_for_the_far_end(qapp):
    dlg = _PreRunChecklistDialog(LoadCondition.CABLE_ONLY)
    assert any("far end" in cb.text() for cb in dlg.findChildren(QCheckBox))


def test_other_conditions_do_not_get_the_far_end_item(qapp):
    for cond in (LoadCondition.DISCONNECTED, LoadCondition.ON_PLATES):
        dlg = _PreRunChecklistDialog(cond)
        assert not any("far end" in cb.text()
                       for cb in dlg.findChildren(QCheckBox))
