# Spec: Faraday cup status contacts read 0 when asserted, plus cup-tab fixes

**Status:** ready-for-agent
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0003-commanded-and-confirmed-cup-position.md`
**Glossary:** `CONTEXT.md` — **Asserted**, **AUTO mode**, **Interlock**, **In transit**, **Raw contact word** (new)
**Tickets:** `.scratch/cup-status-fix/issues/167`–`174`

## Problem

On the lab PC, with the controller in AUTO and the cup moving on app commands, the
Faraday Cup tab says "Controller not in AUTO (LOCAL mode)" and, because of that, uses
current inference instead of confirmed position. In LOCAL it says AUTO. Separately, about
every other commanded insertion raises "Move Fault: Command IN failed: contacts
indeterminate" although the cup lands IN (cup log `cup_20261110T131938.csv`: 13
`fault_impossible_status` rows, each about 0.1 s before a `confirmed_in`, none before a
`confirmed_out`).

## What the bench established

- 2026-11-08, meter at the P1 breakout: 4–12 (AUTO) is **closed** in AUTO.
- 2026-11-20, meter at the P1 breakout: 3–11 (IN) is **closed** with the cup IN.
- The T7 inputs have pull-ups, so a closed contact reads **0** and an open one reads **1**.
- The app shows the cup IN when it is IN under the current "1 = asserted" rule, so the
  line that reads 1 with the cup IN is the **open** OUT contact. FIO2 therefore carries
  the OUT contact (P1 2–10) and FIO3 carries the IN contact (P1 3–11) — the reverse of
  the documented as-built table. Where the swap sits (breakout or CB37) is not known and
  does not matter to the software.

So the 2026-11-10 reading "FIO4 = 1 in AUTO" (ticket 113) was wrong, and with it 113's
only argument that IN and OUT were not swapped. Every symptom follows:

| Cup / mode | FIO4 FIO3 FIO2 | Word | Decoded today (113 rule) | Decoded after ticket 167 |
|---|---|---|---|---|
| IN, AUTO | 0 0 1 | 4 | IN, LOCAL | IN, AUTO |
| OUT, AUTO | 0 1 0 | 8 | OUT, LOCAL | OUT, AUTO |
| travelling, AUTO | 0 1 1 | 12 | INDETERMINATE (fault) | IN_TRANSIT |
| both closed, AUTO | 0 0 0 | 0 | IN_TRANSIT | INDETERMINATE (fault) |
| IN, LOCAL | 1 0 1 | 20 | IN, AUTO | IN, LOCAL |
| status cable unplugged | 1 1 1 | 28 | INDETERMINATE, AUTO | IN_TRANSIT, LOCAL |

Insertions are slow enough for the travel word to last past the 50 ms contact debounce;
retractions so far have not been.

## Decisions

D1. Fix in software, not at the wires: all three status lines are asserted at level 0;
    IN is bit 3 (FIO3), OUT is bit 2 (FIO2), AUTO stays bit 4 (FIO4). The docs are
    rewritten to describe what is landed.
D2. An unplugged status cable reads "in transit, LOCAL". That is accepted: automatic
    insertion cannot start without AUTO, and a commanded move times out after 2 s. The
    LOCAL warning says so: "Controller not in AUTO (LOCAL mode) or status cable unplugged
    — remote commands will be ignored".
D3. INDETERMINATE now means both contacts closed at once, a real fault. A commanded move
    keeps failing at once on it (unchanged behaviour).
D4. The cup log records the **raw contact word** on `position_transition` and
    `fault_impossible_status` rows, in the existing `status_word` column, formatted
    `0x%08X` as `write_fault_impossible_status` already formats it.
D5. The picoammeter's connect/communication error shows as one short line on the tab, with
    the full message as the label's hover tooltip. Short reasons for the four known VISA
    statuses; anything else is the message's first line clipped to 80 characters.
D6. The Faraday Cup tab gets the shared `LabJackPanel` at the top of its "Cup Actuation"
    box, wired to the one shared T7 exactly as the five LabJack tabs are.
D7. The operator can release and re-close the **interlock** from the Faraday Cup tab, with
    a label showing which state the application last commanded. Releasing opens both
    relays (the existing drive release); closing writes only the interlock line. The
    control is disabled while automatic cup insertion runs, while a move is in flight, and
    without the T7. Releasing with the cup not IN is tracked as the operator's insertion.
    This exists so the bench can confirm the interlock's behaviour (2026-11-10 note: open
    interlock, no OUT by any route).
D8. The Overview's Faraday cup panel gets Insert and Retract. The panel emits intent only;
    the Faraday Cup tab performs the move through the same code as its own buttons, so
    move confirmation and the manual-insertion record are the same from either screen.

## Out of scope

- The full right-beamline connection diagram tab (its own effort, after this set).
- Any change to when the application holds the interlock closed (connect, command) or
  releases it (disconnect, shutdown).
- Any change to the contact debounce, the move timeout, or the current-inference path.
- Physical rewiring.
