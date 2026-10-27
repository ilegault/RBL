"""Command-line and path rules for the Capture Process."""
from pathlib import Path

from rbl.config.paths import zwo_sdk_dll_path

BUNDLE = Path("bundle")
REPO = Path("repo")


def test_zwo_sdk_dll_path_env_frozen_and_source():
    override = {"RBL_ZWO_SDK_DLL": "custom/ASICamera2.dll"}
    assert zwo_sdk_dll_path(True, BUNDLE, REPO, override) == Path("custom/ASICamera2.dll")
    assert zwo_sdk_dll_path(False, None, REPO, override) == Path("custom/ASICamera2.dll")
    assert zwo_sdk_dll_path(True, BUNDLE, REPO, {}) == BUNDLE / "ASICamera2.dll"
    assert zwo_sdk_dll_path(False, None, REPO, {}) == REPO / "vendor" / "zwo" / "ASICamera2.dll"
    empty = {"RBL_ZWO_SDK_DLL": ""}
    assert zwo_sdk_dll_path(True, BUNDLE, REPO, empty) == BUNDLE / "ASICamera2.dll"
    assert zwo_sdk_dll_path(False, None, REPO, empty) == REPO / "vendor" / "zwo" / "ASICamera2.dll"
