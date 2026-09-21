# 01: Keysight VISA sees the picoammeter from Python

**What to build:** Keysight IO Libraries Suite as the **primary** VISA on the control
PC, and a plain `pyvisa.ResourceManager()` — no DLL path passed — that enumerates the
picoammeter on the GPIB bus and gets an answer out of it.

That is the whole ticket. It proves the transport: adapter, cable, GPIB address, VISA
routing, and Python's ability to talk through all of it. Ticket 04 needs exactly this
and nothing more.

Checking that the function generators and the oscilloscope survive the VISA switch is
a **different question** and lives in ticket 10. It is a regression check on
instruments that already work, it needs those instruments physically present, and it
gates shipping — not driver development.

**Blocked by:** None (can start immediately)

**Status:** in-progress

> **This is a developer bench task, not agent-grabbable.** It needs physical access to
> the control PC and the instrument. An agent should not claim it.

> **The instrument on the bench is a Keithley 6485 stand-in, not the 6482.** The 6482
> remains the target and the spec is correct as written — **do not "fix" the spec's
> 6482 references to say 6485.** See Comments.

Run `scripts/verify_visa_bench.py` for all of this. It probes each installed VISA
backend separately and reports per backend, so an empty resource list is attributed to
the backend that produced it rather than blamed on the hardware.

- [x] Keysight IO Libraries Suite installed on the control PC
- [x] The 82357B adapter is visible to Keysight Connection Expert
- [x] A Keithley picoammeter answers on the GPIB bus (6485 stand-in, address 14,
      Connection Expert status "Verified")
- [x] Keysight VISA reachable from Python (see Comments — the primary-VISA
      setting turned out not to be the blocker)
- [x] `scripts/visa_probe.py` reports the **default** backend — with no DLL path
      passed to `ResourceManager()` — finding `GPIB0::14::INSTR`
- [x] `*IDN?` over that connection returns the Keithley identification string
- [x] The GPIB protocol mode is recorded: SCPI, MEP enabled
- [x] Findings pasted into this ticket's Comments, including the resource strings seen
- [ ] A bare PyVISA session, with **no** `os.add_dll_directory()` help, opens the
      instrument — see the open question in Comments. This is the one that decides
      whether the application needs a dependency-path shim of its own.

Either of two mechanisms satisfies the default-backend criterion, because both are
**machine configuration** rather than application code:

1. Keysight's VISA Conflict Manager, setting Keysight as the primary VISA. Tidier, and
   it fixes the routing for every program on the PC, not just Python. Note it is a
   standalone utility rather than a page inside Connection Expert, and the 32-bit and
   64-bit versions are separate — this repo runs 64-bit Python, so the 64-bit one is
   the one that matters.
2. A `.pyvisarc` file in the user's home directory naming the Keysight DLL:

       [Paths]
       VISA library: C:\Windows\System32\ktvisa32.dll

   PyVISA reads this automatically, so a bare `ResourceManager()` resolves to Keysight.
   Per-user and specific to this machine, and invisible to anything that is not Python
   — record it here if this is the route taken, or a rebuilt control PC loses it with
   no trace of why things stopped working.

**Do not** pin a VISA DLL path in application code. The difference that matters is not
"path versus no path" — it is whether the setting lives on the machine, where a
machine problem belongs, or travels inside the repo to every other machine that will
never need it.

## Comments

### 2026-09-14 — adapter works; PyVISA was loading the wrong VISA

Keysight Connection Expert sees the picoammeter at `GPIB0::14::INSTR`, MODEL 6485,
serial 1381988, firmware C01, status "Verified". So the 82357B adapter, the cable, and
the GPIB address are all fine.

`scripts/verify_visa_bench.py` found **zero** resources, and its own output explains
why:

    #1: C:\Windows\system32\visa32.dll  ->  Vendor: National Instruments
    #2: C:\Windows\system32\visa64.dll  ->  Vendor: National Instruments
    Enumerated Resources (0 found)

PyVISA loaded **NI-VISA**, which has no driver for the Keysight 82357B. It enumerated
nothing and reported success doing it — `ResourceManager()` constructs cleanly and
`list_resources()` returns an empty tuple, so nothing in the old script distinguished
"no hardware" from "this VISA cannot see this hardware".

The script has been rewritten to probe each installed backend in turn, report per
backend, query the GPIB protocol mode, and print a per-category summary.

### 2026-09-14 — do not install an FTDI driver for the 82357B

A Windows "Update Drivers" dialog was opened against the 82357B pointing at
`CDM-v2.12.36.20-WHQL-Certified`. That is FTDI's Combined Driver Model package — the
USB-serial driver used by the vacuum controllers and the scope's serial adapter. It is
**not** a driver for the 82357B, which uses Keysight's own USB driver installed with
IO Libraries.

There was nothing to fix: Connection Expert already listed the adapter as Verified
with the instrument responding. Forcing an unrelated driver onto a working device
would have broken the one part of this ticket that was already finished. Nothing in
this ticket requires touching Device Manager.

### 2026-09-14 — the 6485 is a stand-in, the 6482 is still the target

The 6485 is what was available to test the connection with. The final instrument is
the 6482, and the spec, ADR 0002 and tickets 04-09 are written against the 6482 on
purpose.

The two are not interchangeable and the differences are load-bearing: the 6485 is
single-channel with no voltage source, the 6482 is dual-channel with two 30 V bias
sources. So the 6482-specific rules in ticket 04 — never send `:CONFigure` because it
turns the source outputs on, and assert both outputs off after connecting — protect
against a hazard the 6485 does not have. They stay, because the 6482 is what ships.

