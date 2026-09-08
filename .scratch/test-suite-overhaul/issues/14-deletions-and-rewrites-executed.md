# 14: Deletions and rewrites executed from the audit

**What to build:** The suite the audit described. Every test with a `delete`
verdict is removed; every test with a `rewrite` verdict covers the same
behaviour through the sanctioned data path.

A rewrite drives the widget the way the application drives it — a real stream
window pushed through a real beamline into a real tab — and asserts on something
an operator could see or a downstream consumer reads. It does not call a private
method and it does not read a private attribute. A rewrite that cannot be
expressed that way is not a rewrite: that test's behaviour is either already
covered by a contract test in ticket 15, or the verdict was wrong. Say which, in
the ticket's comments, rather than reaching for the private attribute again.

Where a test used a hardcoded tab index, it uses the window's title-based lookup
instead, so reordering tabs cannot silently invalidate it.

The headline test count drops substantially. That is the intended outcome, and
ADR 0001 records it in advance so nobody reads it as a regression. What must not
drop is behaviour coverage: no `delete` beyond the signed-off list, and no
assertion quietly dropped from a test being rewritten.

**Blocked by:** 11

**Status:** ready-for-agent

- [ ] Every `delete` verdict from the signed-off list is executed, and nothing outside that list is deleted.
- [ ] Every `rewrite` verdict is executed through the sanctioned data path.
- [ ] No rewritten test calls a private method or reads a private attribute.
- [ ] No hardcoded tab index remains in the suite.
- [ ] Any verdict that could not be executed is recorded in this ticket's comments with the reason, not worked around.
- [ ] The suite is green in CI and the new test count is recorded against the ticket 01 baseline.

Reference: spec section "The audit"; ADR 0001 decision 6 and Consequences.
