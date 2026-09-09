"""
test_layering.py
Enforce that RBL module imports flow downward through the layer stack only.

Layer order (import direction must be strictly downward):
    config < hardware < state < services < gui

Three violations remain that require non-trivial restructuring and are
tracked in docs/IMPROVE_CODEBASE_ARCHITECTURE.md §1.4:
  - hardware/scope_worker.py and hardware/vacuum_worker.py import
    rbl.state.snapshots — to be fixed by moving snapshots to rbl/snapshots.py.
  - state/funcgen_control.py imports rbl.services.ramp_engine — to be fixed
    by moving RampEngine to rbl/hardware/.

Every other upward import is a bug. This test prevents new ones from being
added.
"""
import pathlib
import sys

# Add repo root to path so scripts/ is importable.
_REPO_ROOT = pathlib.Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.check_layers import (
    CONVERSION_HELPERS,
    DEFAULT_RATCHET_FILE,
    check_private_access_ratchet,
    find_gui_conversion_violations,
    find_private_accesses,
    find_violations,
)

# Tracked violations that are not yet fixed; keyed on file path using
# forward slashes (normalised in find_violations).
_KNOWN_VIOLATIONS = {
    'src/rbl/hardware/scope_worker.py',
    'src/rbl/hardware/vacuum_worker.py',
    'src/rbl/state/funcgen_control.py',
}


def test_no_new_layer_violations():
    """Fail if any upward import is added that isn't in the known list."""
    all_violations = find_violations(_REPO_ROOT / 'src' / 'rbl')
    new = [
        msg for key, msg in all_violations
        if not any(key.endswith(k.replace('/', pathlib.sep))
                   or key.endswith(k)
                   for k in _KNOWN_VIOLATIONS)
    ]
    assert new == [], (
        "New layer violation(s) detected — imports must flow config → hardware "
        "→ state → services → gui:\n" + "\n".join(new)
    )


def test_gui_layer_cannot_call_or_import_conversion_helpers():
    """Ensure the GUI layer does not import or call unit-conversion helpers.

    Raw instrument readings become physical units in exactly one place (the
    snapshot layer), and GUI tabs render pre-converted snapshots.
    """
    violations = find_gui_conversion_violations(_REPO_ROOT / 'src' / 'rbl')
    assert violations == [], (
        "GUI layer conversion violation(s) detected — GUI must render pre-converted "
        "snapshots, not call conversion helpers directly:\n"
        + "\n".join(msg for _key, msg in violations)
    )


def test_gui_conversion_check_detects_violations(tmp_path):
    """Verify that find_gui_conversion_violations catches forbidden imports and calls."""
    fake_rbl = tmp_path / "src" / "rbl"
    gui_dir = fake_rbl / "gui"
    gui_dir.mkdir(parents=True)

    bad_file = gui_dir / "bad_tab.py"
    bad_file.write_text(
        "from rbl.hardware.amp_monitor import monitor_to_kv\n"
        "def render(raw):\n"
        "    return monitor_to_kv(raw)\n",
        encoding="utf-8"
    )

    violations = find_gui_conversion_violations(fake_rbl)
    assert len(violations) >= 2
    assert any("imports conversion helper 'monitor_to_kv'" in msg for _, msg in violations)
    assert any("calls conversion helper 'monitor_to_kv'" in msg for _, msg in violations)


def test_private_access_ratchet_does_not_exceed_stored_figure():
    """Verify that private-attribute accesses in Qt test modules do not exceed the ratchet."""
    ratchet_file = _REPO_ROOT / DEFAULT_RATCHET_FILE
    count, ratchet, accesses = check_private_access_ratchet(
        tests_root=_REPO_ROOT / 'tests',
        ratchet_file=ratchet_file,
    )
    assert count <= ratchet, (
        f"Private-attribute access count in Qt test modules rose from {ratchet} to {count}. "
        f"Fix new private accesses or lower the ratchet if accesses were removed:\n"
        + "\n".join(msg for _key, msg in accesses[:20])
    )


def test_private_access_ratchet_detects_violations(tmp_path):
    """Verify that adding a private access in a Qt test module fails the ratchet check."""
    fake_tests = tmp_path / "tests"
    fake_tests.mkdir(parents=True)
    fake_ratchet_file = tmp_path / "private_access_ratchet.txt"
    fake_ratchet_file.write_text("1\n", encoding="utf-8")

    # A Qt-dependent test module with 2 private attribute accesses
    qt_test_file = fake_tests / "test_fake_qt.py"
    qt_test_file.write_text(
        "from PySide6.QtWidgets import QWidget\n"
        "def test_something():\n"
        "    w = QWidget()\n"
        "    x = w._private_var\n"
        "    w._private_method()\n",
        encoding="utf-8",
    )

    count, ratchet, accesses = check_private_access_ratchet(
        tests_root=fake_tests,
        ratchet_file=fake_ratchet_file,
    )
    assert count == 2
    assert ratchet == 1
    assert count > ratchet
    assert len(accesses) == 2
    assert any("_private_var" in msg for _, msg in accesses)
    assert any("_private_method" in msg for _, msg in accesses)


def test_private_access_ratchet_passes_when_at_or_below_ratchet(tmp_path):
    """Verify that when private access count is <= ratchet (e.g. lowered), check passes."""
    fake_tests = tmp_path / "tests"
    fake_tests.mkdir(parents=True)
    fake_ratchet_file = tmp_path / "private_access_ratchet.txt"
    fake_ratchet_file.write_text("2\n", encoding="utf-8")

    qt_test_file = fake_tests / "test_fake_qt.py"
    qt_test_file.write_text(
        "from PySide6.QtWidgets import QWidget\n"
        "def test_something():\n"
        "    w = QWidget()\n"
        "    x = w._private_var\n",
        encoding="utf-8",
    )

    count, ratchet, accesses = check_private_access_ratchet(
        tests_root=fake_tests,
        ratchet_file=fake_ratchet_file,
    )
    assert count == 1
    assert ratchet == 2
    assert count <= ratchet


def test_private_access_check_ignores_non_qt_test_modules(tmp_path):
    """Verify that non-Qt test modules are not scanned for private attribute accesses."""
    fake_tests = tmp_path / "tests"
    fake_tests.mkdir(parents=True)

    non_qt_test = fake_tests / "test_pure_math.py"
    non_qt_test.write_text(
        "import math\n"
        "def test_math():\n"
        "    x = math._test_private\n",
        encoding="utf-8",
    )

    accesses = find_private_accesses(fake_tests)
    assert accesses == []

