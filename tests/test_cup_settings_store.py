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


# ---------------------------------------------------------------------------
# Ticket 02: validate_settings
# ---------------------------------------------------------------------------
import math  # noqa: E402

from rbl.config.cup_config import CUP_MOVE_CONFIRMATION_TIMEOUT_S  # noqa: E402
from rbl.config.cup_settings_store import (  # noqa: E402
    SettingsLoadWarning,
    validate_settings,
)

_T2 = 2 * CUP_MOVE_CONFIRMATION_TIMEOUT_S
_KEYS = (
    "arm_threshold_a",
    "release_threshold_a",
    "cycle_dwell_s",
    "cycle_period_s",
    "settle_window_s",
    "arm_debounce_s",
    "release_interval_s",
)


def _valid(**over):
    raw = dataclasses.asdict(CupSettings.defaults())
    raw.update(over)
    return raw


def _check(over, key, ok):
    settings, warnings = validate_settings(_valid(**over))
    default = getattr(CupSettings.defaults(), key)
    if ok:
        assert warnings == []
        assert getattr(settings, key) == over[key]
    else:
        assert [w.key for w in warnings] == [key]
        assert getattr(settings, key) == default
        assert warnings[0].fallback == default
        assert warnings[0].reason


def test_warning_fields_and_arm_threshold_warning():
    assert [f.name for f in dataclasses.fields(SettingsLoadWarning)] == [
        "key", "found", "reason", "fallback",
    ]
    with pytest.raises(dataclasses.FrozenInstanceError):
        SettingsLoadWarning("k", "f", "r", 1.0).key = "x"
    _, warnings = validate_settings(_valid(arm_threshold_a=5e-3))
    assert len(warnings) == 1
    w = warnings[0]
    assert w.key == "arm_threshold_a"
    assert w.reason
    assert w.fallback == CUP_ARM_THRESHOLD_A


def test_all_defaults_and_valid_roundtrip():
    s, w = validate_settings(_valid())
    assert s == CupSettings.defaults() and w == []


@pytest.mark.parametrize("v,ok", [
    (1e-9, True), (1e-3, True), (0.99e-9, False), (1.01e-3, False),
])
def test_arm_threshold_bounds(v, ok):
    # release must stay below arm for the 'ok' small case
    over = {"arm_threshold_a": v}
    if v < CUP_RELEASE_THRESHOLD_A:
        over["release_threshold_a"] = v / 2
    s, w = validate_settings(_valid(**over))
    assert (w == []) is ok
    assert (s.arm_threshold_a == v) is ok


@pytest.mark.parametrize("v,ok", [
    (CUP_ARM_THRESHOLD_A * 0.999, True),
    (CUP_ARM_THRESHOLD_A, False),
    (CUP_ARM_THRESHOLD_A * 1.001, False),
    (1e-12, True),
    (0.0, False),
    (-1e-7, False),
])
def test_release_threshold_bounds(v, ok):
    _check({"release_threshold_a": v}, "release_threshold_a", ok)


def test_release_compares_against_accepted_arm_not_raw():
    # arm invalid -> default arm 0.5 uA accepted; release 0.6 uA is above it
    s, w = validate_settings(_valid(arm_threshold_a=5.0, release_threshold_a=0.6e-6))
    assert [x.key for x in w] == ["arm_threshold_a", "release_threshold_a"]
    assert s.arm_threshold_a == CUP_ARM_THRESHOLD_A
    assert s.release_threshold_a == CUP_RELEASE_THRESHOLD_A


@pytest.mark.parametrize("v,ok", [
    (3.0 + _T2, True),                      # exactly dwell + 2T
    (3.0 + _T2 - 1e-6, False),
    (604800.0, True),
    (604800.0 + 1e-3, False),
])
def test_cycle_period_bounds(v, ok):
    _check({"cycle_period_s": v}, "cycle_period_s", ok)


@pytest.mark.parametrize("v,ok", [
    (CUP_SETTLE_WINDOW_S + 1e-6, True),
    (CUP_SETTLE_WINDOW_S, False),
    (CUP_CYCLE_PERIOD_S - _T2 - 1e-6, True),
    (CUP_CYCLE_PERIOD_S - _T2, False),
])
def test_cycle_dwell_bounds(v, ok):
    _check({"cycle_dwell_s": v}, "cycle_dwell_s", ok)


