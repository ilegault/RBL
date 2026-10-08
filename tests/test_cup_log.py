"""
Tests for rbl.services.cup_log: one owner of the open cup log, and reading
dose totals back from earlier logs.

WHY THIS EXISTS
---------------
ADR 0003 amendment C1/C4: there is never more than one cup log, a session's log
is ``cup.csv`` in the session folder, a test log is dated under the cup root, and
dose continuation reads the last *counted* summary row of an earlier log. Totals
tests read files written by a real CupSessionWriter so the column contract
between writer and reader is exercised, not hand-typed.
"""
import ast
import os
from datetime import datetime
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from rbl.config.cup_config import CUP_SETTLE_WINDOW_S
from rbl.services import cup_log, cup_log_totals
from rbl.services.cup_log import (
    ContinueChoice,
    CupLog,
    CupLogKind,
    CupView,
    RestartChoice,
    find_previous_session_cup_log,
    read_dose_totals,
)
from rbl.services.cup_session_writer import CupSessionWriter

NOW = datetime(2026, 9, 29, 14, 30, 12)


def _summary(w: CupSessionWriter, *, origin, counted, confirmed, charge, beam_on, mean=2e-9):
    w.write_insertion_summary(
        run_id=1,
        commanded_timestamp=confirmed - 0.5,
        confirmed_timestamp=confirmed,
        dwell=10.0,
        sample_count=5,
        mean_current_a=mean,
        std_current_a=1e-11,
        beam_on_seconds=beam_on,
        charge=charge,
        origin=origin,
        counted_in_dose=counted,
        total_beam_on_s=beam_on,
    )


def _log(tmp_path):
    return CupLog(test_root=tmp_path / "cup")


def test_open_test_names_file_by_month_and_time_and_never_reuses(tmp_path):
    log = _log(tmp_path)
    closed, opened = [], []
    log.closed.connect(closed.append)
    log.opened.connect(lambda p, k: opened.append((p, k)))

    w1 = log.open_test(NOW)
    first = tmp_path / "cup" / "2026-09" / "cup_20260929T143012.csv"
    assert first.exists() and log.path == str(first)
    assert log.kind is CupLogKind.TEST and log.writer is w1

    log.open_test(NOW)
    second = tmp_path / "cup" / "2026-09" / "cup_20260929T143012_2.csv"
    assert second.exists() and log.path == str(second)
    assert closed == [str(first)]
    assert [p for p, _ in opened] == [str(first), str(second)]
    assert w1.is_closed
    log.close()


def test_open_for_session_writes_cup_csv_and_closes_test_log_first(tmp_path):
    log = _log(tmp_path)
    events = []
    log.closed.connect(lambda p: events.append(("closed", p)))
    log.opened.connect(lambda p, k: events.append(("opened", k)))
    log.open_test(NOW)
    test_path = log.path

    folder = tmp_path / "session_1"
    log.open_for_session(folder)
    assert (folder / "cup.csv").exists()
    assert log.kind is CupLogKind.SESSION
    assert events == [("opened", "test"), ("closed", test_path), ("opened", "session")]

    assert log.close() == str(folder / "cup.csv")
    assert log.writer is None and log.kind is None and log.path is None
    assert log.close() is None


def test_open_for_session_passes_continuation_to_header(tmp_path):
    log = _log(tmp_path)
    cont = {"source_path": "old.csv", "total_charge_c": 1.0e-6, "total_beam_on_s": 5.0,
            "insertion_count": 3, "beam_on_during_gap": False}
    w = log.open_for_session(tmp_path / "s", continuation=cont)
    text = open(w.csv_path, encoding="utf-8").read()
    log.close()
    assert "# dose continued from: old.csv" in text


def test_read_dose_totals_returns_last_counted_row(tmp_path):
    w = CupSessionWriter(session_id="cup", output_dir=tmp_path)
    _summary(w, origin="automatic", counted=True, confirmed=100.0, charge=1e-9, beam_on=10.0)
    _summary(
        w, origin="automatic", counted=True, confirmed=200.0, charge=3e-9, beam_on=25.0, mean=4e-9
    )
    _summary(w, origin="manual", counted=False, confirmed=300.0, charge=9e-9, beam_on=99.0)
    path = w.close()

    t = read_dose_totals(Path(path))
    assert t is not None
    assert t.total_charge_c == pytest.approx(3e-9)
    assert t.total_beam_on_s == pytest.approx(25.0)
    assert t.insertion_count == 2
    assert t.last_out_t == pytest.approx(210.0)  # confirmed 200 + dwell 10
    assert t.last_current_a == pytest.approx(4e-9)
    assert t.source_path == path


