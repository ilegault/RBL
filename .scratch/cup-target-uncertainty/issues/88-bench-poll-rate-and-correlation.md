# 88: Bench: measure the acquiring poll rate and sample-to-sample correlation

**Status:** ready-for-developer

**Runner:** developer

**Auto-merge:** yes

**Blocked by:** 82

**Spec:** `.scratch/cup-target-uncertainty/spec.md`
**Binding:** `docs/adr/0003-commanded-and-confirmed-cup-position.md` (amendment D, and C1), `docs/adr/0001-tests-first-and-no-muted-failures.md`

## What to build

Bench work for the developer. Ticket 82 removed the sleep between reads while acquiring;
nobody yet knows the rate that results, or whether neighbouring samples are independent.
Insertion uncertainty assumes they are (ADR 0003 amendment D, consequences). If they are
not, the target is optimistic.

1. With a steady beam into the cup and a test cup log open, force-start a run and leave
   it acquiring for at least 120 s. Repeat at two beam currents a decade apart.
2. From the cup log sample rows, compute the achieved sample rate (median interval) and
   the autocorrelation of the post-settle current at lags 1 to 20 samples.
3. Record the results under `## Comments` here, plus a recommendation: keep
   `min_converge_samples = 10`, raise it, or revisit the rate. Note the measured settle
   behaviour too if it bears on the 1.0 s settle window placeholder.

## Acceptance criteria

- [ ] Achieved acquiring sample rate recorded for both currents.
- [ ] Autocorrelation at lags 1-20 recorded (table or plot path) for both currents.
- [ ] A recommendation on `min_converge_samples` and the target recorded under
      `## Comments`.

No code change is required by this ticket. If one follows, it is a new ticket.

## Comments
