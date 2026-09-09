# 03: Slit Currents rename and tab reorder

**What to build:** The tab showing the four slit currents is renamed from "Beam
Current" to "Slit Currents", and the tabs are reordered to follow the sequence an
operator actually works in: check the machine, measure the beam, drive the beam, then
the rare setup screens.

The rename is not cosmetic. A second screen showing cup current is coming, and
`CONTEXT.md` now defines slit current and cup current as distinct quantities that do
not agree with each other. Two tabs both called "Beam Current" is the exact confusion
the cross-tab agreement invariant exists to prevent.

New order: Overview, Vacuum, Stepper Motors, Slit Currents, Beam Profiler, Camera,
Raster Planner, Function Generators, HV Amplifiers, Dynamic Adjustment, HV Calibration,
Load Characterization. The Faraday Cup tab is inserted after Slit Currents by a later
ticket.

**Blocked by:** None (can start immediately)

**Status:** done

- [x] The slit current tab is titled "Slit Currents" everywhere it is named, including
      docstrings that refer to it
- [x] Tabs appear in the order above
- [x] The application still opens on Overview
- [x] Split view still works: right-clicking a tab still moves it and stack indices
      still resolve correctly
- [x] Tabs that consume the LabJack stream still receive it — the set derives from the
      declarations and must not need separate maintenance
- [x] The two existing tests that assume the slit tab is second are converted to look
      the tab up **by title**, not renumbered to a new index
- [x] No test in the suite identifies a tab by hardcoded index
- [x] Full suite green
