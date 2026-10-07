# EEL5000.20.100 - what the manufacturer says, and what it means for RBL

**Source:** email from Brian Carmer, Electrical Energy Limited (the EEL5000
manufacturer), 2026-10-07, replying to Isaac Legault's questions of 2026-10-06.
**Status:** manufacturer-stated. **Not bench-verified.** The amplifiers are wired
to the steerer plates and cannot be disconnected for a resistor test, so these
statements are adopted on the manufacturer's word (`docs/adr/0007-current-monitor-scale-is-2-ma-per-volt.md`).
**Where this beats the manual:** wherever the two disagree, this file wins. The
manual's ratings table is copied in `docs/AMP_ENVELOPE_AND_HV_SAFETY_PLAN.md`
section 1.2; that table is superseded where marked below.

Vocabulary follows `CONTEXT.md` ("Amplifier limits and load", and "Trip means two
things" under Known collisions). *Amplifier LIMIT* and *amplifier TRIP* are the
front-panel modes; the application's *hard trip* and *soft trip* are something else.

---

## 1. Manual vs manufacturer

| Item | Manual says | Manufacturer says | Effect on RBL |
|---|---|---|---|
| Current monitor scale | 1 V = 10 mA | **1 V = 2 mA**; ±10 V = ±20 mA | Every current the app has computed is 5x too high (section 3.1) |
| Current limit pot range | 0.5 - 10 mA | **0.5 - 20 mA**; one minor division ~ 2 mA; dial 50 (centre) = 10 mA; dial 100 (fully clockwise) = 20 mA | "Pot at maximum" means a 20 mA limit, not 10 mA |
| What amplifier LIMIT acts on | not stated | **Instantaneous** current, clamped at the pot setting | In LIMIT a short spike cannot exceed the pot setting; the output voltage sags instead (section 3.3) |
| Amplifier TRIP | not described in detail | HV turns off when current reaches the pot setting. Manual (local) mode: reset by pressing HV ON. Remote mode: HV re-enables itself within milliseconds, and trips again if the cause is still there | Not used by RBL; we run amplifier LIMIT |
| Default current configuration | implies 20 mA DC **or** 100 mA / 4 ms | **Factory set to 20 mA DC.** 100 mA peak AC is a separate configuration | The burst rating may not exist on our units (open question 5.1) |
| Burst recovery | after 100 mA / 4 ms, **10 mA** for 100 ms | after up to 4 ms at 100 mA, the unit drops to **20 mA** until 100 ms is reached; then another 100 mA burst is available, repeatable indefinitely. Set by an internal timer and comparator; the limit exists because the capacitors need time to recharge | Only matters if our units are in the 100 mA configuration |
| Long-term drift | not stated | **No drift data has ever been taken.** Confidence rests on 40 years of experience with the TC and VC parts | Our 12 h drift pass (ADR 0005) is the only drift evidence there is |
| DYNAMIC ADJUSTMENT | ambiguous | Raises loop gain to damp a capacitive load: more capacitance needs more turn. Fully clockwise is the end; beyond that, square corners round off and a triangle distorts. Two customers (1.8 nF and 20 nF loads) had the internal loop modified for heavy loads. Those high-gain units **must be connected to their load when HV is turned on, or they oscillate and can fail** | Whether a standard unit at fully clockwise is safe with no load is open (5.4) |

## 2. The answers, verbatim

Questions are Isaac's (2026-10-06); answers are Brian Carmer's (2026-10-07).

**1. How does LIMIT mode work? Instantaneous or average, and how fast?**
"The CURRENT LIMIT works on instantaneous current and will limit at whatever value
you set the Potentiometer on the Front Panel to."

**2. With the pot at maximum in LIMIT, is 10 mA the sustained clamp, or can it still
deliver 20 mA continuous?**
"I'm sorry, there are two errors in the Operators Manual related to the Current.
The Potentiometer on the Front Panel goes from 0.5mA - 20mA, so each minor division
on the potentiometer represents ~ 2mA. Center point reading 50 (5 minor divisions -
half way) = 10mA and 100 (10 minor divisions - FCW) = 20mA. The CURRENT MONITOR is
scaled so that 1V = 2mA, so +/-10VDC at the CURRENT MONITOR Represents +/-20mA DC."

**3. How does TRIP mode work? What triggers it, how fast, how is it reset?**
"When you switch the slide switch to the TRIP position on the Front Panel, the unit
will trip off once the dialed current reached. For example, if you have the
Potentiometer set to the center (5 minor divisions - 50 = 10mA), the High Voltage
will Trip Off when ~10mA of current is being drawn. All you need to do to reset the
unit is to push the HV ON button to reactivate the High Voltage assume you removed
the condition that caused the draw of >10mA current. The RESET speed is as fast as
you can manually reactivate the High Voltage. If you are or plan on using the unit
in the REMOTE condition, the High Voltage will turn back on in the milliseconds
range as long as you have removed the condition that caused the unit to trip,
otherwise it will keep tripping when it goes to automatically turn back on. If you
are operating in the MANUAL mode (normal operation), then resetting is manual."

**4. After a 100 mA / 4 ms burst the manual says 10 mA for 100 ms. What starts the
recovery, is it always 100 ms, and what does the output voltage do?**
"The unit is factory set for 20mA DC, not the 100mA peak current for 4ms. When the
unit is set for the 100mA peak AC mode, the unit provides 100mA for up to 4ms, then
the unit automatically goes to 20mA level until 100ms is reached, then you can draw
another 100mA peak current for another 4ms any time after the 100ms, and can repeat
this continuously. There is an internal timer and comparator circuit used to set
these parameters. The reason for these limits is the time needed to charge the
capacitors to be able to deliver the 100mA peak current."

**5. Have you tested these for long periods for drift?**
"No, there is no drift data taken, but we've had over 40 years of experience with
the parts (TC & VC) associated with drift, so we are confident with the low drift
rate."

**6. Best practice for the DYNAMIC ADJUSTMENT pot?**
"This is a bit difficult to define as the Dynamics is based on the capacitive
component of your load. The higher the load, the more damping you will need, so to
achieve this, the loop gain is increased by using the DYNAMIC ADJUSTMENT. However,
there is a limit to how much capacitance the amplifier can dampen. Once it's dialed
FCW, then no more adjustment is available and you will see a rounding of the
corners of a Square wave or more distortion on a Triangle wave form based on how
large the capacitive component is.
We have two customers that we've had to adjust the amplifier loop for their heavy
capacitive loads (1.8nF & 20nF respectively), so the internal loop dynamics have
been adjusted to accommodate these heavy loads. When we do this, the loop gain is
really high, so the unit needs to be connected to the heavy load when the High
Voltage is turned ON. If the load is not connected, the unit will go into an
oscillation because of the high gain and could cause it to fail."

(Brian's typos in "rest" -> "reset", "CRRENT" -> "CURRENT" and "100ms peak" -> "100mA
peak" are corrected above; nothing else is changed.)

---

## 3. What this means for RBL

### 3.1 Every current RBL has computed is 5x too high

`CURRENT_MONITOR_MA_PER_VOLT = 10.0` in `src/rbl/config/hardware_config.py` came
from the manual. The true factor is 2.0. Everything computed from the current
monitor is therefore inflated by exactly 5:

| Number | As recorded | Corrected (divide by 5) |
|---|---|---|
| Plate load capacitance, last four-channel measurement | 1528 - 1650 pF | **~306 - 330 pF** |
| `CAL_LOAD_CAP_PF` (planning fallback) | 1500 pF | ~300 pF |
| Physics plan section 1.4 figure | 1200 pF | ~240 pF |
| Current-monitor noise floor (physics plan section 1.8) | ~1.4 mA rms | ~0.28 mA rms |
| Steady current at the usual operating points (physics plan section 1.8) | 1.2 - 5 mA | ~0.24 - 1 mA |

The corrected capacitance now makes sense. The geometry estimate in physics plan
section 1.4 is ~125 pF for cable, feedthrough and plates. The "~1 nF unaccounted
for" shrinks to roughly 180 - 200 pF, which is a plausible amplifier output
network. A corrected ~300 pF is also under the manual's "loads > 1 nF need factory
adjustment" line. The old 1.5 nF was over it.

**Why the old measurements looked trustworthy and still weren't:** C measured at 64
Hz and 517 Hz agreed within 8%. That agreement shows the load is a linear capacitor.
It says nothing about the monitor's scale factor, because a wrong scale multiplies
both measurements by the same 5.

### 3.2 The application's own trips have been firing early, not late

The conversion and the thresholds were both in the inflated units, so in true
current:

| Application limit | Threshold as written | True current at which it fired |
|---|---|---|
| Soft trip (`CAL_AC_TRIP_MA`) | 20 mA | **~4 mA** |
| Hard trip (`CAL_TRIP_HARD_MA`) | 60 mA | **~12 mA** |
| Spike "above the continuous rating" warning | 20 mA | ~4 mA |

Nothing has been unsafe. The application has been more conservative than it
meant to be: it reported operating points as near the 20 mA continuous rating
when they were at about a fifth of it. The Raster Planner's current wall sits 5x
too low in frequency.

**Fixing the scale is not just a constant change.** With 1 V = 2 mA, the
LabJack's ±10 V input range covers only ±20 mA. A monitor at the end of its
range reads 20 mA, not "a huge number", so a 60 mA hard trip can never fire. The
hard trip's definition in `CONTEXT.md` ("a railed monitor reads as a huge number")
relies on the old factor. The hard trip is therefore redefined by time at the
rail (section 6).

### 3.3 Amplifier LIMIT clamps instantaneously, so spikes cannot overshoot it

In amplifier LIMIT, current is clamped at the pot setting on every instant. With
the pot fully clockwise that is 20 mA, which is also the end of the monitor's
range. So in LIMIT:

- No current spike can exceed the pot setting. A spike that wants more current
  shows up as a **voltage** shortfall: the output stops following its input.
- The **regulation detector** (commanded vs. measured voltage) is the instrument
  that sees a limit event. The spike recorder sees current pinned at the limit.
- A current reading at the end of the monitor's ±10 V range is a lower bound, not
  a measurement.

The earlier belief that "LIMIT lets short spikes exceed 20 mA" came from the
inflated monitor readings and is withdrawn.

**Operating mode (settled):** amplifier LIMIT, pot fully clockwise (dial 100 =
20 mA). The reason is that amplifier LIMIT keeps a multi-hour irradiation running
where amplifier TRIP would drop HV and wait for someone to press HV ON. It is not
spike headroom.

### 3.4 The burst rating may not apply

The units ship set for 20 mA DC. The 100 mA / 4 ms burst and its recovery period
exist only in the 100 mA peak AC configuration. Until the manufacturer confirms
which configuration our four serial numbers are in, the burst rating, the
recovery period and every warning built on them (the 100 mA line on the
Raster Planner and spike charts, the "two spikes under 100 ms apart" warning)
describe a capability we may not have.

**Working assumption (operator, 2026-10-07):** our units are in the factory 20 mA
DC configuration, and the numbers only looked like burst-level currents because of
the wrong scale. The application assumes no burst until the manufacturer says
otherwise. Running a unit in the 100 mA AC configuration to see what a burst looks
like is a later experiment the operator wants to do, not part of current work.

### 3.5 Drift

The manufacturer has no drift data. A 12 h drift pass on the plates (ADR 0005)
produces the only drift record that exists for these amplifiers. Treat it as a
measurement campaign, not as a confirmation.

---

## 4. A consistency check that needs no disconnection

A resistor test needs the output disconnected, which isn't possible. The clamp
test (ticket 45) can still cross-check the scale with the plates attached:

1. Set one amplifier's pot to dial **50** (10 mA, per the manufacturer).
2. Drive a triangle and raise the frequency until the output stops following its
   input.
3. Read the **raw current-monitor voltage** where the current flattens.
   - **~5 V** confirms 1 V = 2 mA.
   - **~1 V** would mean the manual was right after all.

This is a cross-check, not a precondition. ADR 0007 adopts 1 V = 2 mA now.

---

## 5. Open questions

For the manufacturer (follow-up email of 2026-10-07). Add the answers here when
they arrive, and note any question the email did not ask:

1. For our four serial numbers: 20 mA DC configuration or 100 mA peak AC?
2. Does the current monitor scale depend on that configuration? (Would the
   manual's 1 V = 10 mA be correct for 100 mA units?)
3. In the 100 mA configuration, how does the LIMIT pot interact with a burst?
4. Does an amplifier in LIMIT ever shut its output off on its own, and if so,
   on what condition?
5. Is a *standard* (unmodified) unit with DYNAMIC ADJUSTMENT fully clockwise
   safe to turn on with no load connected?

Settled inside RBL on 2026-10-07; see section 6.

---

## 6. Decisions taken from this (2026-10-07)

Spec: `.scratch/current-monitor-scale/spec.md`.

1. **Scale.** The current monitor is 1 V = 2 mA, on the manufacturer's word (ADR
   0007). There is one conversion constant and nothing else converts current.
2. **No software current limit on an experiment.** Sessions and drift passes are
   never stopped by current (ADR 0006, unchanged). An irradiation run at 19.9 mA is
   allowed. The amplifier's own LIMIT is the protection.
3. **Calibration and characterization keep their aborts, redefined for a 20 mA
   monitor.** The hard trip is "at the rail for 5 ms or more" (a square edge into
   even 3000 pF leaves the rail in under 1.5 ms). The soft trip level is 19 mA,
   just under the rail. The 20 mA continuous rating stays a separate, unchanged
   number for display and planning.
4. **The Raster Planner warns, it does not limit.** Amber from 10 mA and red above
   16 mA are warnings only.
5. **Above 20 mA is recorded as "at the rail", with its duration.** No peak is
   claimed for a rail event. The burst-only parts (the 100 mA chart levels, the
   4 ms zone, the "two spikes under 100 ms apart" warning) are removed until the
   100 mA configuration is understood.
6. **Old data is archived, never rescaled.** A one-shot script moves the
   amplifier-current stores into an archive folder with a README explaining why.
   Every new result and spike file records the scale it was computed with.
7. **The clamp test at pot dial 50 is how the operator first sees the corrected
   current.** It should flatten near 5 V on the monitor (10 mA).
