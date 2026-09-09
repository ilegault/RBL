# 13: The GUI layer may not convert; the amplifier tab renders a pre-converted value

**What to build:** Cross-tab agreement enforced structurally rather than checked
case by case.

Raw instrument readings become physical units in exactly one place, the snapshot
layer, and tabs render what they are handed. A whole-codebase rule forbids the
GUI layer from calling the unit-conversion helpers, built on the same machinery
that already enforces the import-layering rule — one rule, checked across every
module, no new seam.

The rule fails on day one: the amplifier tab converts a raw monitor reading to
kilovolts itself. Fixing that is part of this ticket, not a follow-up. The
converted value is published from the snapshot layer and the tab renders it. The
operator-visible reading must not change; if it does, the conversion was not
equivalent and that is the bug this rule exists to catch.

The behavioural pairwise comparison of device views described in the spec is
**not** built here. It is added only for devices the audit found rendered on more
than one tab, and only if this structural rule proves insufficient for them.

**Blocked by:** 11

**Status:** complete

- [x] A rule check fails when a module in the GUI layer calls a unit-conversion helper.
- [x] The check is built on the existing whole-codebase rule machinery, not on new infrastructure.
- [x] The check runs in CI and passes.
- [x] The amplifier tab no longer imports or calls a conversion helper.
- [x] The kilovolt value the amplifier tab renders is produced in the snapshot layer.
- [x] A test asserts the rendered reading is unchanged, driven through the sanctioned payload path.
- [x] No pairwise device-view comparison is added in this ticket.

Reference: spec section "Cross-tab agreement"; `CONTEXT.md` on cross-tab agreement.

## Comments

- Added `find_gui_conversion_violations()` and `CONVERSION_HELPERS` to `scripts/check_layers.py` to forbid the GUI layer (`src/rbl/gui`) from calling or importing unit-conversion helpers directly.
- Wired GUI conversion checking into `tests/test_layering.py` via `test_gui_layer_cannot_call_or_import_conversion_helpers()` and `test_gui_conversion_check_detects_violations()`.
- Extended `AmpChannelSnapshot` with pre-converted `dc_kv` and `dc_ma` fields computed once in `src/rbl/state/labjack_link.py`.
- Refactored `src/rbl/gui/amp_tab.py` to remove unit-conversion helper imports (`monitor_to_kv`, `monitor_to_ma`) and render `ch.dc_kv` / `ch.dc_ma` directly from the snapshot layer.
- Updated `src/rbl/services/snapshot_json.py` to use `dc_kv` / `dc_ma` from `AmpChannelSnapshot`.
- Added / updated tests in `tests/test_amp_tab_isolation.py` and `tests/test_labjack_link.py` verifying pre-converted snapshot generation and rendered table values driven via `LabJackFeed`.

