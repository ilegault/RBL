# Active work: Faraday cup reader

This is a **pointer**, not the work. The work is a ticket set.

- Spec: `.scratch/faraday-cup-reader/spec.md`
- Decision record: `docs/adr/0002-cup-acquisition-triggered-by-current.md` — **read it before ticket 07**
- Binding: `docs/adr/0001-tests-first-and-no-muted-failures.md` — still in force
- Glossary: `CONTEXT.md`, "Beam interception and collection" — slit current vs cup current
- Tickets: `.scratch/faraday-cup-reader/issues/01…10`
- Tracker conventions: `docs/agents/issue-tracker.md`

Done: **01** (VISA/GPIB transport proven), **02** (bundled-installer path retired),
**03** (Slit Currents rename and tab reorder). Previous effort
`.scratch/test-suite-overhaul/` is also complete.

## Next up

- **04 — Keithley 6482 driver and parsing.** Unblocked, and the only thing on the
  frontier. Everything from 05 onward chains off it.

**Read ticket 01's Comments before starting 04.** The bench work found things that
change how the driver should be written, not just whether it can connect:

- The instrument currently on the bench is a **6485 stand-in**. The 6482 is the target
  and every ticket is written against it deliberately. Do not rewrite them for the
  6485 — it is single-channel with no voltage source, so the 6482's source-output
  safety rules look unnecessary against it and are not.
- **`list_resources()` succeeding proves very little.** On GPIB it can be answered
  from the VISA configuration store without touching the bus. `viOpen` is the first
  real hardware access. A connect path must not report success on enumeration alone.
- **Protocol mode is SCPI, not 488.1**, so compound queries are permitted. Query it at
  connect anyway — it is a front-panel setting stored in EEPROM that can change with
  no software involvement.
- A plain `pyvisa.ResourceManager()` reaches the instrument with no DLL-path help, so
  the application needs no dependency-path shim.

## Also open

- **10 — Existing instruments under Keysight VISA.** Developer bench task, blocked by
  01, blocks nothing. The function generators and scope were not connected during 01,
  so their regression check is still outstanding. **Must pass before a build ships.**

## Rules for working this set

- Do not start a ticket whose `Blocked by:` line names an unfinished ticket.
  Work the frontier: any ticket whose blockers are all done.
- Ticket 10 is a developer task. An agent must not claim it.
- A failing test is fixed or escalated, never muted. The escalation path —
  commit to branch, ticket `Status: blocked`, comment on the ticket, draft PR —
  is ADR 0001 decision 3.
- Two requirements in this set are structural and will be quietly violated if
  read as preferences: SCPI response parsing is a **pure function outside the
  polling thread** (ticket 04), and the acquisition state machine **takes
  timestamps as inputs and never calls the clock** (ticket 07). Both exist so
  the feature is testable at one high seam; burying either collapses the seam.
- The 6482's configure command is never sent. It turns the voltage source
  outputs on, and those outputs would drive the cup collector.
