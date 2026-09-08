# Baseline — first CI run in which the tests actually executed

Ticket 01 deliverable. Every later ticket measures against these figures.

**Source:** PR #27, `refs/pull/27/merge` at `c689336` (ticket 01's branch merged
onto master). Run 2026-09-08T04:23–04:28Z, `windows-latest`, both matrix jobs.
Recorded from the run logs; ticket 01 landed without writing this file.

## Tests

| Job | Result | Wall time |
|---|---|---|
| `test (3.13)` | **1675 passed, 11 xfailed, 0 failed, 0 errors** | 183.86 s |
| `test (3.14)` | **1675 passed, 11 xfailed, 0 failed, 0 errors** | 201.75 s |

Identical on both Python versions. This is the first time the suite has ever run
in CI — before ticket 01 the type-check step exited 1 and the test step never
executed on any push.

The 11 xfails are the muted tests ticket 05 fixes. **After 05 and 06 the
expected figure is 1686 passed, 0 xfailed**, and `xfail_strict` makes any
surviving marker a build failure.

Test modules: 81. Spec's headline count of 1,621 test *functions* is unchanged;
1675 is pytest's collected-and-run count (parametrised cases expand).

## Types

```
Found 152 errors in 33 files (checked 113 source files)
type_gate: 12 hard-layer error(s) (src/rbl/config, src/rbl/hardware);
           140 soft-layer error(s) (ratchet: 132).
```

- **Hard layer (fails the build): 12.** `funcgen_driver` 9, `camera_source` 2,
  `load_model.py:135` 1. The first eleven are third-party stub gaps in `pyvisa`
  and `cv2`, not defects in this code; the twelfth is real.
- **Soft layer: 140.** The committed ratchet says 132 — measured somewhere other
  than a Windows runner with these stub versions. **The true starting figure is
  140** and the file must be corrected to it before it can ratchet downward.

## Lint

`ruff check .` passes on both jobs, with one warning worth fixing under ticket 03:

```
warning: Invalid `# noqa` directive on tests\test_tab_persistence.py:68:
         expected a comma-separated list of codes
```

That is the same test the spec singles out for its dead assignment — the malformed
`# noqa` is why the unused-variable rule never fired on it.

## Consequence for the frontier

The suite is **green**. Only the type gate is red, and for two specific
mechanical reasons above. Tickets 03, 04 and 05 can be verified against a clean
test background; a test failure in any of their CI runs is theirs, not
pre-existing noise.
