# 05: Picoammeter owned by Beamline

**What to build:** The picoammeter becomes an instrument the application owns, in the
same way it owns every other instrument. Connecting it from Connect All brings it up;
polling it produces a typed cup current snapshot that anything can subscribe to.

No user-visible screen in this ticket. What this delivers is the complete path from
bytes on the wire to a published snapshot, verifiable on its own through the test seam.

**Blocked by:** 04

**Status:** ready-for-agent

The test seam introduced here is the one the rest of the feature is tested through, so
it matters more than the usual test helper. A cup feed helper joins the existing test
payload module alongside the LabJack feed, injecting **raw SCPI response strings** into
a real `Beamline` wired as the main window wires it. Follow the LabJack feed's shape and
read its docstring first: the previous approach fed a widget's private method directly
and so tested a widget-shaped imitation of the production path instead of the path.

- [ ] A polling worker performs all blocking instrument I/O on its own thread and
      communicates only by signals
- [ ] The worker owns its instrument handle for its lifetime
- [ ] A link mixin joins `Beamline`, following the shape of the existing vacuum link
- [ ] `Beamline` owns the instrument — no widget constructs or tears one down
- [ ] Cup current is published as a frozen snapshot carrying `connected`, like every
      other snapshot
- [ ] The snapshot carries current, instrument timestamp, status word, and an over-range
      flag
- [ ] No unit conversion happens anywhere in this path — the instrument returns amps
      already scaled, and that must remain true
- [ ] The picoammeter participates in the stepped Connect All sequence without making
      the window appear hung
- [ ] Connect and disconnect are available independently of the rest of the beamline
- [ ] Teardown is ordered correctly in shutdown and leaves the instrument's state alone
- [ ] Idle polling runs at 2 Hz; both poll rates live in the config layer, not as
      literals
- [ ] A cup feed helper exists in the test payload module and injects raw SCPI strings
      into a real `Beamline`
- [ ] A test drives that helper and asserts on the snapshot `Beamline` publishes,
      including an over-range sample
- [ ] Nothing connected produces a snapshot with `connected` false, not a zeroed reading
