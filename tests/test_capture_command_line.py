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


def test_capture_command_line_and_child_env():
    import os

    from rbl.services.capture_client import capture_child_env, capture_command_line

    assert capture_command_line(True, "RBL.exe", ["--camera", "fake"]) == [
        "RBL.exe", "--capture-process", "--camera", "fake"]
    assert capture_command_line(False, "py", ["--camera", "fake"]) == [
        "py", "-m", "rbl.main", "--capture-process", "--camera", "fake"]

    env = {"PYTHONPATH": "/a", "X": "1"}
    out = capture_child_env(False, "/parent", env)
    assert out["PYTHONPATH"] == "/parent" + os.pathsep + "/a"
    assert out["X"] == "1"
    assert env == {"PYTHONPATH": "/a", "X": "1"}
    assert capture_child_env(False, "/parent", {})["PYTHONPATH"] == "/parent"
    assert capture_child_env(True, "/parent", env) == env
    assert capture_child_env(True, "/parent", env) is not env