def test_read_dose_totals_none_when_only_manual_rows(tmp_path):
    w = CupSessionWriter(session_id="cup", output_dir=tmp_path)
    _summary(w, origin="manual", counted=False, confirmed=100.0, charge=1e-9, beam_on=10.0)
    path = w.close()
    assert read_dose_totals(Path(path)) is None


def test_read_dose_totals_none_for_missing_file(tmp_path):
    assert read_dose_totals(tmp_path / "nope.csv") is None


def test_read_dose_totals_adds_continued_insertion_count(tmp_path):
    cont = {"source_path": "old.csv", "total_charge_c": 1e-9, "total_beam_on_s": 5.0,
            "insertion_count": 3, "beam_on_during_gap": True}
    w = CupSessionWriter(session_id="cup", output_dir=tmp_path, continuation=cont)
    _summary(w, origin="automatic", counted=True, confirmed=50.0, charge=2e-9, beam_on=9.0)
    path = w.close()
    t = read_dose_totals(Path(path))
    assert t.insertion_count == 4  # 3 carried over + 1 in this file


def _session(logs, name, rows):
    folder = logs / name
    w = CupSessionWriter(session_id="cup", output_dir=folder)
    for counted in rows:
        _summary(w, origin="automatic" if counted else "manual", counted=counted,
                 confirmed=100.0, charge=1e-9, beam_on=10.0)
    w.close()
    return folder / "cup.csv"


def test_find_previous_session_cup_log_skips_files_without_counted_rows(tmp_path):
    logs = tmp_path / "logs"
    oldest = _session(logs, "session_20260901T090000", [True])
    middle = _session(logs, "session_20260915T090000", [True])
    _session(logs, "session_20260920T090000", [False])
    assert find_previous_session_cup_log(logs, exclude=tmp_path / "x.csv") == middle
    assert oldest != middle


def test_find_previous_session_cup_log_excludes_given_path(tmp_path):
    logs = tmp_path / "logs"
    only = _session(logs, "session_20260901T090000", [True])
    _session(logs, "session_20260920T090000", [False])
    assert find_previous_session_cup_log(logs, exclude=only) is None


def test_find_previous_session_cup_log_missing_dir(tmp_path):
    assert find_previous_session_cup_log(tmp_path / "none", exclude=tmp_path / "x") is None


def test_choice_and_view_dataclasses_are_frozen():
    r = RestartChoice(resume_schedule=True, beam_on_during_gap=False)
    c = ContinueChoice(continue_dose=True, beam_on_during_gap=True)
    v = CupView(logging=False, log_path=None, run_open=False, automatic_running=False,
                charge_c=0.0, fluence=None, dpa=None)
    for obj, attr in ((r, "resume_schedule"), (c, "continue_dose"), (v, "logging")):
        with pytest.raises(Exception):
            setattr(obj, attr, not getattr(obj, attr))


def test_pure_totals_module_imports_no_qt_and_is_reexported():
    tree = ast.parse(open(cup_log_totals.__file__, encoding="utf-8").read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert not [m for m in mods if m.startswith(("PySide6", "rbl.gui", "shiboken"))]
    assert cup_log.read_dose_totals is cup_log_totals.read_dose_totals
    assert cup_log.find_previous_session_cup_log is cup_log_totals.find_previous_session_cup_log


class TestWriterSettingsProvider:
    """The settings in force when a log opens go into its header (ticket 28)."""

    PROVIDED = {"arm_threshold_a": 7.5e-7, "release_threshold_a": 2.5e-7, "settle_window_s": 3.0}

    def test_test_log_header_carries_provided_thresholds(self, tmp_path):
        log = CupLog(test_root=tmp_path)
        log.set_writer_settings_provider(lambda: dict(self.PROVIDED))
        writer = log.open_test(NOW)
        log.close()
        header = [
            ln for ln in Path(writer.csv_path).read_text(encoding="utf-8").splitlines()
            if ln.startswith("# thresholds:")
        ]
        assert "arm=7.500e-07 A" in header[0]
        assert "release=2.500e-07 A" in header[0]
        assert "settle_window=3.0 s" in header[0]

    def test_session_log_header_carries_provided_thresholds(self, tmp_path):
        log = CupLog(test_root=tmp_path)
        log.set_writer_settings_provider(lambda: dict(self.PROVIDED))
        writer = log.open_for_session(tmp_path / "session_x")
        assert writer.settle_window_s == pytest.approx(3.0)
        log.close()

    def test_without_provider_writer_defaults_apply(self, tmp_path):
        log = CupLog(test_root=tmp_path)
        writer = log.open_test(NOW)
        log.close()
        assert writer.settle_window_s == pytest.approx(CUP_SETTLE_WINDOW_S)
