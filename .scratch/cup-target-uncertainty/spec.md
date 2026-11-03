# Spec: End a cup insertion when its mean is precise enough (target uncertainty)

Status: ready-for-agent
Date: 2026-11-03
Related: `docs/adr/0003-commanded-and-confirmed-cup-position.md` (read amendment D,
2026-11-03, and C1), `docs/adr/0002-cup-acquisition-triggered-by-current.md`
(amendment A6-A8: settings, lock, pending change, settings-change markers),
`docs/adr/0001-tests-first-and-no-muted-failures.md`,
`CONTEXT.md` (Cup actuation and dose: insertion uncertainty, dwell mode, converged
insertion, maximum dwell, sampling insertion, settle window, pending change, manual
insertion, counted insertion),
`.scratch/cup-settings/spec.md` and `.scratch/cup-actuation/spec.md` (the settings and
cycle this builds on).

> ADR 0001 is binding on all test work here: a failing test is fixed or escalated,
> never muted.

## Problem Statement

Every automatic cup insertion lasts the same fixed dwell (3 s by default), whatever the
beam is doing. On a quiet beam most of that dwell measures a current already known well
enough, and every second of it shadows the specimen. On a noisy beam the same dwell may
not be long enough to pin the current down, and nothing tells the operator so. The
application already computes each insertion's post-settle mean and spread, but uses
neither to decide when to pull the cup out, and the operator cannot see how precise an
insertion was while it is happening.

## Solution

A second **dwell mode**, **target uncertainty**, alongside the existing **fixed dwell**.
In target uncertainty an insertion ends as soon as its **insertion uncertainty** (the
relative standard error of its post-settle mean) is at or below the operator's target
(default 5 %), once it holds at least a minimum number of post-settle samples (default
10), or when its **maximum dwell** (default 5 s) is reached, whichever comes first.

Insertion uncertainty is computed, shown live and logged for every insertion in both
modes. When it cannot be computed, the operator is told why. Manual insertions show the
same live readout, can optionally retract themselves on convergence, and can optionally
run with no time limit for bench testing. The Overview cup panel shows two small bars
while the cup is in: uncertainty draining toward the target, and elapsed time against
the dwell. While the cup is out it shows a one-line summary of the last insertion.

The picoammeter is polled back-to-back while a run is open, instead of sleeping 0.1 s
after every read, so insertions collect samples as fast as the instrument allows.

## User Stories

1. As an operator, I want to choose between fixed dwell and target uncertainty on the
   Faraday Cup tab, so that I can keep the behaviour I trust until I trust the new one.
2. As an operator, I want fixed dwell to remain the default, so that nothing changes
   for an irradiation unless I change it.
3. As an operator, I want the fixed dwell and the maximum dwell to be two separately
   labelled fields ("Fixed dwell" and "Max dwell (target uncertainty)"), so that I never
   confuse a set time with a cap.
4. As an operator, I want only the field belonging to the current mode to be editable,
   with the other shown disabled, so that I can see both values but cannot edit the
   wrong one.
5. As an operator, I want editing one dwell value never to change the other, so that
   each mode remembers its own number.
6. As an operator, I want the target uncertainty to be editable as a percentage
   (default 5 %, allowed 0.5 % to 50 %), so that I can trade precision against beam time.
7. As an operator, I want a change to the mode, the target, or either dwell made while
   automatic insertion is running to be held as a pending change and applied at the
   next insertion, so that no insertion changes rules partway through.
8. As an operator, I want each of those changes written to the cup log as a
   settings-change marker with old value, new value and time, so that the archive shows
   why two insertions behaved differently.
9. As an operator, I want an automatic insertion in target-uncertainty mode to retract
   as soon as it converges, so that the specimen is shadowed for as little time as the
   measurement needs.
10. As an operator, I want an automatic insertion that does not converge to retract at
    the maximum dwell anyway, so that the cup is never left in front of the specimen
    without limit while nobody is watching.
11. As an operator, I want a not-converged automatic insertion still to count toward
    the dose with its mean, so that a beam-on interval is never left with no current.
12. As an operator, I want an insertion never to stop on precision before it holds the
    minimum number of post-settle samples, so that one sample or a lucky quiet stretch
    cannot end it.
13. As a developer at the bench, I want the minimum sample count in the settings file
    only, so that it can be tuned without exposing it on the tab.
14. As an operator, I want the live insertion uncertainty and sample count shown on the
    Faraday Cup tab during every insertion, so that I can watch precision build.