What the 6485 can prove: the adapter, the VISA path, the GPIB address, cabling, and
the protocol mode. What it cannot prove: the 6482's reading format, its element names,
or its source-output behaviour.

### 2026-09-14 — scope narrowed, regression check split out to ticket 10

Only the GPIB adapter and the picoammeter are on the bench right now; the function
generators and the oscilloscope are not connected, and Connection Expert's USB0 node
reads "No Instruments Found". Waiting for them would block ticket 04 on equipment
availability for no benefit — a new driver for a new instrument does not depend on
whether the old instruments survived a VISA switch.

So this ticket is now the GPIB path only, and the regression check moved to ticket 10.
Ticket 10 does not block any cup work, but it must pass before a build ships.

### 2026-09-14 — the Conflict Manager is not inside Connection Expert

Looked for a "primary VISA" option in Connection Expert and it is not there. The VISA
Conflict Manager is a separate utility installed alongside IO Libraries Suite, and
Keysight ships distinct 32-bit and 64-bit versions of it. This repo's Python is
64-bit, so the 64-bit one is the relevant one.

If it cannot be found, the `.pyvisarc` route above achieves the same outcome for
Python without any UI, and is fully reversible by deleting the file.

### 2026-09-14 — RESOLVED: it was VISA implementations sharing one process

`scripts/visa_probe.py` (new file; `verify_visa_bench.py` kept getting overwritten by
PyCharm's editor buffer on Run) probes each backend in **its own subprocess**. With
that change all three working backends open a session and talk to the instrument:

    --- Backend: default (PyVISA's own resolution) ---
      library: Visa Library at C:\Windows\system32\visa32.dll
        RESOURCE: GPIB0::14::INSTR
        *IDN?: KEITHLEY INSTRUMENTS INC.,MODEL 6485,1381988,C01 Jun 23 2010 12:22:00/A02 /G
        protocol: SCPI (MEP enabled)

    --- Backend: Keysight (IVI Foundation ktbin, 64-bit) ---  same result
    --- Backend: Keysight (System32 shim) ---                 same result

Two separate faults were in play and they masked each other:

1. **Dependency search path.** `C:\Windows\System32\ktvisa32.dll` is a shim; its
   implementation lives in `C:\Program Files\IVI Foundation\VISA\Win64\ktvisa\ktbin`.
   Python 3.8 stopped searching PATH for the dependencies of a ctypes-loaded DLL, so
   it failed with "Could not find module (or one of its dependencies)" — which reads
   like a missing file when the file is present. `os.add_dll_directory()` fixes it.

2. **Two VISA implementations in one process.** After fault 1 was fixed, every
   backend enumerated the instrument and then threw Windows error `0xE06D7363` — an
   unhandled C++ exception — on `viOpen`. `list_resources()` succeeding first made
   this look like a hardware or driver fault. It was neither: the probe loop had
   loaded NI's `visa32.dll` and Keysight's `ktvisa32.dll` into the same process.
   Isolating each backend in its own process removed it entirely.

**The lesson worth keeping: `list_resources()` succeeding proves very little.** On
GPIB it can be answered from Keysight's configuration store without touching the bus.
`viOpen` is the first real hardware access, so a driver's connect path should treat a
successful enumeration as unverified until a session is actually opened.

**Protocol mode: SCPI (MEP enabled).** Not 488.1. This matches what ticket 04's driver
assumes, so compound queries are permitted. Ticket 04 still queries this at connect
because the setting is a front-panel control stored in EEPROM and can change without
any software involvement.

**Instrument is the 6485 stand-in.** The 6482 remains the target; nothing above
changes that. The transport, adapter, address and protocol findings all carry over.

### 2026-09-14 — OPEN QUESTION: does the application need the dependency shim?

The default backend now works — but every probe in `visa_probe.py` runs after that
script has called `os.add_dll_directory()` on the Keysight folders. The RBL
application does no such thing.

So it is not yet known whether a plain `pyvisa.ResourceManager()` inside the app will
reach the instrument, or whether it needs the same dependency-path setup. Until this
is answered, do not assume the driver in ticket 04 will connect just because this
script does.

Check with, from an activated venv and nothing else running:

    python -c "import pyvisa; print(pyvisa.ResourceManager().list_resources())"

If that returns `('GPIB0::14::INSTR',)`, nothing more is needed. If it returns `()` or
raises, then `os.add_dll_directory()` on the ktbin folder belongs in the application —
in the driver preflight rather than scattered through instrument code — and that is a
scope addition to ticket 04 that should be recorded here first.

### 2026-09-21 — GPIB path confirmed end to end on the real 6482

- The earlier `GPIB0::14::INSTR` responses were from a **6485** at address 14 (the cable was on the
  wrong picoammeter). The 6482 is at **GPIB address 2**, SCPI protocol, GPIB interface selected.
- Before Keysight Connection Expert had scanned address 2, the default (NI) `pyvisa.ResourceManager()`
  gave `VI_ERROR_RSRC_NFOUND` on `GPIB0::2::INSTR`: NI reaches the 82357B only through Keysight's
  saved instrument table. After a rescan in Connection Expert, both the default ResourceManager and
  `ktvisa32.dll` (with the ktbin and IO Libraries `bin` dirs added via `os.add_dll_directory`)
  return the 6482's `*IDN?`.
- A write timeout seen once in between cleared after exiting the front-panel menu / closing
  Connection Expert / reseating; cause not isolated.
- `visa_probe.py` reported "default backend works" and "GPIB 3 found" in runs where nothing
  answered. Fix is ticket 12.
