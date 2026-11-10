"""
Contract tests for capture package import boundaries.

WHY THIS EXISTS
---------------
ADR 0008 decision 3 requires that the Capture Process imports no Qt.
Furthermore, the Capture Process must remain decoupled from the main application's
GUI, services, and state layers so it can be run independently and replaced by a
compiled program if needed. No module outside rbl.capture may import rbl.capture
except rbl.main (which dispatches --capture-process).
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_RBL = REPO_ROOT / "src" / "rbl"

FORBIDDEN_PREFIXES_IN_CAPTURE = (
    "PySide6",
    "rbl.gui",
    "rbl.services",
    "rbl.state",
)

CAPTURE_HARDWARE_FILES = {
    "ser_format.py",
    "fits_format.py",
    "capture_protocol.py",
    "preview_stretch.py",
}


def _collect_imported_modules(file_path: Path) -> list[str]:
    """Parse a python file with AST and return all module names imported."""
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module)
    return modules


def scan_capture_import_violations(root: Path) -> list[str]:
    """Scan source tree rooted at `root` (containing `rbl`) for import violations."""
    violations: list[str] = []

    # Check files in rbl/capture and the designated hardware files
    capture_dir = root / "capture"
    hardware_dir = root / "hardware"

    target_files: list[Path] = []
    if capture_dir.exists():
        target_files.extend(capture_dir.rglob("*.py"))
    if hardware_dir.exists():
        for f in hardware_dir.glob("*.py"):
            if f.name in CAPTURE_HARDWARE_FILES:
                target_files.append(f)

    for p in target_files:
        imported = _collect_imported_modules(p)
        for mod in imported:
            for forbidden in FORBIDDEN_PREFIXES_IN_CAPTURE:
                if mod == forbidden or mod.startswith(f"{forbidden}."):
                    violations.append(f"{p}: forbidden import {mod!r}")

    # Check that no file in src/rbl outside src/rbl/capture/ imports rbl.capture except main.py
    for p in root.rglob("*.py"):
        if capture_dir.exists() and capture_dir in p.parents:
            continue
        if p.name == "main.py" and p.parent == root:
            continue
        imported = _collect_imported_modules(p)
        for mod in imported:
            if mod == "rbl.capture" or mod.startswith("rbl.capture."):
                violations.append(f"{p}: outside rbl.capture, forbidden import of {mod!r}")

    return violations


def test_capture_import_rules() -> None:
    violations = scan_capture_import_violations(SRC_RBL)
    assert violations == [], "\n".join(violations)


def test_capture_import_rules_catches_violations(tmp_path: Path) -> None:
    # Set up mock rbl structure
    mock_rbl = tmp_path / "rbl"
    mock_capture = mock_rbl / "capture"
    mock_hardware = mock_rbl / "hardware"
    mock_gui = mock_rbl / "gui"

    mock_capture.mkdir(parents=True)
    mock_hardware.mkdir(parents=True)
    mock_gui.mkdir(parents=True)

    # 1. Violating file inside capture importing PySide6
    bad_capture = mock_capture / "bad.py"
    bad_capture.write_text("import PySide6.QtWidgets", encoding="utf-8")

    # 2. Violating file in hardware importing rbl.gui
    bad_hw = mock_hardware / "capture_protocol.py"
    bad_hw.write_text("from rbl.gui import something", encoding="utf-8")

    # 3. Violating file outside capture importing rbl.capture
    bad_gui = mock_gui / "view.py"
    bad_gui.write_text("import rbl.capture.process", encoding="utf-8")

    # 4. Valid main.py importing rbl.capture
    main_py = mock_rbl / "main.py"
    main_py.write_text("import rbl.capture.process", encoding="utf-8")

    violations = scan_capture_import_violations(mock_rbl)
    assert len(violations) == 3
    assert any("PySide6.QtWidgets" in v for v in violations)
    assert any("rbl.gui" in v for v in violations)
    assert any("rbl.capture.process" in v for v in violations)