15. As an operator, I want to see the reason when the uncertainty cannot be computed
    ("too few samples (n/10)", "no valid samples", "mean negative", "mean below run end
    current"), so that a missing number tells me what is wrong with the beam or reading.
16. As an operator, I want insertion uncertainty computed and logged in fixed-dwell mode
    too, so that I build evidence about my beam's noise before switching modes.
17. As an operator, I want every insertion's summary in the cup log to record its dwell
    mode, target, minimum sample count, maximum dwell, achieved uncertainty (or the
    reason it has none), and the reason it ended, so that I know why every insertion
    lasted as long as it did.
18. As an operator, I want the end reason to be one of `converged`, `max_dwell`,
    `dwell_elapsed`, `operator`, `fault`, so that the archive can be filtered by it.
19. As an operator, I want each target-uncertainty insertion to record
    `converged_after_s`, the time from the end of the settle window to the first moment
    it reached the target, even if it kept going, so that I can choose a sensible
    maximum dwell from real data.
20. As an operator, I want the new cup log columns appended after the existing ones, so
    that old analysis scripts that index columns by position keep working.
21. As an operator doing a beam check, I want manual insertions to show the live
    uncertainty and the converged state but stay in until I retract them, by default,
    so that the cup never moves out from under me.
22. As an operator, I want a "Retract manual insertions when converged" checkbox, off by
    default, enabled only in target-uncertainty mode, saved in the settings file and
    never locked, so that I can let a manual test end itself.
23. As an operator, I want a manual insertion that ends itself to stay uncounted, so
    that beam checks never enter the dose.
24. As an operator testing at the bench, I want a "No time limit on manual insertions"
    option, enabled only when the manual auto-retract checkbox is on, so that I can see
    how long the beam takes to converge without a cap cutting it off.
25. As an operator, I want automatic insertions never to be offered "no limit", so that
    an unattended cycle cannot park the cup in the beam.
26. As an operator, I want a hand insertion at the controller and any insertion made
    while the application cannot command the cup to be display-only, so that the
    application never claims to retract something it cannot move.
27. As an operator, I want the Overview cup panel to show, while the cup is in, an
    uncertainty bar that drains toward a target mark, so that I see convergence at a
    glance from the Overview tab.
28. As an operator, I want that bar scaled to four times the target (20 % for a 5 %
    target) with anything above shown full and the target mark a quarter of the way
    along, so that the early, wild uncertainties do not make the scale meaningless.
29. As an operator, I want the bar amber while working, green once converged, and grey
    with the reason text while the uncertainty cannot be computed, so that its colour
    alone tells me the state.
30. As an operator, I want text beside the bar showing the live uncertainty, the target,
    `n = <n> / ~<n_needed>` and an estimated time left, so that I know how close it is.
31. As an operator, I want a second bar showing elapsed time against the active dwell
    (fixed dwell or max dwell), or a plain running timer when there is no limit, so
    that I see how long the insertion has been going.
32. As an operator, I want the uncertainty readout to keep updating after convergence
    on a manual insertion that stays in, so that I can see where it settles.
33. As an operator, I want, while the cup is out, a one-line summary of the last
    insertion (for example `last: 4.2 % · converged in 1.8 s · n = 23`), so that the
    panel stays useful between insertions.
34. As an operator in fixed-dwell mode, I want the uncertainty bar still shown but with
    no target mark and never turning green, so that I see beam noise without implying a
    stopping rule.
35. As an operator, I want the Faraday Cup tab to show a "Last insertion" line with the
    final uncertainty (or its reason), sample count, end reason and duration of the most
    recent insertion, so that I can see how the last one went after the cup is out.
36. As an operator, I want the picoammeter polled back-to-back while a run is open and
    at the existing idle rate otherwise, so that insertions collect more samples without
    loading the bus when nothing is happening.
37. As an operator, I want a bad value for any new setting in the settings file to fall
    back to its default with the key, value, reason and default shown on the tab, so
    that a bad edit never silently changes behaviour.
38. As an operator, I want the period check to apply to whichever dwell the active mode
    uses (the period must hold that dwell plus two confirmed moves), so that the
    schedule stays valid in either mode.
39. As a developer at the bench, I want a bench ticket that measures the real acquiring
    poll rate and the sample-to-sample correlation of the cup current at that rate, so
    that we know whether the insertion uncertainty is honest.
40. As a reader of the archive, I want the settings file values in force (including the
    file-only minimum sample count) written into the cup log header, so that a run can be
    re-judged later.

## Implementation Decisions

- **Insertion uncertainty is a pure function in the dose model**, beside the existing
  post-settle statistics, with no Qt and no clock. Inputs: the insertion's samples
  ((time, current) pairs, invalid/over-range readings already marked), the confirmed-in
  time, the settle window, the target (fraction), the minimum sample count and the run
  end current. Output: a frozen result holding the uncertainty (fraction, or none), the
  reason it is none, the post-settle valid sample count, whether it is converged, and the
  time since settle-window end. It is pure so the stopping rule is testable with lists of
  numbers and no hardware.
- **Uncertainty definition:** u = s / (sqrt(n) · |mean|) using the sample standard
  deviation (ddof = 1) of the valid post-settle samples, the same samples
  `compute_insertion_current` uses for the mean.
- **Reasons the uncertainty is none**, checked in this order: `no valid samples` (at
  least one post-settle reading and none valid); `too few samples (n/<min>)` (fewer than
  the minimum count; this also covers no post-settle readings yet); `mean negative`;
  `mean below run end current` (mean at or below the run end current). The reason is a
  fixed set of values, not free text, and the display text is derived from it.
- **Converged** means u is not none and u <= target. An insertion is converged from the
  first sample at which that holds; `converged_after_s` is the time of that sample minus
  the settle-window end. Later samples never un-converge an insertion that has already
  been marked converged for logging, but the live readout keeps updating.
- **Bar numbers are a second pure function** taking the uncertainty result, the target,
  the minimum count and the observed mean sample spacing: drain fraction =
  min(u, 4·target) / (4·target), target mark at 0.25; n_needed = max(min count,
  ceil((s / (|mean|·target))²)); time left = (n_needed − n) × mean sample spacing, or none
  when not computable. These numbers are for display only and never feed the stopping
  decision.
- **The scheduler stays clockless and never sees samples.** It gains: the dwell mode,
  the fixed dwell and the maximum dwell as separate values (each with its own pending
  value, applied at the next insertion like the existing dwell), and a new input
  `notify_converged(t)`. In target-uncertainty mode, after `notify_converged(t)` during
  INSERTING the next `tick` returns `CycleRetract`; otherwise `tick` returns
  `CycleRetract` when the maximum dwell has elapsed. In fixed dwell, `notify_converged`
  is ignored and the fixed dwell governs as today. The retract action carries its end
  reason (`converged`, `max_dwell` or `dwell_elapsed`).
- **The Faraday Cup tab is the only caller of the convergence check.** On each new
  sample during an open run it calls the pure function, updates the live readout and the
  cup view, and on first convergence: notifies the scheduler for an automatic insertion;
  for a manual insertion (Insert button or force start) commands a retract only when the
  auto-retract checkbox is on, actuation is connected and the controller is in AUTO;
  otherwise does nothing but display. A manual insertion with auto-retract on and no-limit
  off also retracts at the maximum dwell, with end reason `max_dwell`.
- **The tab keeps the current insertion's samples itself**, in memory, from run open to
  run close, independent of the cup log writer. Today the post-settle statistics come
  from the writer, which exists only while a cup log is open; the live uncertainty must
  work with no cup log open (ADR 0002 B3: displayed, nothing written), because a manual
  bench test is often run without one.
- **End reasons** are a fixed set: `converged`, `max_dwell`, `dwell_elapsed`,
  `operator` (any operator retract, including a manual insertion pulled by hand), `fault`
  (any fault-forced retract). Manual and automatic insertions use the same set.
- **Manual insertions stay uncounted** whatever ends them (ADR 0003 C1). Their excluded
  interval is handled exactly as today.
- **Settings file gains keys**, validated like the existing ones (bad value → default,
  shown with key, value, reason): `dwell_mode` (`fixed` | `target_uncertainty`, default
  `fixed`); `target_uncertainty` (fraction, 0.005 to 0.5, default 0.05); `max_dwell_s`
  (default 5.0, must exceed the settle window); `min_converge_samples` (integer >= 2,
  default 10, file only, no field on the tab); `manual_retract_on_converge` (bool,
  default false); `manual_no_time_limit` (bool, default false). The existing
  `cycle_dwell_s` is the fixed dwell and keeps its key and default. The period rule
  validates against the dwell of the active mode.
- **None of the new settings is locked.** They decide when the cup comes out, not which
  samples count. Mode, target and both dwells are pending changes while automatic
  insertion runs; the two manual checkboxes apply immediately.
- **Settings-change markers** are written for `dwell_mode`, `target_uncertainty`,
  `max_dwell_s`, `manual_retract_on_converge` and `manual_no_time_limit`, as for the
  existing editable values. `min_converge_samples` goes in the cup log header with the
  other file-only values.
- **Cup log schema:** append, after the current last column, `dwell_mode`,
  `target_uncertainty`, `min_converge_samples`, `max_dwell_s`, `insertion_uncertainty`,
  `uncertainty_reason`, `end_reason`, `converged_after_s`. Written on insertion summary
  rows; blank on other row types. In fixed-dwell mode `target_uncertainty` and
  `converged_after_s` are blank and `max_dwell_s` is blank; with no time limit,
  `max_dwell_s` is blank. The existing `dwell` column keeps meaning the actual duration.
- **Picoammeter worker:** while acquiring, it polls back-to-back (no sleep after a
  read); idle polling stays at 2 Hz. The settle window remains time-based, so its
  meaning does not change with the rate.
- **Cup view for the Overview panel** gains the fields the panel needs: whether an
  insertion is in progress, the bar numbers, the uncertainty reason, elapsed time, the
  active dwell limit (or none), the dwell mode, and the last insertion's summary. The
  panel renders these and does no arithmetic.
- **Faraday Cup tab layout:** a dwell mode toggle; "Fixed dwell" and "Max dwell (target
  uncertainty)" as two fields, the inactive one disabled; the target field; the two
  manual checkboxes; the live uncertainty readout beside the existing run statistics;
  a "Last insertion" line. The tab has no insertion history table today and this spec
  does not add one.

