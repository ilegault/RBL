"""Contract tests for EEL5000 current-monitor scale factor (ADR 0007)."""
import re
from pathlib import Path

from rbl.hardware.amp_monitor import ma_to_monitor, ma_unclamped, monitor_to_ma


def test_five_volts_is_ten_milliamps():
    """ADR 0007: The EEL5000 current monitor reads 1 V = 2 mA (not 10 mA).

    docs/adr/0007-current-monitor-scale-is-2-ma-per-volt.md.
    At 5.0 V monitor reading, current is 10.0 mA.
    At 20.0 mA current, monitor reading is 10.0 V.
    Unclamped conversion preserves negative values: -10.0 V -> -20.0 mA.
    """
    assert monitor_to_ma(5.0) == 10.0
    assert ma_to_monitor(20.0) == 10.0
    assert ma_unclamped(-10.0) == -20.0


def test_the_scale_is_assigned_only_in_hardware_config():
    """CURRENT_MONITOR_MA_PER_VOLT must be assigned in hardware_config.py only."""
    pattern = re.compile(r"^\s*CURRENT_MONITOR_MA_PER_VOLT\s*=", re.MULTILINE)
    repo_root = Path(__file__).resolve().parent.parent
    src_rbl = repo_root / "src" / "rbl"
    matches = []
    for py_file in src_rbl.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if pattern.search(text):
            matches.append(py_file.relative_to(repo_root).as_posix())

    assert matches == ["src/rbl/config/hardware_config.py"]


def test_no_source_file_states_the_old_scale():
    """No source file under src/rbl/ may state the old 1 V = 10 mA scale."""
    pattern = re.compile(r"1\s*V\s*={1,2}\s*10\s*mA", re.IGNORECASE)
    repo_root = Path(__file__).resolve().parent.parent
    src_rbl = repo_root / "src" / "rbl"
    matches = []
    for py_file in src_rbl.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if pattern.search(text):
            matches.append(py_file.relative_to(repo_root).as_posix())

    assert matches == []
