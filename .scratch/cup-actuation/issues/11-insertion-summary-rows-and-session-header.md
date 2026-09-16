# 11: One row per insertion, and a header that makes the dpa traceable

**What to build:** The archive an irradiation leaves behind. One summary row per
insertion, so an experimenter can see each measurement without reading every sample. Each
stage of the dose chain in its own column, so the arithmetic can be recomputed by hand
and compared. And a session header carrying everything the calculation assumed, so
nothing in the dose chain is implicit.

After this ticket the feature is complete in software and ready for bench bring-up.

**Blocked by:** 07, 10

**Status:** ready-for-agent

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

- [ ] One summary row per insertion, written at run close, with every field above
- [ ] Running *Q*, fluence and dpa are three separate columns, each recomputable by hand
      from the other columns in the file
- [ ] Beam-on seconds measures the interval **between** insertions and excludes time the
      cup spent in the beam, asserted by a test over a multi-insertion sequence
- [ ] The session header carries species, energy, charge state, area, *k*, *k*'s depth,
      SRIM version, entry date, cycle period and dwell
- [ ] Absent provenance is written as an explicit marker, never as a blank or a zero
- [ ] Every row flushes immediately, like every other row in this writer
- [ ] A test builds a three-insertion sequence at known currents and intervals and
      asserts the running *Q*, fluence and dpa columns match values computed by hand in
      the test, using ticket 08's worked example as one of them
- [ ] A test asserts the standard deviation column reflects the post-settle samples only,
      matching the mean's sample set
- [ ] A test reads a completed session file back and reconstructs the dpa from the
      charge, charge state, area and *k* columns alone, asserting it matches the recorded
      dpa
- [ ] `ruff check .`, `python scripts/check_tests_first.py`, `python tools/type_gate.py`
      and `pytest --tb=short -q -n auto --dist loadfile` all pass
