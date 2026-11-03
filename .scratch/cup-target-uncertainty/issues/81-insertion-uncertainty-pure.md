# 81: Insertion uncertainty and convergence bars as pure functions

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/cup-target-uncertainty/spec.md`
**Binding:** `docs/adr/0003-commanded-and-confirmed-cup-position.md` (amendment D, and C1), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/hardware/dose_model.py` (`compute_insertion_current`, `InsertionCurrentStats`); `CONTEXT.md` entries *Insertion uncertainty*, *Dwell mode*, *Converged insertion*.

## What to build

The arithmetic every later ticket uses to decide whether an insertion is precise enough,
and the numbers the Overview bars display. Pure Python in `src/rbl/hardware/dose_model.py`:
no Qt, no clock, no I/O. It is pure so the stopping rule can be tested with lists of
numbers and no hardware; keep it that way.

1. **Prefactor.** Extract the post-settle sample selection inside
   `compute_insertion_current` into a private helper `_post_settle_values(samples,
   start_t, settle_window_s) -> tuple[list[float], int, int]` returning (valid
   post-settle values, excluded count, count of post-settle readings including invalid
   ones). `compute_insertion_current` calls it and its behaviour and existing tests are
   unchanged. The new function below calls the same helper, so the mean and the
   uncertainty can never be computed over different samples.
2. **`UncertaintyReason`** (`enum.Enum`, string values): `NO_VALID_SAMPLES =
   "no_valid_samples"`, `TOO_FEW_SAMPLES = "too_few_samples"`, `MEAN_NEGATIVE =
   "mean_negative"`, `MEAN_BELOW_RUN_END = "mean_below_run_end"`.
   Function `uncertainty_reason_text(reason, n, min_samples) -> str` returns exactly:
   `"no valid samples"`, `f"too few samples ({n}/{min_samples})"`, `"mean negative"`,
   `"mean below run end current"`.
3. **`InsertionUncertainty`** (frozen dataclass): `uncertainty: float | None` (a
   fraction, 0.05 means 5 %), `reason: UncertaintyReason | None` (None exactly when
   `uncertainty` is not None), `n: int` (valid post-settle samples), `mean_a: float`,
   `std_a: float`, `converged: bool`, `post_settle_elapsed_s: float` (last post-settle
   sample time minus `start_t + settle_window_s`, 0.0 when none),
   `mean_spacing_s: float | None` (mean interval between post-settle samples, None when
   n < 2).
4. **`compute_insertion_uncertainty(samples, start_t, settle_window_s, target,
   min_samples, run_end_current_a) -> InsertionUncertainty`.** Same sample shapes as
   `compute_insertion_current`. Reason, checked in this order: `NO_VALID_SAMPLES` when
   there is at least one post-settle reading and none is valid; `TOO_FEW_SAMPLES` when
   n < `min_samples` (this includes no post-settle readings yet); `MEAN_NEGATIVE` when
   mean < 0; `MEAN_BELOW_RUN_END` when mean <= `run_end_current_a`. Otherwise
   `uncertainty = std / (sqrt(n) * mean)` with the sample standard deviation (ddof=1),
   and `converged = uncertainty <= target`. `converged` is False whenever `reason` is set.
5. **`ConvergenceBars`** (frozen dataclass) and **`compute_convergence_bars(result,
   target) -> ConvergenceBars`**: fields `drain_fraction: float | None` =
   `min(uncertainty, 4*target) / (4*target)`; `target_mark: float = 0.25`; `n: int`;
   `n_needed: int | None` = `max(min_samples, ceil((std / (mean*target))**2))` (take
   `min_samples` as a parameter); `time_left_s: float | None` = `max(0, n_needed - n) *
   mean_spacing_s`. All three are None when `result.reason` is set; `time_left_s` is
   None when `mean_spacing_s` is None. Final signature:
   `compute_convergence_bars(result, target, min_samples)`. These numbers are display
   only; nothing may use them to decide a retract.

## Acceptance criteria

- [ ] `tests/test_dose_model.py` has a table-driven test asserting, for hand-made
      `(t, current)` lists, the exact `reason` for each of the four cases and that the
      precedence holds: a list whose only post-settle readings are `None` gives
      `NO_VALID_SAMPLES`, not `TOO_FEW_SAMPLES`; a negative mean below the run end
      current gives `MEAN_NEGATIVE`.
- [ ] A test asserts nine identical post-settle samples with `min_samples=10` are not
      converged (`TOO_FEW_SAMPLES`, text `"too few samples (9/10)"`) and ten of the
      same value (std 0) are converged with `uncertainty == 0.0`.
- [ ] A test asserts samples inside the settle window and `None`/non-finite values are
      excluded, and that `uncertainty` equals `std/(sqrt(n)*mean)` computed by hand for
      a known list (use `pytest.approx`).
- [ ] A test asserts `compute_convergence_bars`: an uncertainty of 0.30 with target 0.05
      gives `drain_fraction == 1.0`; 0.05 gives `0.25`; `n_needed` and `time_left_s` for
      a hand-computed case; all None when a reason is set.
- [ ] The existing `compute_insertion_current` tests pass unchanged, and `dose_model.py`
      still imports neither PySide6 nor `time`.

Tests use real functions only; nothing may be faked.

## Gate

Run all four, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
