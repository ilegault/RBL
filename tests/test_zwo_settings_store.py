"""zwo_settings_store: defaults, validation, round trip, and test isolation."""
import ast
import dataclasses
import json
from pathlib import Path

import pytest

import rbl.config.zwo_settings_store as store
from rbl.config import capture_config as cc
from rbl.config import paths
from rbl.config.zwo_settings_store import (
    SettingsLoadWarning,
    ZwoSettings,
    load_settings,
    save_settings,
    validate_settings,
)


def test_defaults():
    d = ZwoSettings.defaults()
    assert d.camera_kind == "usb"
    assert d.exposure_us == cc.ZWO_EXPOSURE_US_DEFAULT
    assert d.gain == cc.ZWO_GAIN_DEFAULT
    assert d.bit_depth == cc.ZWO_BIT_DEPTH_DEFAULT
    assert d.roi is None
    assert d.keep_one_in_n == 1
    assert d.preview_mode == "auto"
    assert d.brightness == cc.PREVIEW_BRIGHTNESS_DEFAULT
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.gain = 1  # type: ignore[misc]


def test_store_path():
    assert paths.ZWO_SETTINGS_STORE == paths.CONFIG_DIR / "zwo_camera.json"
    assert paths.ZWO_DARKS_DIR == paths.DATA_DIR / "zwo_darks"


def test_empty_and_non_dict_give_defaults_without_warning():
    for raw in ({}, None, [], "x"):
        s, w = validate_settings(raw)  # type: ignore[arg-type]
        assert s == ZwoSettings.defaults() and w == []


def test_valid_round_trip_with_roi(tmp_path):
    path = tmp_path / "zwo.json"
    custom = ZwoSettings("zwo", 5000, 50, 8, (16, 32, 640, 480), 10, "linear", 2.5)
    assert save_settings(custom, path) is True
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["roi"] == {"x": 16, "y": 32, "width": 640, "height": 480}
    assert "_README" in on_disk
    loaded, warnings = load_settings(path)
    assert warnings == [] and loaded == custom
    assert [p.name for p in tmp_path.iterdir()] == ["zwo.json"]


def test_round_trip_full_sensor(tmp_path):
    path = tmp_path / "zwo.json"
    assert save_settings(ZwoSettings(), path)
    loaded, warnings = load_settings(path)
    assert warnings == [] and loaded == ZwoSettings()


_BAD = [
    ("camera_kind", "ccd"), ("camera_kind", 3),
    ("exposure_us", 0), ("exposure_us", 1.5), ("exposure_us", True), ("exposure_us", "x"),
    ("gain", -1), ("gain", 2.0), ("gain", None),
    ("bit_depth", 12), ("bit_depth", "16"),
    ("roi", {"x": 0, "y": 0, "width": 4, "height": 100}),
    ("roi", {"x": 0, "y": 0, "width": 100, "height": 1}),
    ("roi", {"x": 0, "y": 0, "width": 100}),
    ("roi", {"x": 0, "y": 0, "width": 100.0, "height": 10}),
    ("roi", [0, 0, 100, 10]),
    ("keep_one_in_n", 0), ("keep_one_in_n", cc.CAPTURE_KEEP_ONE_IN_N_MAX + 1),
    ("preview_mode", "gamma"), ("preview_mode", None),
    ("brightness", 0.1), ("brightness", 5.0), ("brightness", float("nan")),
    ("brightness", float("inf")), ("brightness", "1"), ("brightness", True),
]


@pytest.mark.parametrize("key,value", _BAD)
def test_invalid_key_gives_one_warning_and_default(key, value):
    s, w = validate_settings({key: value})
    assert [x.key for x in w] == [key]
    assert w[0].reason and isinstance(w[0].found, str)
    assert getattr(s, key) == getattr(ZwoSettings.defaults(), key)
    assert w[0].fallback == getattr(ZwoSettings.defaults(), key)
    assert dataclasses.replace(s, **{key: getattr(ZwoSettings.defaults(), key)}) == s


def test_boundaries_accepted():
    raw = {"exposure_us": 1, "gain": 0, "roi": {"x": 0, "y": 0, "width": 8, "height": 2},
           "keep_one_in_n": cc.CAPTURE_KEEP_ONE_IN_N_MAX,
           "brightness": cc.PREVIEW_BRIGHTNESS_MIN}
    s, w = validate_settings(raw)
    assert w == [] and s.roi == (0, 0, 8, 2)
    _, w = validate_settings({"brightness": cc.PREVIEW_BRIGHTNESS_MAX})
    assert w == []


def test_one_bad_key_leaves_good_keys():
    s, w = validate_settings({"gain": 7, "bit_depth": 12, "camera_kind": "zwo"})
    assert [x.key for x in w] == ["bit_depth"]
    assert s.gain == 7 and s.camera_kind == "zwo" and s.bit_depth == 16


def test_corrupt_file_gives_defaults_and_a_warning(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json {", encoding="utf-8")
    s, w = load_settings(path)
    assert s == ZwoSettings.defaults()
    assert len(w) == 1 and w[0].key == "_file" and str(path) in w[0].reason
    path.write_text("[1, 2]", encoding="utf-8")
    s, w = load_settings(path)
    assert s == ZwoSettings.defaults() and [x.key for x in w] == ["_file"]


def test_missing_file_gives_defaults(tmp_path):
    assert load_settings(tmp_path / "nope.json") == (ZwoSettings.defaults(), [])


def test_save_to_unwritable_location_returns_false(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    assert save_settings(ZwoSettings(), blocker / "sub" / "z.json") is False


def test_warning_fields():
    assert [f.name for f in dataclasses.fields(SettingsLoadWarning)] == [
        "key", "found", "reason", "fallback"]


def test_module_purity_no_pyside():
    text = Path(store.__file__).read_text(encoding="utf-8")
    assert "PySide6" not in text


def test_no_default_literals_in_module():
    tree = ast.parse(Path(store.__file__).read_text(encoding="utf-8"))
    forbidden = {cc.ZWO_EXPOSURE_US_DEFAULT, cc.ZWO_GAIN_DEFAULT, cc.ZWO_BIT_DEPTH_DEFAULT,
                 cc.PREVIEW_BRIGHTNESS_DEFAULT, cc.CAPTURE_KEEP_ONE_IN_N_DEFAULT}
    # 8 and 16 are the two legal bit depths, 1/0 are range ends; the defaults
    # themselves (20000, 100, 1.0) must come from capture_config.
    forbidden -= {16, 1, 1.0}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            assert node.value not in forbidden, node.value
    # the string defaults are not repeated as dataclass field defaults either
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ZwoSettings":
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                    assert not isinstance(stmt.value, ast.Constant) or stmt.value.value is None


def test_conftest_redirects_the_store():
    assert store.STORE_PATH != paths.ZWO_SETTINGS_STORE
    assert (Path.home() / ".config") not in store.STORE_PATH.parents
