# 08: The dose chain, and a displacement coefficient that can be traced

**What to build:** The arithmetic that turns measured cup currents into a dpa figure, and
the operator inputs that figure depends on. Charge, fluence and dpa are each computed
separately so a later reader can recompute any stage by hand and compare.

After this ticket an operator can enter the displacement coefficient with its provenance
and see the irradiated area arrive automatically from the Raster Planner. Nothing
accumulates yet — that is ticket 11 — but every number the accumulation needs exists and
is tested against hand-worked values.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

**Read `docs/adr/0003-commanded-and-confirmed-cup-position.md` decision 7 first**, and
`CONTEXT.md`'s "Cup actuation and dose" section for the vocabulary.
`docs/adr/0001-tests-first-and-no-muted-failures.md` is binding.

## Structural requirements

- **The arithmetic is pure functions over plain floats in `rbl/hardware/`**, in the
  manner of `raster_model.py` and `hv_interlock.py`. It does not live in the writer and
  it does not live in the tab. This is what makes it testable against numbers worked by
  hand, which is the only check this chain will ever get.
- The accumulator is likewise pure: it takes timestamps as inputs and never calls the
  clock. No Qt anywhere on this path.
- **The zero-order hold is an approximation and the docstring says so plainly.** About
  one percent of an eight-hour irradiation is measured; the rest is assumed constant
  between samples. The error is whatever the beam drifted and it is not bounded by
  anything the application can observe. Do not write a docstring that implies otherwise,
  and do not add interpolation, a drift model, or any use of slit current to fill the
  gaps — the slit currents are a relative centring signal read through log amps whose
  absolute calibration nobody relies on, and cannot be integrated into a charge.

## The four stages

1. **Insertion current** — the mean of one run's samples taken **after** the settle
   window, in amperes, with its sample count and standard deviation. Samples inside the
   settle window are recorded but excluded from the mean, because the picoammeter is
   still autoranging there and its readings are not yet trustworthy.
2. **Accumulated charge** *Q* — zero-order hold. Each insertion's current is held
   constant across the beam-on interval it represents and multiplied by that interval's
   length; the products accumulate. The beam-on interval runs **between** insertions, not
   during them: while the cup is in the beam the specimen is not being irradiated.
3. **Fluence** phi = *Q* / (*q* · *e* · *A*), with *q* the charge state from the species
   table, *e* the elementary charge, and *A* the irradiated area in cm^2.
4. **dpa** = phi · *k*.

New constant: per-insertion settle window, `1.0` s default, in `rbl/config/cup_config.py`.
Ticket 13 measures the real value; `1.0` s is a placeholder and its docstring must say so.

## The irradiated area comes from the Raster Planner

Do not add another pair of fields for the operator to type. The Raster Planner tab
already holds the patch dimensions the operator has set — `sb_patch_x_mm` and
`sb_patch_y_mm`, full width X and full height Y in mm. Those are the irradiated area.

Wire it as a signal, not an import: the planner emits its patch dimensions when they
change, following the `slit_targets_ready` precedent already in that tab, and
`MainWindow.__init__` connects it to the Faraday Cup tab with a comment saying why the
connection exists. **No gui -> gui import** — `scripts/check_layers.py` must stay clean,
and four upward imports already exist and are documented as defects.

The cup tab displays the area it is using and where it came from, so an operator who
changes the patch size mid-session can see the dose calculation follow.

## The coefficient and its provenance

*k* is operator-entered and cannot be derived by this application. It comes from SRIM,
it is depth-dependent, and nothing in the repository can compute it. It is entered with
its **depth**, its **SRIM version**, and its **date of entry**. A dpa figure whose
coefficient cannot be traced to a version and a depth is not a result.

Numeric entry uses `gui/widgets/inputs.py`, never a bare spin box.

- [ ] Pure functions in `rbl/hardware/` for: post-settle mean and standard deviation of
      one insertion's samples; charge from one current and one interval; fluence from
      charge, charge state and area; dpa from fluence and coefficient
- [ ] A pure accumulator object summing charge across insertions, taking timestamps as
      inputs, with no Qt import and no clock call
- [ ] Nothing on this path imports PySide6
- [ ] Tests against hand-worked numbers: one insertion held across a known interval;
      several insertions at different currents; a zero-current insertion contributing
      zero charge without producing NaN
- [ ] One full worked chain is tested with its expected values written into the test:
      I = 1.0e-9 A held for 300.0 s gives Q = 3.0e-7 C; patch 5.0 mm x 10.0 mm gives
      A = 0.5 cm^2; q = 3 and e = 1.602176634e-19 C give phi = 1.248302e12 ions/cm^2;
      k = 1.0e-15 gives dpa = 1.248302e-3
- [ ] A test asserts samples inside the settle window are excluded from the mean and that
      the excluded count is reported rather than silently dropped
- [ ] The Raster Planner emits its patch X and Y when they change; `MainWindow.__init__`
      connects it to the Faraday Cup tab with an explanatory comment
- [ ] `scripts/check_layers.py` reports no new upward import
- [ ] The Faraday Cup tab shows the area in use and its source, and fields for *k*, its
      depth, its SRIM version and its entry date, all via `gui/widgets/inputs.py`
- [ ] A test asserts changing the planner's patch dimensions changes the area the cup tab
      reports, through the real signal path
- [ ] The accumulator's module docstring states the zero-order hold's approximation and
      that its error is unbounded by anything the application observes
- [ ] `ruff check .`, `python scripts/check_tests_first.py`, `python tools/type_gate.py`
      and `pytest --tb=short -q -n auto --dist loadfile` all pass
