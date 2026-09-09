# 01: Keysight VISA on the control PC, existing instruments confirmed

**What to build:** Keysight IO Libraries Suite installed as the primary VISA
implementation on the control PC, and confirmation that the instruments already in
service still enumerate under it. Nothing else in this ticket set is worth building
until this is known.

The 82357B USB/GPIB adapter requires Keysight's VISA. The function generators and the
oscilloscope are USB-TMC and are expected to work under either implementation, but
"expected to" is the whole reason this ticket exists. If they do not enumerate, the
transport decision in the spec has to be reopened.

**Blocked by:** None (can start immediately)

**Status:** ready-for-developer

> **This is a developer bench task, not agent-grabbable.** It needs physical access to
> the control PC and the instruments. An agent should not claim it.

- [ ] Keysight IO Libraries Suite installed on the control PC
- [ ] Resource enumeration lists both DG1022Z function generators
- [ ] Resource enumeration lists the TDS 2012 oscilloscope
- [ ] Resource enumeration lists the 82357B GPIB interface
- [ ] The Keithley 6482 answers an identification query over GPIB
- [ ] The application still connects to both function generators and the scope
- [ ] Findings recorded in this ticket's Comments, including the resource strings seen
- [ ] If any existing instrument does not enumerate: stop, comment, and escalate — do
      not work around it
