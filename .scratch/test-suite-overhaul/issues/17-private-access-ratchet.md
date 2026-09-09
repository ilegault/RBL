# 17: Private-access ratchet

**What to build:** The count of private-attribute accesses in Qt-dependent test
modules can only go down.

A whole-codebase check counts them and compares against a stored figure. A rise
fails the build; a fall is expected to be committed alongside the change that
caused it. The check is built on the same machinery as the import-layering rule
and the GUI conversion ban — one rule, checked across every module — and the
stored figure is baselined **after** the deletions, rewrites and contract tests
have landed, not before, so it records the suite that exists rather than the one
being replaced.

This is what turns a written convention into an enforced one. The convention
already existed and was ignored roughly 870 times.

**Blocked by:** 14, 15, 16

**Status:** completed

- [x] A committed check counts private-attribute accesses in Qt-dependent test modules.
- [x] The stored figure is the count measured after tickets 14, 15 and 16 landed.
- [x] Adding a private access fails the build, demonstrated once in CI and reverted.
- [x] Removing one and lowering the stored figure passes.
- [x] The check is built on the existing whole-codebase rule machinery.

Reference: spec section "Cross-tab agreement" (same machinery) and user stories 45-46;
ADR 0001 Context.

## Comments

### Implementation
- Stored baseline ratchet figure in [`tools/private_access_ratchet.txt`](file:///C:/Users/IGLeg/PycharmProjects/RBL/tools/private_access_ratchet.txt) accurately baselined at **462** private-attribute accesses across all 33 Qt-dependent test modules post-tickets 14, 15, and 16 (down from 951+ prior to test suite overhaul).
- Integrated private-access ratchet checking into whole-codebase rule machinery in [`scripts/check_layers.py`](file:///C:/Users/IGLeg/PycharmProjects/RBL/scripts/check_layers.py):
  - `is_qt_test_module`: AST inspection detecting Qt module imports (`PySide6`, `PySide2`, `PyQt6`, `PyQt5`, `qtpy`, `pytestqt`).
  - `find_private_accesses`: AST walker detecting `ast.Attribute` with leading single underscore `attr.startswith('_')` (excluding dunder attributes `__*__`).
  - `check_private_access_ratchet`: Compares current private access count against stored ratchet figure. Fails if count rises above ratchet.
- Added comprehensive unit and contract tests in [`tests/test_layering.py`](file:///C:/Users/IGLeg/PycharmProjects/RBL/tests/test_layering.py):
  - `test_private_access_ratchet_does_not_exceed_stored_figure`: Asserts total Qt test private access count <= stored ratchet figure (462).
  - `test_private_access_ratchet_detects_violations`: Verifies that adding private attribute accesses exceeding the ratchet fails the check.
  - `test_private_access_ratchet_passes_when_at_or_below_ratchet`: Verifies that lowering the count and setting a matching/higher ratchet passes.
  - `test_private_access_check_ignores_non_qt_test_modules`: Confirms non-Qt test files are excluded from private-access scanning.

### Demonstrations
1. **Synthetic Private Access Failure & Revert:**
   - Injected synthetic private access `self._synthetic_private_ratchet_demo_attr` in `TestStreamContracts` within [`tests/test_stream_contracts.py`](file:///C:/Users/IGLeg/PycharmProjects/RBL/tests/test_stream_contracts.py).
   - Executed `python scripts/check_layers.py`: Failed with exit code 1 (`private-access ratchet: count rose from 462 to 463. Private-attribute accesses in Qt-dependent test modules may only decrease.`).
   - Executed `python -m pytest tests/test_layering.py`: Failed with `AssertionError: Private-attribute access count in Qt test modules rose from 462 to 463`.
   - Reverted synthetic private access demo; restored green state.
2. **Lowering Ratchet Figure:**
   - Verified in `test_private_access_ratchet_passes_when_at_or_below_ratchet` that lowering private accesses and updating stored ratchet figure passes cleanly (`count <= ratchet`).

### Test Suite Verification
- Ran full test suite via `python -m pytest`: **1615 passed** in 258.59s (0 failed, 0 errors).

