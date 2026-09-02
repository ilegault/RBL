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
    'rbl/hardware/scope_worker.py',
    'rbl/hardware/vacuum_worker.py',
    # 1.4-b: state imports services (RampEngine); will be fixed by moving
    # RampEngine to rbl/hardware/.
    'rbl/state/funcgen_control.py',
}


def find_violations(root: pathlib.Path = pathlib.Path('rbl')) -> list[str]:
    bad = []
    for p in root.rglob('*.py'):
        parts = p.parts
        # Layer is the immediate subdirectory of rbl/
        L = parts[1] if len(parts) > 2 else 'root'
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


if __name__ == '__main__':
    violations = find_violations()
    for _key, msg in violations:
        print(msg)
    sys.exit(1 if violations else 0)
