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

**Status:** done

- [x] The tab titles, widget construction and stack order derive from one declaration.
- [x] No parallel hand-ordered tab sequence remains in the main window.
- [x] The stream-consuming tab set is derived from the declaration, not maintained separately.
- [x] The application opens on the same tab as before, and split view still reorders correctly.
- [x] Reordering entries in the declaration reorders the tab bar and the stack together, demonstrated once and reverted.
- [x] The suite is green in CI.

Reference: spec section "The single new seam"; ADR 0001 decision 5.

## Comments

- Replaced 3 parallel hand-ordered tab lists (tab bar `addTab` calls, manual widget assignments, and `_outer_stack.addWidget` calls) with a single `TAB_DECLARATIONS` tuple of `TabDeclaration(title, attr_name, factory, consumes_stream)` in [`src/rbl/gui/app.py`](file:///C:/Users/IGLeg/PycharmProjects/RBL/src/rbl/gui/app.py).
- `MainWindow.__init__` now builds the tab bar and stacked widget iteratively in a single loop over `self.TAB_DECLARATIONS`.
- Derived `stream_consuming_tabs` (and legacy `_lj_tabs` alias) as a property over `TAB_DECLARATIONS` where `consumes_stream=True`.
- Application continues opening on "Overview" via dynamic index lookup, and split view behavior operates identically.
- Added tests `test_tabs_derive_from_single_declaration`, `test_stream_consuming_tabs_derived_from_declaration`, and `test_split_view_reordering_and_restoration` to [`tests/test_gui_hardware.py`](file:///C:/Users/IGLeg/PycharmProjects/RBL/tests/test_gui_hardware.py).
- Demonstrated reordering by temporarily swapping "Stepper Motors" and "Beam Current" in `TAB_DECLARATIONS` and verifying that tab bar and stack reordered synchronously in `test_tabs_derive_from_single_declaration`, then reverted back to original declaration order.
- Verified test suite passes cleanly.