## Testing Decisions

- A good test drives a module through its public interface and asserts on what an
  operator or the archive would see: actions returned, rows written, values displayed.
  No test reaches into private attributes, and none is shaped around making the
  implementation pass.
- **Pure uncertainty and bar functions** (new): table-driven tests on hand-made sample
  lists. Cover each reason in its precedence order, the minimum-count guard (a perfectly
  flat run of fewer than the minimum never converges), settle-window exclusion,
  over-range exclusion, `converged_after_s`, the 4×-target cap, and n_needed. Prior art:
  the existing dose model tests.
- **Scheduler**: timestamp-sequence tests as in the existing scheduler tests. Converge
  before max dwell → retract with `converged` on the next tick; no convergence → retract
  at max dwell with `max_dwell`; fixed dwell ignores `notify_converged`; pending mode,
  target and dwell changes apply at the next insertion only; fixed and max dwell are
  independent; the existing "no Qt, no time" import test still passes.
- **Faraday Cup tab with fake readings** (the end-to-end seam, and the only place the
  manual-insertion rules are tested): automatic insertion retracts on convergence and at
  max dwell and writes the right summary row; manual insertion with checkbox off never
  retracts; with checkbox on retracts on convergence and at max dwell; with no-limit on
  runs past max dwell and retracts only on convergence; manual insertions are never
  counted; display-only when actuation is disconnected or the controller is in LOCAL.
  Prior art: the existing Faraday Cup tab, insertion-origin and stop/restart tests.
