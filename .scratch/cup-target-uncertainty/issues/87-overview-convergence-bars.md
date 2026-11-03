# 87: Overview cup panel: uncertainty and insertion bars

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 84

**Spec:** `.scratch/cup-target-uncertainty/spec.md`
**Binding:** `docs/adr/0003-commanded-and-confirmed-cup-position.md` (amendment D, and C1), `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0002-cup-acquisition-triggered-by-current.md` (amendments A5-A8, B3)

**Read first:** `src/rbl/gui/widgets/cup_panel.py` (`on_cup_view`, `_build_ui`); `src/rbl/services/cup_log.py` (`CupView`); `src/rbl/gui/faraday_cup_tab.py` (`_emit_cup_view`, which builds `CupView` and de-duplicates it); `tests/test_cup_panel.py` (prior art); `src/rbl/gui/theme.py` for the WARN/OK colours.

## What to build

While the cup is in, the Overview cup panel shows two thin stacked bars; while it is out,
a one-line summary of the last insertion. The panel renders the view; it does no
arithmetic.

1. `CupView` gains (all defaulted so existing constructions stay valid):
   `insertion_open: bool = False`, `dwell_mode: str = "fixed"`,
   `drain_fraction: float | None = None`, `uncertainty: float | None = None`,
   `target: float | None = None`, `reason_text: str | None = None`, `n: int = 0`,
   `n_needed: int | None = None`, `time_left_s: float | None = None`,
   `converged: bool = False`, `elapsed_s: float = 0.0`,
   `dwell_limit_s: float | None = None` (fixed dwell, max dwell, or None for no limit),
   `last_insertion_text: str | None = None`.
2. The tab fills these from ticket 81's `compute_convergence_bars` and the live result
   on every sample (the existing equality de-duplication still applies) and sets
   `last_insertion_text` to the same string as `lbl_last_insertion`.
3. **Uncertainty bar** drains: fill = `drain_fraction`, a target mark at 25 % of the
   width in target mode only. Colour: WARN (amber) while working, OK (green) once
   converged, grey with `reason_text` shown in place of the numbers when a reason is
   set. In fixed mode: no target mark and never green. Text beside it:
   `f"{u*100:.1f} % → {target*100:.0f} %   n = {n} / ~{n_needed}   ~{time_left_s:.1f} s left"`
   (omit pieces that are None; omit "→ target" in fixed mode).
4. **Insertion bar**: elapsed against `dwell_limit_s`, text `f"{elapsed:.1f} s /
   {limit:.1f} s max"` (`"… s dwell"` in fixed mode); with `dwell_limit_s` None, no
   bar fill, just `f"{elapsed:.1f} s"`.
5. Both bars hidden while `insertion_open` is False; `last_insertion_text` shown instead.

## Acceptance criteria

- [ ] `tests/test_cup_panel.py`: given a `CupView` with `drain_fraction=0.5`,
      `uncertainty=0.10`, `target=0.05`, target mode, the uncertainty bar's value is
      50 % of its range, the target mark is visible, and the text contains `"10.0 %"`
      and `"5 %"`.
- [ ] Given `converged=True` the bar uses the OK colour; given `reason_text="no valid
      samples"` it shows that text and the grey colour; in fixed mode the target mark is
      hidden.
- [ ] Given `dwell_limit_s=None` and `elapsed_s=12.3` the insertion bar text is
      `"12.3 s"`.
- [ ] Given `insertion_open=False` and `last_insertion_text="last: 4.2 % · converged ·
      n = 23 · 1.8 s"`, both bars are hidden and that text is shown.
- [ ] A rig test (prior art: `test_charge_text_matches_the_faraday_tab_after_an_insertion_closes`)
      feeds a converging insertion through the real tab and asserts the panel's
      uncertainty text matches the tab's `lbl_uncertainty` percentage.

May fake: instrument feeds. Must be real: the tab, the panel, `CupView`.

## Gate

Run all four, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
