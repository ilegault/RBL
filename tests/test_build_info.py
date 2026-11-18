"""Build stamp: build.bat writes build_info.json, rbl/build_info.py reads it.

The JSON literals here match what build.bat's PowerShell actually emits
(ConvertTo-Json of an ordered hashtable), so a change on either side shows up.
"""
import json
import sys

from rbl import build_info as bi
from rbl.build_info import BuildInfo, format_label, load_build_info, parse_build_info

STAMP = (
    '{\n    "version":  "4.0",\n    "commit":  "60e7b08",\n'
    '    "dirty":  false,\n    "built_at":  "2026-11-18T15:27:33"\n}'
)


def test_clean_build_label():
    info = parse_build_info(json.loads(STAMP), frozen=True)
    assert info == BuildInfo("4.0", "60e7b08", False, "2026-11-18T15:27:33", True)
    assert info.label() == "v4.0 (60e7b08)"


def test_dirty_build_is_marked_in_the_label():
    assert format_label(BuildInfo("4.0", "60e7b08", True, "", True)) == "v4.0 (60e7b08+dirty)"


def test_dev_run_label_has_no_v_prefix():
    assert format_label(BuildInfo("dev", "abc1234", False, "", False)) == "dev (abc1234)"
    assert format_label(BuildInfo("dev", "", False, "", False)) == "dev"


def test_missing_keys_degrade_rather_than_raise():
    info = parse_build_info({}, frozen=True)
    assert info.version == "unknown"
    assert info.commit == ""
    assert info.label() == "unknown"


def test_reads_stamp_from_explicit_path(tmp_path):
    p = tmp_path / "build_info.json"
    # build.bat writes UTF-8 without a BOM; a BOM (hand-edited file) must not break it.
    p.write_text("﻿" + STAMP, encoding="utf-8")
    info = load_build_info(p)
    assert (info.version, info.commit, info.dirty) == ("4.0", "60e7b08", False)


def test_corrupt_stamp_reports_unknown_not_crash(tmp_path):
    p = tmp_path / "build_info.json"
    p.write_text("{not json", encoding="utf-8")
    assert load_build_info(p).version == "unknown"
    assert load_build_info(tmp_path / "missing.json").version == "unknown"


def test_frozen_build_reads_the_bundled_stamp(tmp_path, monkeypatch):
    (tmp_path / bi.BUILD_INFO_FILENAME).write_text(STAMP, encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    info = load_build_info()
    assert info.frozen is True
    assert info.label() == "v4.0 (60e7b08)"


def test_frozen_build_without_stamp_says_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert load_build_info().version == "unknown"


def test_source_run_is_dev():
    info = load_build_info()
    assert info.version == "dev"
    assert info.frozen is False
    assert info.built_at == ""


def test_window_title_carries_the_build_label():
    from rbl.gui.app import window_title

    assert window_title() == f"Right Beam Line DAQ — {bi.build_info().label()}"
    assert bi.build_info().label() in window_title()


def test_calibration_sidecar_records_the_build(tmp_path):
    from rbl.services.calibration_writer import CalibrationWriter

    w = CalibrationWriter(run_id="t", output_dir=tmp_path)
    w.close()
    meta = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))
    assert meta["rbl_build"] == bi.build_info().as_dict()
    assert meta["rbl_build"]["version"] == "dev"
