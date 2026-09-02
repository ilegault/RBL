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

import pytest

# Add repo root to path so scripts/ is importable.
_REPO_ROOT = pathlib.Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.check_layers import find_violations

# Tracked violations that are not yet fixed; keyed on file path using
# forward slashes (normalised in find_violations).
_KNOWN_VIOLATIONS = {
    'rbl/hardware/scope_worker.py',
    'rbl/hardware/vacuum_worker.py',
    'rbl/state/funcgen_control.py',
}


def test_no_new_layer_violations():
    """Fail if any upward import is added that isn't in the known list."""
    all_violations = find_violations(_REPO_ROOT / 'rbl')
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
