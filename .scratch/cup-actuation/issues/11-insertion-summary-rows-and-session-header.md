# 11: One row per insertion, and a header that makes the dpa traceable

**What to build:** The archive an irradiation leaves behind. One summary row per
insertion, so an experimenter can see each measurement without reading every sample. Each
stage of the dose chain in its own column, so the arithmetic can be recomputed by hand
and compared. And a session header carrying everything the calculation assumed, so
nothing in the dose chain is implicit.

After this ticket the feature is complete in software and ready for bench bring-up.

**Blocked by:** 07, 10

**Status:** done

**Read `docs/adr/0003-commanded-and-confirmed-cup-position.md` decision 7 first.**
`docs/adr/0001-tests-first-and-no-muted-failures.md` is binding.

## The insertion summary row

One per insertion, written when the run closes, carrying:

- run id
- commanded timestamp
- confirmed timestamp
- dwell
- sample count
- post-settle mean current, in amperes
- standard deviation of those samples
- beam-on seconds since the previous insertion
- running *Q*, fluence and dpa **after** this insertion

Commanded and confirmed timestamps are both recorded on every insertion. That pair is
what measures the mechanical lag from relay closure through the controller and solenoid
to the cup actually moving — continuously, for free, as a side effect of normal
operation. It is why confirmed rather than commanded position is the run boundary, and
ticket 14 reads it back out.

The standard deviation is not decoration: it is how an experimenter sees that a reading
had not settled.

The beam-on seconds column makes the interval each current was held across explicit,
rather than leaving a later reader to infer it from timestamps and guess whether
insertion time was included. It was not — the beam-on interval runs between insertions.

## The session header

Carries species, energy, charge state, irradiated area, *k* with its depth, its SRIM
version and its entry date, and the cycle period and dwell in force.

A *k* whose depth and SRIM version are not in the file produces a dpa figure that cannot
be traced to the number that produced it, and a dpa figure that cannot be traced is not a
result. If *k*'s provenance fields are absent the header says so explicitly rather than
writing a blank that reads as zero.

- [x] One summary row per insertion, written at run close, with every field above
- [x] Running *Q*, fluence and dpa are three separate columns, each recomputable by hand
      from the other columns in the file
- [x] Beam-on seconds measures the interval **between** insertions and excludes time the
      cup spent in the beam, asserted by a test over a multi-insertion sequence
- [x] The session header carries species, energy, charge state, area, *k*, *k*'s depth,
      SRIM version, entry date, cycle period and dwell
- [x] Absent provenance is written as an explicit marker, never as a blank or a zero
- [x] Every row flushes immediately, like every other row in this writer
- [x] A test builds a three-insertion sequence at known currents and intervals and
      asserts the running *Q*, fluence and dpa columns match values computed by hand in
      the test, using ticket 08's worked example as one of them
- [x] A test asserts the standard deviation column reflects the post-settle samples only,
      matching the mean's sample set
- [x] A test reads a completed session file back and reconstructs the dpa from the
      charge, charge state, area and *k* columns alone, asserting it matches the recorded
      dpa
- [x] `ruff check .`, `python scripts/check_tests_first.py`, `python tools/type_gate.py`
      and `pytest --tb=short -q -n auto --dist loadfile` all pass

## Comments

### Implementation summary
- Implemented `write_insertion_summary(...)` in `CupSessionWriter` writing one `insertion_summary` row per insertion with all 13 columns, flushed immediately after writing.
- Exposed `last_beam_on_s` on `DoseAccumulator` to record the interval between insertions excluding cup-in dwell.
- Updated session header formatting with `# session_header:` carrying species, energy, charge_state, area, k, k_depth, srim_version, entry_date, cycle_period, cycle_dwell. Absent fields default to `"NOT_SPECIFIED"` (never blank or zero).
- Added `update_session_parameters(...)` to `CupSessionWriter` allowing parameter updates during operation and header rewrites before rows are written.
- Added `species_changed = Signal(str, float, int)` to `RasterPlannerTab` and wired it in `MainWindow` to `FaradayCupTab.on_species_changed` to propagate active species, energy, and charge state.
- Verified post-settle sample collection (excluding 1.0s settle window) for computing mean and sample standard deviation (`InsertionCurrentStats`).
- Verified reconstructibility of dpa directly from CSV columns alone (`charge`, `charge_state`, `area`, `k`).
- All quality gates pass: `ruff check .` clean, `scripts/check_tests_first.py` passed, `tools/type_gate.py` passed, `scripts/check_layers.py` passed with ratchet maintained at 462, and all 1915 pytest test cases passed.
