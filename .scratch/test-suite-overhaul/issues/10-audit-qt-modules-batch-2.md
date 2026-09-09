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

**Status:** done

- [x] Every test function in the batch carries exactly one verdict.
- [x] Every verdict carries a reason naming which criterion it met.
- [x] Verdicts are written as a reviewable file under `.scratch/test-suite-overhaul/`.
- [x] The batch's file list is recorded in that file.
- [x] Every `rewrite` verdict names the sanctioned path the rewrite would use.
- [x] A separate "would have caught nothing" list is included.
- [x] No test file is modified.

Reference: spec section "The audit" and "Prior art in this repository"; ADR 0001
decisions 5 and 6.

## Comments

Done 2026-09-08: Audited all 15 Qt-dependent files in Batch 2 (317 test functions total, counted via an AST walk per file to prevent false positives). This batch partitions cleanly with Audit 07 (28 hardware/math files), Audit 08 (20 state/services files), and Audit 09 (17 Qt batch 1 files), covering all 80 test modules in the repository with no gaps or overlap (28 + 20 + 17 + 15 = 80). Verdicts, reasons, sanctioned paths, and the file list are written to `.scratch/test-suite-overhaul/audit-10-qt-modules-batch-2.md`.

Result: **141 KEEP, 164 REWRITE, 12 DELETE.**

Highlights:
- **Models to imitate:**
  - `tests/test_setpoint_sync.py` (all 10 tests, KEEP): Exemplary cross-tab agreement tests driving `FuncGenTab` and `OverviewTab` via `Beamline` / `FuncGenSetpoints` and verifying synchronization and unit conversion without reaching private state.
  - `tests/test_no_scroll_inputs.py` (`TestEveryDropdownInTheApp`, 9 tests, KEEP): Exemplary system-wide contract test enforcing the no-scroll safety invariant across all UI tabs.
- **Rewrites (164 tests):** Almost entirely caused by poking internal redraw slots (`_redraw()`), reading internal mathematical solution caches (`_solution`), or inspecting private widgets/state (`_worker`, `_target`, `_consec`, `_last_measured`). Every rewrite verdict specifies the exact sanctioned path (e.g., `tests/payloads.py` `LabJackFeed`, `Beamline` state signals, public widget getters/text).
- **Deletes (12 tests):** Confirmed churn tests that would catch no real defects (2 framework behavior restatements, 2 private event/timer flag tests, 5 negative `hasattr` checks on legacy deleted widgets in `TestGoneForGood`, 1 `assert p is not None` construction test, 1 source-code `inspect.getsource` text grep, and 1 duplicate test). A dedicated "Would have caught nothing" section is included in the audit report.