@pytest.mark.parametrize("v,ok", [
    (0.0, True), (CUP_CYCLE_DWELL_S - 1e-6, True),
    (CUP_CYCLE_DWELL_S, False), (-1e-6, False),
])
def test_settle_window_bounds(v, ok):
    _check({"settle_window_s": v}, "settle_window_s", ok)


@pytest.mark.parametrize("key", ["arm_debounce_s", "release_interval_s"])
@pytest.mark.parametrize("v,ok", [
    (1e-6, True), (60.0, True), (0.0, False), (60.0 + 1e-6, False),
])
def test_debounce_and_interval_bounds(key, v, ok):
    _check({key: v}, key, ok)


def test_period_term_uses_imported_constant(monkeypatch):
    import rbl.config.cup_settings_store as mod
    monkeypatch.setattr(mod, "CUP_MOVE_CONFIRMATION_TIMEOUT_S", 10.0)
    _, w = validate_settings(_valid(cycle_dwell_s=3.0, cycle_period_s=3.0 + _T2))
    assert [x.key for x in w] == ["cycle_period_s"]


def test_dwell_and_period_both_invalid_two_warnings():
    s, w = validate_settings(_valid(cycle_dwell_s=-5.0, cycle_period_s=1.0))
    assert [x.key for x in w] == ["cycle_dwell_s", "cycle_period_s"]
    assert s.cycle_dwell_s == CUP_CYCLE_DWELL_S
    assert s.cycle_period_s == CUP_CYCLE_PERIOD_S


def test_warnings_follow_validation_order():
    bad = {k: -1.0 for k in _KEYS}
    _, w = validate_settings(bad)
    assert [x.key for x in w] == list(_KEYS)


def test_valid_coupled_pair_lowered_together_is_accepted():
    s, w = validate_settings(_valid(cycle_dwell_s=0.5, settle_window_s=0.2))
    assert w == [] and s.cycle_dwell_s == 0.5 and s.settle_window_s == 0.2


def test_never_raises_on_hostile_values():
    for bad in ("abc", None, float("nan"), float("inf"), -float("inf"), -3.0,
                [1], {}, True, object()):
        for key in _KEYS:
            s, w = validate_settings(_valid(**{key: bad}))
            assert [x.key for x in w] == [key], (key, bad)
            assert getattr(s, key) == getattr(CupSettings.defaults(), key)
            assert isinstance(w[0].found, str)


def test_never_raises_on_non_dict_inputs():
    for raw in (None, [], "x", 3, {}):
        s, w = validate_settings(raw)
        assert s == CupSettings.defaults()


def test_missing_keys_default_without_warning():
    s, w = validate_settings({})
    assert s == CupSettings.defaults() and w == []
    s, w = validate_settings({"arm_threshold_a": 2e-6})
    assert w == [] and s.arm_threshold_a == 2e-6
    assert s.release_threshold_a == CUP_RELEASE_THRESHOLD_A


def test_unknown_keys_ignored_silently():
    s, w = validate_settings(_valid(_README="hello", other=1))
    assert w == [] and s == CupSettings.defaults()


def test_ints_accepted_and_nan_found_text():
    s, w = validate_settings(_valid(cycle_period_s=600))
    assert w == [] and s.cycle_period_s == 600.0
    _, w = validate_settings(_valid(arm_debounce_s=float("nan")))
    assert math.isnan(float(w[0].found))


# ---------------------------------------------------------------------------
# Ticket 03: load_settings and save_settings
# ---------------------------------------------------------------------------
import json  # noqa: E402

from rbl.config.cup_settings_store import load_settings, save_settings  # noqa: E402


def test_autouse_fixture_redirects_store_path():
    """Ensure autouse fixture in conftest redirects STORE_PATH away from ~/.config/rbl."""
    assert store.STORE_PATH.name == "cup_settings.json"
    parent_str = str(store.STORE_PATH.parent).lower()
    store_str = str(store.STORE_PATH).lower()
    assert "config" not in parent_str or "tmp" in store_str
    assert store.STORE_PATH != CUP_SETTINGS_STORE