- **Cup log writer**: new columns are appended after the existing ones in that order,
  existing column positions are unchanged, and blanks are written where specified.
  Prior art: the existing cup session writer tests.
- **Settings store**: each new key's default, valid range and bad-value fallback, and
  the period rule against the active mode's dwell. Prior art: the existing settings
  store tests.
- **Overview cup panel**: given a cup view, the right bar fill, colour state, target mark
  presence, reason text, timer text and last-insertion line are displayed. Prior art: the
  existing cup panel tests.
- **Picoammeter worker**: no sleep between reads while acquiring; idle interval
  unchanged. Prior art: the existing picoammeter worker tests.

## Out of Scope

- Lowering NPLC, burst/trace-buffer reads, or any other change to how the Keithley 6482
  is configured.
- Correcting the insertion uncertainty for sample-to-sample correlation. The bench
  ticket measures it; any correction is a later decision.
- Any change to dose integration (zero-order hold), beam-on intervals, or how drift
  between insertions is handled.
- A history strip of past insertions on the Overview panel, and an insertion history
  table on the Faraday Cup tab.
- Changing the settle window, run start current or run end current, or their lock.
- "No limit" for automatic insertions.

## Further Notes

- **Bench ticket (`ready-for-developer`):** with a steady beam into the cup, record a
  long acquiring run at the new back-to-back rate and report the achieved sample rate
  and the autocorrelation of the cup current at lags of 1 to 20 samples. If neighbouring
  samples are strongly correlated, the 5 % target is optimistic and the minimum sample
  count or rate should be revisited.
- Default 5 s maximum dwell is a starting value. `converged_after_s` from the first
  irradiations is the evidence for changing it.
- Insertion uncertainty is a precise sample, not a precise dose (ADR 0003 amendment D,
  consequences). On-screen help text for the target field says so in one line.
