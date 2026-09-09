"""
check_layers.py
Verify that RBL module imports flow downward only through the layer stack.

Layer order: config < hardware < state < services < gui

Run from the repo root:
    python scripts/check_layers.py

Exits 0 if clean, 1 if any violation is found.
Also wired into pytest via tests/test_layering.py.
"""
import ast
import pathlib
import sys

ORDER = {'config': 0, 'hardware': 1, 'state': 2, 'services': 3, 'gui': 4}

# Known remaining violations being tracked but not yet fixed.
# Remove entries from this set as each is resolved.
KNOWN_VIOLATIONS = {
    # 1.4-a: snapshots.py is imported by hardware workers; will be fixed
    # by moving snapshots to rbl/types/ or rbl/snapshots.py.
    'src/rbl/hardware/scope_worker.py',
    'src/rbl/hardware/vacuum_worker.py',
    # 1.4-b: state imports services (RampEngine); will be fixed by moving
    # RampEngine to rbl/hardware/.
    'src/rbl/state/funcgen_control.py',
}


# Unit-conversion helpers that convert raw instrument readings to physical units.
# The GUI layer (src/rbl/gui) is strictly forbidden from importing or calling these;
# physical units are produced in the snapshot layer, and GUI tabs render snapshots.
CONVERSION_HELPERS = {
    'monitor_to_kv',
    'monitor_to_ma',
    'ma_to_monitor',
    'ma_unclamped',
    'voltage_to_current',
    'samples_to_volts',
}


def find_violations(root: pathlib.Path = pathlib.Path('src/rbl')) -> list[tuple[str, str]]:
    bad = []
    for p in root.rglob('*.py'):
        rel = p.relative_to(root).parts
        # Layer is the immediate subdirectory of rbl/ regardless of root depth
        L = rel[0] if len(rel) > 1 else 'root'
        if L not in ORDER:
            continue
        try:
            tree = ast.parse(p.read_text(encoding='utf-8'))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            mods = (
                [n.module] if isinstance(n, ast.ImportFrom) and n.module
                else [a.name for a in n.names] if isinstance(n, ast.Import)
                else []
            )
            for m in mods:
                if not m or not m.startswith('rbl.'):
                    continue
                M = m.split('.')[1]
                if M in ORDER and ORDER[M] > ORDER[L]:
                    key = str(p).replace('\\', '/')
                    bad.append((key, f"{p}:{n.lineno}  {L} -> {m}"))
    return bad


def find_gui_conversion_violations(root: pathlib.Path = pathlib.Path('src/rbl')) -> list[tuple[str, str]]:
    """Scan GUI modules and return any imports or calls to forbidden unit conversion helpers."""
    bad = []
    for p in root.rglob('*.py'):
        rel = p.relative_to(root).parts
        L = rel[0] if len(rel) > 1 else 'root'
        if L != 'gui':
            continue
        try:
            tree = ast.parse(p.read_text(encoding='utf-8'))
        except SyntaxError:
            continue
        key = str(p).replace('\\', '/')
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                for alias in n.names:
                    if alias.name in CONVERSION_HELPERS:
                        bad.append((key, f"{p}:{n.lineno}  GUI layer imports conversion helper '{alias.name}'"))
            elif isinstance(n, ast.Import):
                for alias in n.names:
                    if alias.name in CONVERSION_HELPERS:
                        bad.append((key, f"{p}:{n.lineno}  GUI layer imports conversion helper '{alias.name}'"))
            elif isinstance(n, ast.Call):
                func_name = None
                if isinstance(n.func, ast.Name):
                    func_name = n.func.id
                elif isinstance(n.func, ast.Attribute):
                    func_name = n.func.attr
                if func_name in CONVERSION_HELPERS:
                    bad.append((key, f"{p}:{n.lineno}  GUI layer calls conversion helper '{func_name}'"))
    return bad


DEFAULT_RATCHET_FILE = pathlib.Path('tools/private_access_ratchet.txt')
QT_MODULE_PREFIXES = ('PySide6', 'PySide2', 'PyQt6', 'PyQt5', 'qtpy', 'pytestqt')


def is_qt_test_module(tree: ast.AST) -> bool:
    """Return True if the AST imports Qt bindings or Qt test utilities."""
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for alias in n.names:
                if alias.name.split('.')[0] in QT_MODULE_PREFIXES:
                    return True
        elif isinstance(n, ast.ImportFrom):
            if n.module and n.module.split('.')[0] in QT_MODULE_PREFIXES:
                return True
    return False


def find_private_accesses(root: pathlib.Path = pathlib.Path('tests')) -> list[tuple[str, str]]:
    """Scan Qt-dependent test modules and return all private-attribute accesses."""
    bad = []
    paths = [root] if root.is_file() else sorted(root.rglob('test_*.py'))
    for p in paths:
        try:
            tree = ast.parse(p.read_text(encoding='utf-8'))
        except (SyntaxError, OSError):
            continue
        if not is_qt_test_module(tree):
            continue
        key = str(p).replace('\\', '/')
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute):
                attr = n.attr
                if attr.startswith('_') and not (attr.startswith('__') and attr.endswith('__')):
                    bad.append((key, f"{p}:{n.lineno}  private attribute access '{attr}'"))
    return bad


def check_private_access_ratchet(
    tests_root: pathlib.Path = pathlib.Path('tests'),
    ratchet_file: pathlib.Path = DEFAULT_RATCHET_FILE,
) -> tuple[int, int, list[tuple[str, str]]]:
    """Return (current_count, ratchet_limit, accesses) and check against stored ratchet."""
    accesses = find_private_accesses(tests_root)
    count = len(accesses)
    if not ratchet_file.exists():
        raise FileNotFoundError(f"Ratchet file not found: {ratchet_file}")
    ratchet = int(ratchet_file.read_text(encoding='utf-8').strip())
    return count, ratchet, accesses


if __name__ == '__main__':
    layer_violations = find_violations()
    for _key, msg in layer_violations:
        print(msg)
    conversion_violations = find_gui_conversion_violations()
    for _key, msg in conversion_violations:
        print(msg)

    private_ratchet_failed = False
    ratchet_file = DEFAULT_RATCHET_FILE
    if ratchet_file.exists():
        count, ratchet, accesses = check_private_access_ratchet(
            pathlib.Path('tests'), ratchet_file
        )
        if count > ratchet:
            private_ratchet_failed = True
            print(
                f"private-access ratchet: count rose from {ratchet} to {count}. "
                f"Private-attribute accesses in Qt-dependent test modules may only decrease."
            )
        else:
            print(
                f"private-access ratchet: {count} access(es) in Qt test modules "
                f"(ratchet: {ratchet})."
            )
    else:
        print(f"Warning: ratchet file {ratchet_file} does not exist.")

    sys.exit(
        1 if (layer_violations or conversion_violations or private_ratchet_failed) else 0
    )

