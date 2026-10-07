# 50: The spike chart: peak current vs duration on log-log axes, live and from past runs

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 47, 45

**Spec:** `.scratch/amplifier-load-and-spikes/spec.md` (Part E)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/load_characterization_tab.py` (after 45; its matplotlib figure set-up in `__init__`), `src/rbl/services/spike_recorder.py` (`spike_recorded`, the `spikes.csv` columns).

## What to build

A "Spikes" panel on the Load Characterization tab with its own matplotlib figure: log-log,
x "Spike duration (s)", y "Peak current (mA)". Zones: shading above 100 mA labelled
`over burst rating`, shading above 20 mA right of x = 4 ms labelled `beyond 4 ms burst`, and
horizontal lines at 20 and 100 mA. One marker colour per plate; markers hollow where
`peak_is_lower_bound`. It updates live from `spike_recorded` and has an "Open spike file..."
button behind a replaceable `_ask_spike_file() -> Path | None` that loads a past `spikes.csv`.

Tests may fake: `_ask_spike_file`. The CSV read is real (a file written in the test).

## Acceptance criteria

- [ ] Loading a CSV with three spikes on two plates draws three markers in two colours on axes whose x and y scales are both `log`.
- [ ] A spike with `peak_is_lower_bound` true is drawn with face colour `none`.
- [ ] A live `spike_recorded` emission adds one marker without reloading.
- [ ] Artists labelled `over burst rating` and `beyond 4 ms burst` exist, and lines at y = 20 and y = 100.
- [ ] `_ask_spike_file` returning `None` changes nothing on the chart.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