def test_load_settings_missing_file(tmp_path, monkeypatch):
    p = tmp_path / "nonexistent" / "cup_settings.json"
    monkeypatch.setattr(store, "STORE_PATH", p)
    settings, warnings = load_settings()
    assert settings == CupSettings.defaults()
    assert warnings == []


def test_load_settings_invalid_json(tmp_path, monkeypatch):
    p = tmp_path / "corrupt.json"
    p.write_text("not valid json {", encoding="utf-8")
    monkeypatch.setattr(store, "STORE_PATH", p)
    settings, warnings = load_settings()
    assert settings == CupSettings.defaults()
    assert len(warnings) == 1
    w = warnings[0]
    assert w.key == "_file"
    assert str(p) in w.reason


def test_load_settings_partial_valid_and_invalid(tmp_path, monkeypatch):
    p = tmp_path / "partial.json"
    data = {
        "arm_threshold_a": 10e-6,
        "release_threshold_a": 5e-6,
        "settle_window_s": 0.5,
        "cycle_dwell_s": 2.0,
        "cycle_period_s": 100.0,
        "arm_debounce_s": -99.0,  # invalid: must be > 0 and <= 60
    }
    p.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(store, "STORE_PATH", p)
    settings, warnings = load_settings()
    assert settings.arm_threshold_a == 10e-6
    assert settings.release_threshold_a == 5e-6
    assert settings.settle_window_s == 0.5
    assert settings.cycle_dwell_s == 2.0
    assert settings.cycle_period_s == 100.0
    assert settings.arm_debounce_s == CUP_ARM_DEBOUNCE_S
    assert len(warnings) == 1
    assert warnings[0].key == "arm_debounce_s"


def test_load_settings_never_raises(tmp_path, monkeypatch):
    # Directory path instead of file, causing OS error on read
    p = tmp_path / "adir"
    p.mkdir()
    monkeypatch.setattr(store, "STORE_PATH", p)
    settings, warnings = load_settings()
    assert settings == CupSettings.defaults()
    assert len(warnings) == 1
    assert warnings[0].key == "_file"


def test_save_settings_leaves_exactly_one_file(tmp_path, monkeypatch):
    store_dir = tmp_path / "settings_dir"
    store_file = store_dir / "cup_settings.json"
    monkeypatch.setattr(store, "STORE_PATH", store_file)

    settings = CupSettings.defaults()
    ok = save_settings(settings)
    assert ok is True

    # Assert exactly one file in directory (no temporary leftover files)
    files = list(store_dir.iterdir())
    assert len(files) == 1
    assert files[0].name == "cup_settings.json"


def test_save_settings_unwritable_location(tmp_path, monkeypatch, caplog):
    # Point at a path whose parent cannot be created because a regular file is in the way
    blocking_file = tmp_path / "blocker"
    blocking_file.write_text("block", encoding="utf-8")
    unwritable_path = blocking_file / "sub" / "cup_settings.json"
    monkeypatch.setattr(store, "STORE_PATH", unwritable_path)

    settings = CupSettings.defaults()
    ok = save_settings(settings)
    assert ok is False


def test_save_and_load_readme_and_roundtrip(tmp_path, monkeypatch):
    store_file = tmp_path / "test_roundtrip.json"
    monkeypatch.setattr(store, "STORE_PATH", store_file)

    custom_settings = CupSettings(
        arm_threshold_a=2e-6,
        release_threshold_a=1e-6,
        settle_window_s=0.8,
        cycle_period_s=200.0,
        cycle_dwell_s=15.0,
        arm_debounce_s=2.5,
        release_interval_s=5.0,
    )
    assert save_settings(custom_settings) is True

    # Check written file contents directly
    raw = json.loads(store_file.read_text(encoding="utf-8"))
    assert "_README" in raw
    assert raw["_README"] == (
        "RBL rewrites this file whenever a setting changes on the Faraday Cup tab. "
        "Edit it only while RBL is closed."
    )
    for field_name in dataclasses.asdict(custom_settings):
        assert field_name in raw
        assert raw[field_name] == getattr(custom_settings, field_name)

    # Load back
    loaded_settings, warnings = load_settings()
    assert warnings == []
    assert loaded_settings == custom_settings
