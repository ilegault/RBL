# 10: Audit — Qt test modules, batch 2

**What to build:** A keep / rewrite / delete verdict, with a stated reason, for
every test function in the batch below. Verdicts are written to the tracker as
reviewable output. **No test is deleted, rewritten or otherwise modified in this
ticket.**

**Batch:** the Qt-importing test modules whose filename sorts after
`test_load_characterizer.py` — sixteen files covering the no-scroll inputs,
the overview tab, the ramp engine, the raster planner tab, the recording panel,
the regulation monitor and its response, scope acquisition and port handling,
the session recorder, setpoint sync, the slit control and its cross-screen
behaviour, the TDS waveform path, the video recorder and transcoder, and the
mini widgets.

This is where the problem lives. The Qt-dependent files hold roughly 870
private-attribute accesses under a written convention that forbids exactly that,
and only five of eighty-one test modules use the sanctioned payload path for
driving a widget with real data. Expect a high proportion of `rewrite` and
`delete`. A smaller suite here is the intended outcome, not a regression.

Any test in this batch that reaches a widget through the shared payload helpers
is a model to imitate; verdict it `keep` and note that.

**Criteria**, in the vocabulary of the decision record:

- **Delete** — a *churn test*: it fails when code is restructured without
  behaviour changing, and would not have caught a real defect. Also delete if it
  restates a guarantee the framework already makes (that a stack widget sets the
  index it was told to set), or if it writes the private state it then reads back.
- **Rewrite** — covers real behaviour but reaches it by calling a private method
  or reading a private attribute. A rewrite drives the widget through the
  sanctioned data path: the shared payload helpers, or a real beamline.
- **Keep** — drives the production path and asserts on something an operator
  could see or a downstream consumer reads.

Also report, separately from the verdicts, which tests in this batch **would
have caught nothing** — so the reviewer can see what the suite is actually
paying for.

**Blocked by:** 06

**Status:** ready-for-agent

- [ ] Every test function in the batch carries exactly one verdict.
- [ ] Every verdict carries a reason naming which criterion it met.
- [ ] Verdicts are written as a reviewable file under `.scratch/test-suite-overhaul/`.
- [ ] The batch's file list is recorded in that file.
- [ ] Every `rewrite` verdict names the sanctioned path the rewrite would use.
- [ ] A separate "would have caught nothing" list is included.
- [ ] No test file is modified.

Reference: spec section "The audit" and "Prior art in this repository"; ADR 0001
decisions 5 and 6.
