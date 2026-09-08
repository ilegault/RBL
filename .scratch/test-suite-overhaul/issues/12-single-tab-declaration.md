# 12: The main window builds its tabs from one ordered declaration

**What to build:** The main window's tab set is written down once, and the
application builds from that one place.

Today the tab list exists three times — the tab-bar labels, the constructor
assignments, and the stack insertion order — as three parallel sequences kept in
the same order by hand. They collapse into a single ordered declaration: title,
the widget it wraps, and whether it consumes stream windows. The window builds
its tab bar and its stack from that declaration, and the existing
stream-consuming tab set becomes a view over it rather than a second hand-kept
list.

This is the **only new seam** this effort adds. It carries its weight as a
refactor independent of testing: it removes a three-way ordering hazard of
exactly the kind that left a hardcoded tab-index table in the test suite
describing four tabs when the application has twelve and opens on one that is
not in the list. Behaviour does not change — same tabs, same order, same
starting tab.

Do not add per-tab metadata beyond what the declaration needs. Split-view
handling, which removes a scroll area from the stack and shifts the indices
below it, keeps working through the existing index-mapping helper; the
declaration describes the tabs, not the stack's live state.

**Blocked by:** 11

**Status:** ready-for-agent

- [ ] The tab titles, widget construction and stack order derive from one declaration.
- [ ] No parallel hand-ordered tab sequence remains in the main window.
- [ ] The stream-consuming tab set is derived from the declaration, not maintained separately.
- [ ] The application opens on the same tab as before, and split view still reorders correctly.
- [ ] Reordering entries in the declaration reorders the tab bar and the stack together, demonstrated once and reverted.
- [ ] The suite is green in CI.

Reference: spec section "The single new seam"; ADR 0001 decision 5.
