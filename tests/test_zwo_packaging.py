"""Ticket 76: the ZWO DLL ships in the one-folder build and tools/zwo_bench.py exists.

WHY THIS EXISTS
    The DLL cannot be committed and the build cannot run in CI, so what CI can
    prove is: the spec file still names the DLL and zwoasi, the DLL stays
    gitignored, the README the developer follows exists, and the bench tool is
    standalone (it must run on a PC with no ``rbl`` and no ``zwoasi``) and its
    two pure summary functions are right.
"""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "zwo_bench.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("zwo_bench_under_test", TOOL)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bench_tool_is_standalone():
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] != "rbl"
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "rbl"
    for node in tree.body:  # module level only
        if isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] != "zwoasi" for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "zwoasi"

    result = subprocess.run(
        [sys.executable, "tools/zwo_bench.py", "--help"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--sdk-dll" in result.stdout


def test_levels_report_and_fps_summary():
    tool = _load_tool()

    twelve_bit = np.array([0, 16, 32, 4080] * 10, dtype=np.uint16)
    rep = tool.levels_report(twelve_bit)
    assert rep["distinct_levels"] == 4
    assert list(rep["low_nibble_levels"]) == [0]

    ramp = np.arange(64, dtype=np.uint16)
    rep = tool.levels_report(ramp)
    assert rep["distinct_levels"] == 64
    assert len(rep["low_nibble_levels"]) == 16

    s = tool.fps_summary(500, 10.0, 3)
    assert s["fps"] == 50.0
    assert s["dropped"] == 3
    assert s["frames"] == 500


def test_spec_bundles_zwoasi_and_the_dll():
    spec = (REPO / "rbl.spec").read_text(encoding="utf-8")
    hidden = spec.split("all_hiddenimports = [", 1)[1].split("\n]", 1)[0]
    assert '"zwoasi"' in hidden
    for segment in ('"vendor"', '"zwo"', '"ASICamera2.dll"'):
        assert segment in spec

    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "vendor/zwo/*.dll" in [line.strip() for line in gitignore]

    readme = REPO / "vendor" / "zwo" / "README.md"
    assert readme.is_file()
    assert "SHA-256:" in readme.read_text(encoding="utf-8")
