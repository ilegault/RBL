# 06: Faraday Cup tab renders cup current

**What to build:** A Faraday Cup tab showing the live cup current, with its own connect
panel so the cup can be brought up without connecting the whole beamline. Inserted after
Slit Currents.

This is the first moment the application displays two different current quantities at
once, which is the situation `CONTEXT.md`'s cross-tab agreement invariant exists to
guard. ADR 0001 records that nothing in the suite currently tests that invariant.

**Blocked by:** 03, 05

**Status:** ready-for-agent

- [ ] A tab titled "Faraday Cup" appears directly after "Slit Currents"
- [ ] It renders the cup current snapshot, auto-scaling across nanoamps to milliamps
- [ ] It converts nothing — it renders what the snapshot carries
- [ ] It owns no driver and no instrument handle
- [ ] An over-range reading is shown as such, not as a number
- [ ] Its own connect panel connects and disconnects the picoammeter alone
- [ ] Connection state is presented the same way other instruments present theirs
- [ ] With nothing connected the tab says so, and shows neither a stale value nor zero
- [ ] The tab is usable with no hardware present at all
- [ ] A test drives the cup feed helper and asserts on what the tab renders
- [ ] A cross-tab agreement test asserts that slit current and cup current are rendered
      as distinct, differently-labelled quantities, and that the cup reading is not
      derived from or conflated with the log-amp path
- [ ] Tests assert on rendered output, not on private widget attributes
