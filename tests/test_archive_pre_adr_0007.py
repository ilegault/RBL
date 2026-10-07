"""
test_archive_pre_adr_0007.py
Tests for scripts/archive_pre_adr_0007.py.

WHY THIS EXISTS
---------------
Before ADR 0007, amplifier currents were computed with 1 V = 10 mA instead of
the true 1 V = 2 mA scale. scripts/archive_pre_adr_0007.py archives every store
written under the old scale into an archive folder with a README explaining why.
It moves files aside, never rescales, and never deletes data.
These tests verify dry runs, apply runs, refusal on second runs, missing stores,
and that the planning core is pure and reads only its arguments.
"""
from __future__ import annotations

import pathlib
import sys
from pathlib import Path

# Add repo root to sys.path so scripts/ is importable
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ruff: noqa: E402
import pytest
from scripts.archive_pre_adr_0007 import (
    apply_archive,
    main,
    plan_archive,
)


def _setup_full_environment(
    log_root: Path, config_dir: Path
) -> dict[str, bytes]:
    """Create a complete set of pre-ADR-0007 data and config stores."""
    data_dir = log_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    files_content: dict[str, bytes] = {
        "trip_history": b'{"event": "trip", "ma": 60.0}\n',
        "dynamic_adjustment": b'{"event": "adj", "c_pf": 1500.0}\n',
        "conditioning": b'{"event": "cond", "v": 1000.0}\n',
        "cal_file": b"step,measured_ma\n1,10.0\n",
        "char_file": b'{"capacitance_pf": 1600.0}\n',
        "load_cal": b'{"X1": 1550.0, "Y1": 1600.0}\n',
        "spikes_csv": b"time_iso,plate_position,peak_ma\n2026-10-01T00:00:00,X1,15.0\n",
        "spike_wf": b"t_s,raw_v\n0.0,1.5\n",
        "unrelated_vacuum": b"iso_time,pressure_torr\n2026-10-01T00:00:00,1.2e-7\n",
    }

    # Data stores
    (data_dir / "trip_history.jsonl").write_bytes(files_content["trip_history"])
    (data_dir / "dynamic_adjustment_history.jsonl").write_bytes(
        files_content["dynamic_adjustment"]
    )
    (data_dir / "conditioning_history.jsonl").write_bytes(
        files_content["conditioning"]
    )

    cal_dir = data_dir / "calibration"
    cal_dir.mkdir(parents=True, exist_ok=True)
    (cal_dir / "cal_run_1.csv").write_bytes(files_content["cal_file"])

    char_dir = data_dir / "load_characterization"
    char_dir.mkdir(parents=True, exist_ok=True)
    (char_dir / "char_run_1.json").write_bytes(files_content["char_file"])

    # Config store
    (config_dir / "load_calibration.json").write_bytes(files_content["load_cal"])

    # Nested spikes under sessions/s1
    session_dir = log_root / "sessions" / "s1"
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "spikes.csv").write_bytes(files_content["spikes_csv"])

    spikes_wf_dir = session_dir / "spikes"
    spikes_wf_dir.mkdir(parents=True, exist_ok=True)
    (spikes_wf_dir / "wf_1.csv").write_bytes(files_content["spike_wf"])

    # Unrelated store that must NOT be moved
    vacuum_dir = data_dir / "vacuum"
    vacuum_dir.mkdir(parents=True, exist_ok=True)
    (vacuum_dir / "v.csv").write_bytes(files_content["unrelated_vacuum"])

    return files_content


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    """Capture relative paths and content of all files under a root."""
    snapshot: dict[str, bytes] = {}
    if not root.exists():
        return snapshot
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(root).as_posix())
            snapshot[rel] = p.read_bytes()
    return snapshot


def test_dry_run_moves_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    log_root = tmp_path / "Desktop" / "RBL_log"
    config_dir = tmp_path / ".config" / "rbl"
    _setup_full_environment(log_root, config_dir)

    log_before = _snapshot_tree(log_root)
    cfg_before = _snapshot_tree(config_dir)

    exit_code = main(["--log-root", str(log_root), "--config-dir", str(config_dir)])
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert "would move" in captured
    assert "dry run: nothing moved; run again with --apply" in captured

    assert _snapshot_tree(log_root) == log_before
    assert _snapshot_tree(config_dir) == cfg_before


