# 46: The spike detector and reference capture (pure)

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/amplifier-load-and-spikes/spec.md` (Part E)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `CONTEXT.md` (Reference current, Spike threshold, Current spike)

**Read first:** `src/rbl/hardware/ac_metrics.py` (module style: pure, numpy only), `src/rbl/hardware/load_model.py` (`capacitance_from_charge` - trapezoid charge integral).

## What to build

A new pure module `src/rbl/hardware/spike_detector.py`. No Qt, no clock.

- `capture_reference(samples_ma, dt_s) -> Reference(current_ma, noise_ma)`: `current_ma` = median
  of |I|; `noise_ma` = 1.4826 x median(| |I| - current_ma |).
- `spike_threshold(ref) = max(2 * ref.current_ma, ref.current_ma + 5 * ref.noise_ma)`.
- `SpikeDetector(ref, dt_s)` with `feed(samples_ma, t0_s) -> list[Spike]`. A spike is a
  contiguous run of samples with |I| above the threshold. It keeps only the state needed to
  join a spike that runs across the end of one window into the next.
  `Spike(start_s, duration_s, peak_ma, charge_uc, peak_is_lower_bound)`: charge is the integral
  of (|I| - reference) over the run; `peak_is_lower_bound = duration_s < 100e-6`.

Tests may fake: nothing. Synthetic numpy arrays from a seeded generator.

## Acceptance criteria

- [ ] A 2 mA baseline with seeded Gaussian noise of sigma 0.1 mA: threshold is 4.0 mA within 2 %, and a 6 mA, 1 ms excursion is reported once with peak 6 mA within 5 %.
- [ ] The same baseline with sigma 1.0 mA: threshold equals reference + 5 x noise within 5 %, and one hour of pure noise at 7.5 kS/s (fed in 100 ms chunks) yields fewer than 5 spikes.
- [ ] A spike starting in the last 3 samples of one `feed` and ending in the next is reported once, with its full duration.
- [ ] At 7.5 kS/s a 3-sample spike has `peak_is_lower_bound is True`; a 20-sample spike has `False`.
- [ ] A rectangular spike 10 mA above reference for 1 ms has `charge_uc` 10 within 2 %.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
