"""
Tests for Faraday Cup acquisition settings dataclass and path definition.
Ticket 01 of cup-settings.
"""
import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

import rbl.config.cup_settings_store as store
from rbl.config.cup_config import (
    CUP_ARM_DEBOUNCE_S,
    CUP_ARM_THRESHOLD_A,
    CUP_CYCLE_DWELL_S,
    CUP_CYCLE_PERIOD_S,
    CUP_RELEASE_INTERVAL_S,
    CUP_RELEASE_THRESHOLD_A,
    CUP_SETTLE_WINDOW_S,
)
from rbl.config.cup_settings_store import CupSettings
from rbl.config.paths import CONFIG_DIR, CUP_SETTINGS_STORE


def test_cup_settings_frozen_dataclass_fields():
    expected_fields = {
        "arm_threshold_a",
        "release_threshold_a",
        "settle_window_s",
        "cycle_period_s",
        "cycle_dwell_s",
        "arm_debounce_s",
        "release_interval_s",
    }
    field_names = {f.name for f in dataclasses.fields(CupSettings)}
    assert field_names == expected_fields

    for f in dataclasses.fields(CupSettings):
        assert f.type is float

    settings = CupSettings.defaults()
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.arm_threshold_a = 1.0


def test_cup_settings_defaults():
    defaults = CupSettings.defaults()
    assert defaults.arm_threshold_a == CUP_ARM_THRESHOLD_A
    assert defaults.release_threshold_a == CUP_RELEASE_THRESHOLD_A
    assert defaults.settle_window_s == CUP_SETTLE_WINDOW_S
    assert defaults.cycle_period_s == CUP_CYCLE_PERIOD_S
    assert defaults.cycle_dwell_s == CUP_CYCLE_DWELL_S
    assert defaults.arm_debounce_s == CUP_ARM_DEBOUNCE_S
    assert defaults.release_interval_s == CUP_RELEASE_INTERVAL_S


def test_no_default_literals_in_module():
    """Ensure no numeric defaults are hardcoded as literals in cup_settings_store.py."""
    source_file = Path(store.__file__)
    source_text = source_file.read_text(encoding="utf-8")
    tree = ast.parse(source_text)

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            assert node.value not in {
                CUP_ARM_THRESHOLD_A,
                CUP_RELEASE_THRESHOLD_A,
                CUP_CYCLE_PERIOD_S,
            }, f"Found literal {node.value} in {source_file.name}, must import from cup_config"


def test_cup_settings_store_path():
    assert CUP_SETTINGS_STORE.name == "cup_settings.json"
    assert CUP_SETTINGS_STORE.parent == CONFIG_DIR


def test_module_purity_no_pyside_no_clock_no_open():
    source_text = Path(store.__file__).read_text(encoding="utf-8")
    assert "import PySide6" not in source_text
    assert "from PySide6" not in source_text
    assert "open(" not in source_text
    assert "time.time" not in source_text
    assert "time.monotonic" not in source_text


def test_module_docstring_why_this_exists():
    doc = inspect.getdoc(store)
    assert doc is not None
    assert "WHY THIS EXISTS" in doc
    assert "PyInstaller" in doc
    doc_lower = doc.lower()
    assert "rebuild" in doc_lower
    assert "beam" in doc_lower
