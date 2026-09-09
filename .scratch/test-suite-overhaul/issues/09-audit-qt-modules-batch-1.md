# 09: Audit — Qt test modules, batch 1

**What to build:** A keep / rewrite / delete verdict, with a stated reason, for
every test function in the batch below. Verdicts are written to the tracker as
reviewable output. **No test is deleted, rewritten or otherwise modified in this
ticket.**

**Batch:** the Qt-importing test modules whose filename sorts at or before
`test_load_characterizer.py` — seventeen files covering the amplifier tab and
its single-channel and isolation behaviour, waveform fidelity, auto-home,
calibration wiring and the calibration runner, the camera tab, the drag panel,
dynamic adjustment and its tab, function-generator panel status and sync, the
GUI hardware integration module, the HV conditioner, the numeric input widgets,
and load characterisation and its tab.

This is where the problem lives. The Qt-dependent files hold roughly 870
private-attribute accesses under a written convention that forbids exactly that,
and only five of eighty-one test modules use the sanctioned payload path for
driving a widget with real data. Expect a high proportion of `rewrite` and
`delete`. A smaller suite here is the intended outcome, not a regression.

Two modules in this batch are the models to imitate and should be verdicted
`keep` with that noted: the GUI hardware module's test that pushes a real stream
window through the application's own beamline and asserts on rendered label
text, and any test that reaches a widget through the shared payload helpers.

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

**Status:** done

- [x] Every test function in the batch carries exactly one verdict.
- [x] Every verdict carries a reason naming which criterion it met.
- [x] Verdicts are written as a reviewable file under `.scratch/test-suite-overhaul/`.
- [x] The batch's file list is recorded in that file.
- [x] Every `rewrite` verdict names the sanctioned path the rewrite would use.
- [x] A separate "would have caught nothing" list is included.
- [x] No test file is modified.

## Comments

- **Completed Audit:** Full audit written to [audit-09-qt-modules-batch-1.md](../audit-09-qt-modules-batch-1.md).
- **Summary Statistics:**
  - Total Tests Audited: 296 across 17 Qt-dependent test files
  - **Keep:** 150 (50.7%) — Observable behavior, DSP math, safety interlocks, input handling.
  - **Rewrite:** 95 (32.1%) — Real behavior reaching private state or using fragile `mock.call` order assertions; sanctioned paths specified using `FakeAsyncHardwareManager`, QtBot, and Qt signal spies.
  - **Delete:** 51 (17.2%) — 30 private drag-panel white-box tests, 8 Qt stacked-widget navigation tautologies, 12 private flag / mock forwarding tautologies, and 1 process-polluting `sys.modules` monkeypatching test.
- **Dedicated "Would Have Caught Nothing" List:** 51 tests itemized and categorized in the audit document.
- **Zero test files or application code files modified.**

Reference: spec section "The audit" and "Prior art in this repository"; ADR 0001
decisions 5 and 6.