def test_apply_moves_exactly_the_listed_stores_and_writes_both_readmes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    log_root = tmp_path / "Desktop" / "RBL_log"
    config_dir = tmp_path / ".config" / "rbl"
    contents = _setup_full_environment(log_root, config_dir)

    exit_code = main(
        ["--log-root", str(log_root), "--config-dir", str(config_dir), "--apply"]
    )
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert "moved" in captured

    data_archive = log_root / "data" / "archive" / "pre-2026-10-07-current-scale"
    cfg_archive = config_dir / "archive" / "pre-2026-10-07-current-scale"

    # Verify byte-identical content at destinations
    assert (data_archive / "trip_history.jsonl").read_bytes() == contents["trip_history"]
    assert (
        data_archive / "dynamic_adjustment_history.jsonl"
    ).read_bytes() == contents["dynamic_adjustment"]
    assert (
        data_archive / "conditioning_history.jsonl"
    ).read_bytes() == contents["conditioning"]
    assert (
        data_archive / "calibration" / "cal_run_1.csv"
    ).read_bytes() == contents["cal_file"]
    assert (
        data_archive / "load_characterization" / "char_run_1.json"
    ).read_bytes() == contents["char_file"]
    assert (
        cfg_archive / "load_calibration.json"
    ).read_bytes() == contents["load_cal"]

    # Original files should not exist at old locations
    data_dir = log_root / "data"
    assert not (data_dir / "trip_history.jsonl").exists()
    assert not (data_dir / "dynamic_adjustment_history.jsonl").exists()
    assert not (data_dir / "conditioning_history.jsonl").exists()
    assert not (data_dir / "calibration").exists()
    assert not (data_dir / "load_characterization").exists()
    assert not (config_dir / "load_calibration.json").exists()

    # Spikes renamed in place under session folder
    session_dir = log_root / "sessions" / "s1"
    assert (session_dir / "spikes.pre-adr-0007.csv").is_file()
    assert (
        session_dir / "spikes.pre-adr-0007.csv"
    ).read_bytes() == contents["spikes_csv"]
    assert not (session_dir / "spikes.csv").exists()

    assert (session_dir / "spikes.pre-adr-0007").is_dir()
    assert (
        session_dir / "spikes.pre-adr-0007" / "wf_1.csv"
    ).read_bytes() == contents["spike_wf"]
    assert not (session_dir / "spikes").exists()

    # Both READMEs exist and contain '5x too high'
    data_readme = data_archive / "README.txt"
    cfg_readme = cfg_archive / "README.txt"
    assert data_readme.is_file()
    assert cfg_readme.is_file()
    assert "5x too high" in data_readme.read_text(encoding="utf-8")
    assert "5x too high" in cfg_readme.read_text(encoding="utf-8")

    # Unrelated file was not moved
    assert (data_dir / "vacuum" / "v.csv").is_file()
    assert (data_dir / "vacuum" / "v.csv").read_bytes() == contents["unrelated_vacuum"]


def test_a_second_apply_refuses_and_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    log_root = tmp_path / "Desktop" / "RBL_log"
    config_dir = tmp_path / ".config" / "rbl"
    _setup_full_environment(log_root, config_dir)

    # First apply succeeds
    assert (
        main(["--log-root", str(log_root), "--config-dir", str(config_dir), "--apply"])
        == 0
    )
    capsys.readouterr()  # clear buffer

    log_snapshot_1 = _snapshot_tree(log_root)
    cfg_snapshot_1 = _snapshot_tree(config_dir)

    # Second apply refuses
    exit_code = main(
        ["--log-root", str(log_root), "--config-dir", str(config_dir), "--apply"]
    )
    captured = capsys.readouterr().out

    assert exit_code == 1
    assert "refusing to run twice" in captured

    assert _snapshot_tree(log_root) == log_snapshot_1
    assert _snapshot_tree(config_dir) == cfg_snapshot_1


def test_missing_stores_are_skipped_not_errors(tmp_path: Path):
    log_root = tmp_path / "Desktop" / "RBL_log"
    config_dir = tmp_path / ".config" / "rbl"
    data_dir = log_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    trip_path = data_dir / "trip_history.jsonl"
    trip_path.write_bytes(b'{"event": "trip"}\n')

    exit_code = main(
        ["--log-root", str(log_root), "--config-dir", str(config_dir), "--apply"]
    )
    assert exit_code == 0

    data_archive = data_dir / "archive" / "pre-2026-10-07-current-scale"
    cfg_archive = config_dir / "archive" / "pre-2026-10-07-current-scale"

    assert (data_archive / "trip_history.jsonl").is_file()
    assert not (data_dir / "trip_history.jsonl").exists()
    assert (data_archive / "README.txt").is_file()
    assert (cfg_archive / "README.txt").is_file()
    assert not (data_archive / "calibration").exists()
    assert not (cfg_archive / "load_calibration.json").exists()


def test_plan_archive_reads_only_its_arguments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    log_root = tmp_path / "custom_log"
    config_dir = tmp_path / "custom_cfg"
    _setup_full_environment(log_root, config_dir)

    def _poison_home() -> Path:
        raise RuntimeError("plan_archive called Path.home()!")

    monkeypatch.setattr(Path, "home", _poison_home)

    pairs = plan_archive(log_root, config_dir)

    # Ensure expected pairs are present
    sources = [src for src, _ in pairs]
    assert log_root / "data" / "trip_history.jsonl" in sources
    assert log_root / "data" / "dynamic_adjustment_history.jsonl" in sources
    assert log_root / "data" / "conditioning_history.jsonl" in sources
    assert log_root / "data" / "calibration" in sources
    assert log_root / "data" / "load_characterization" in sources
    assert config_dir / "load_calibration.json" in sources
    assert log_root / "sessions" / "s1" / "spikes.csv" in sources
    assert log_root / "sessions" / "s1" / "spikes" in sources

    # Unrelated files must NOT be in sources
    assert log_root / "data" / "vacuum" / "v.csv" not in sources
    assert log_root / "data" / "vacuum" not in sources


def test_apply_archive_function_directly(tmp_path: Path):
    src_file = tmp_path / "src" / "sample.txt"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text("hello")

    dst_file = tmp_path / "archive" / "sample.txt"
    readme_dir = tmp_path / "archive"

    apply_archive([(src_file, dst_file)], [readme_dir])

    assert dst_file.read_text() == "hello"
    assert not src_file.exists()
    assert (readme_dir / "README.txt").is_file()
    assert "5x too high" in (readme_dir / "README.txt").read_text(encoding="utf-8")

