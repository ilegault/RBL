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

**Status:** ready-for-agent

- [ ] A rule check fails when a module in the GUI layer calls a unit-conversion helper.
- [ ] The check is built on the existing whole-codebase rule machinery, not on new infrastructure.
- [ ] The check runs in CI and passes.
- [ ] The amplifier tab no longer imports or calls a conversion helper.
- [ ] The kilovolt value the amplifier tab renders is produced in the snapshot layer.
- [ ] A test asserts the rendered reading is unchanged, driven through the sanctioned payload path.
- [ ] No pairwise device-view comparison is added in this ticket.

Reference: spec section "Cross-tab agreement"; `CONTEXT.md` on cross-tab agreement.
