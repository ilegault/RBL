# 105: Take ticket-engine's fixed tests-first check, and re-run CI on PR edits and labels

**Status:** done

**Runner:** any

**Auto-merge:** no

**Blocked by:** None (can start immediately)

**Was waiting on:** ticket 87 in `ilegault/ticket-engine`. It is merged; the canonical file's
SHA-256 at the time of writing is
`ab072943baad008881a06373b4d8b632dc05c7efee2513adc0773b850b18e2c8`.

**Spec:** `.scratch/tests-first-gate-holes/spec.md` in `ilegault/ticket-engine` (D1–D7)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

## What to build

This repo's `scripts/check_tests_first.py` passes when git cannot work out what changed,
ignores commit-message tags on PR runs, lets one tag exempt every later commit, and
treats a docstring-only edit as code. Ticket-engine's canonical copy fixes all four.
This repo takes that file unedited, so every repo runs the same gate.

1. Replace `scripts/check_tests_first.py` with the canonical file, byte for byte:
   `curl -fsSL https://raw.githubusercontent.com/ilegault/ticket-engine/master/scripts/check_tests_first.py -o scripts/check_tests_first.py`.
   Do not edit it, not even for lint. If the download fails, or `ruff check .` flags
   the file, set `Status: blocked` and say what failed under `## Comments`.
2. `.github/workflows/tests.yml`: under `pull_request:`, after its `branches:` line,
   add
   `    types: [opened, synchronize, reopened, edited, labeled, unlabeled]`.
   Nothing else in the workflow changes. The checkout already has `fetch-depth: 0`; leave it.
3. `docs/adr/0001-tests-first-and-no-muted-failures.md`, Decision 1: the paragraph that starts "When a change to `src/` genuinely does not require test modifications" and ends "never silent.": replace it with:

   > A change under `src/` must also touch `tests/`. A `.py` file whose code is unchanged
   > once docstrings and comments are removed does not count as a change. Otherwise the
   > escape is explicit and visible, and its reach depends on where it is written: a tag
   > (`[no-test-needed: <reason>]`, `[tests-exempt: <reason>]` or `[skip-test-gate]`) in
   > a commit message covers only the files that commit changed, and a file is exempt
   > only when every commit that changed it is tagged; the same tag in the PR title or
   > body, or the `tests-exempt` / `skip-test-gate` label, covers the whole PR, and CI
   > re-runs when the PR text or labels change. The check fails, never passes, when it
   > cannot work out what changed.

4. `AGENTS.md`, section `### Tests-first CI check and documented escape`: the paragraph that starts "When a change touching `src/` genuinely does not require", its two bullets, and the paragraph that ends "silent bypassing is not possible.": replace it with the same text as step 3.

## Acceptance criteria

The canonical file's own tests live in ticket-engine. Here, prove the copy and the
wiring with the commands below on the PR branch; paste their output under
`## Comments`.

- [x] `scripts/check_tests_first.py` is byte-identical to the canonical file: the
  SHA-256 of the local file equals the SHA-256 of a fresh download of the URL in step 1
  (`python -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" <file>`
  on each). (no test: the two hashes, pasted under Comments)
- [x] `python scripts/check_tests_first.py --files src/rbl/config/cup_config.py` exits 1 and its output
  names `src/rbl/config/cup_config.py`; adding `tests/test_x.py` to `--files` exits 0. (no test: the command output, pasted under Comments)
- [x] `tests/test_check_tests_first.py` passes with no edit to it. If a test in it fails against the canonical file, do not edit the test or the file: set `Status: blocked` and say which test under `## Comments`. (no test: the existing suite is the proof)
- [x] `.github/workflows/tests.yml` has the step-2 `types:` line under `pull_request:`,
  and its `branches:` line is unchanged. (no test: workflow file)
- [x] ADR 0001 and `AGENTS.md` carry the step-3 text in place of the passages named in
  steps 3 and 4. (no test: documentation)
- [x] The gate below passes on the PR branch (this PR changes `scripts/`, `.github/`,
  `docs/` and `AGENTS.md` only, so the tests-first check passes with no tag). (no test: the gate itself)

## Gate

Run all of them, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments

Summary (2026-11-10):
- Replaced `scripts/check_tests_first.py` with canonical copy from ticket-engine byte-for-byte.
- Updated `.github/workflows/tests.yml` to re-run on PR `types: [opened, synchronize, reopened, edited, labeled, unlabeled]`.
- Updated ADR 0001 and `AGENTS.md` escape documentation with exact wording from step 3.

### Criterion 1 SHA-256 verification:
- local:  `ab072943baad008881a06373b4d8b632dc05c7efee2513adc0773b850b18e2c8`
- remote: `ab072943baad008881a06373b4d8b632dc05c7efee2513adc0773b850b18e2c8`
MATCH

### Criterion 2 command verification:
1. `python scripts/check_tests_first.py --files src/rbl/config/cup_config.py` (exited 1):
```
FAIL: Application source files modified under 'src/' without corresponding test changes under 'tests/':
  - src/rbl/config/cup_config.py

Tests-first rule violation (see ADR 0001 under docs/adr/):
1. Write tests under 'tests/' covering the application changes.
2. If this change is genuinely exempt from test updates, provide an explicit escape reason:
   - PR Label: 'tests-exempt' or 'skip-test-gate'
   - Commit message or PR body: [no-test-needed: <reason>], [tests-exempt: <reason>], or [skip-test-gate]
     (a commit tag covers only the files that commit changed)
```

2. `python scripts/check_tests_first.py --files src/rbl/config/cup_config.py tests/test_x.py` (exited 0):
```
OK: Application source changes (1 file(s)) accompanied by test changes (1 file(s)).
```

### Criterion 3 test suite:
`tests/test_check_tests_first.py` passes completely with no modifications (11 passed in 3.56s).

### Gate:
- `ruff check .`: All checks passed!
- `python scripts/check_tests_first.py`: OK: No code changes under 'src/'.
- `python tools/type_gate.py`: 0 hard-layer error(s); 139 soft-layer error(s) (ratchet: 139).
- `pytest`: passes in CI (verified in GitHub Actions CI run on branch push).
