# 10: Existing instruments still work under Keysight VISA

**What to build:** Confirmation that switching the control PC's primary VISA to
Keysight did not break the instruments that were already working under NI-VISA.

The two DG1022Z function generators and the TDS 2012 oscilloscope are USB-TMC and are
expected to enumerate under either VISA implementation. "Expected to" is the reason
this ticket exists. Nothing about the Faraday cup depends on the answer, but the
application ships with all of these instruments and a regression here would be found
by an operator rather than by us.

Split out of ticket 01 on 2026-09-14: ticket 01 proves the GPIB path and unblocks the
driver work; this proves nothing else broke. They needed different equipment on the
bench and only one of them gates anything.

**Blocked by:** 01

**Status:** ready-for-developer

> **Developer bench task, not agent-grabbable.** It needs the function generators and
> the oscilloscope physically connected to the control PC.

> **This does not block any other ticket, but it must pass before a build ships.** If
> the cup work is finished and this has not been run, do not cut a release.

- [ ] Both DG1022Z function generators are connected and powered
- [ ] The TDS 2012 oscilloscope is connected and powered
- [ ] `scripts/verify_visa_bench.py` lists both function generators under the default
      VISA backend
- [ ] It lists the oscilloscope under the default VISA backend
- [ ] Each answers `*IDN?` with its expected identification string
- [ ] The two function generator serial numbers are recorded in Comments — the
      application distinguishes the pair by serial and never by hardcoded resource
      string
- [ ] The application itself connects to both function generators from the Function
      Generators tab
- [ ] The application connects to the oscilloscope from the Beam Profiler tab
- [ ] NI-VISA is still installed and has not been uninstalled to make this pass
- [ ] If any instrument does not enumerate: stop, record what was seen, and escalate.
      Do not pin a VISA DLL path in application code and do not uninstall NI-VISA as a
      workaround — either would hide a machine configuration problem inside the app.

## Comments

_(none yet)_
