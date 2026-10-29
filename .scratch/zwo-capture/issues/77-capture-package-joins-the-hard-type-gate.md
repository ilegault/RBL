# 77: `rbl.capture` joins the hard type gate

**Status:** done

**Runner:** any

**Auto-merge:** no

**Blocked by:** 67, 68, 69

**Spec:** `.scratch/zwo-capture/spec.md`, "Further Notes" item 12
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md` (decision 4: the per-layer
type-check bar), `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 4: the
Capture Process is a contract another implementation may replace; its types are part of it)

**Read first:** `pyproject.toml` `[tool.rbl.type_gate]` and its comment; `tools/type_gate.py`
module docstring; `tools/mypy_ratchet.txt`.

## What to build

This ticket changes gate configuration, so it is held for the developer's review
(`Auto-merge: no`) and nothing depends on it.

1. `pyproject.toml`: `hard = ["rbl.config", "rbl.hardware", "rbl.capture"]`. Extend the comment
   above it by one line: `rbl.capture` is hard because it is the Capture Process (ADR 0008),
   plain Python with no Qt, and fully typed from the start.
2. If `python tools/type_gate.py` then reports errors under `rbl.capture`, fix them **in the
   annotations of `src/rbl/capture/`**, without changing behaviour, in this same ticket. Such a
   commit touches `src/` without tests: put `[no-test-needed: type annotations only, behaviour
   covered by tests/test_capture_process.py]` in the commit message so
   `scripts/check_tests_first.py` passes, as AGENTS.md "Tests-first CI check and documented
   escape" requires. No `# type: ignore` (`warn_unused_ignores = true`), no `cast` to `Any`.
3. If fixing an error would change behaviour, stop and escalate (AGENTS.md: commit, set
   `Status: blocked`, comment here, draft PR).

## Acceptance criteria

- [x] (no test: gate configuration, checked by the gate itself) `pyproject.toml`'s `[tool.rbl.type_gate].hard` is exactly
  `["rbl.config", "rbl.hardware", "rbl.capture"]`.
- [x] (no test: gate configuration, checked by the gate itself) `python tools/type_gate.py` exits 0 with `rbl.capture` in the hard list (any error under a
  hard prefix makes it exit non-zero).
- [x] (no test: gate configuration, checked by the gate itself) The soft count it prints is not above `tools/mypy_ratchet.txt` (lower is fine; if lower,
  update the ratchet file to the new figure, as its docstring says it may only decrease).
- [x] (no test: regression, proved by the existing suite in the gate) The full suite passes with no test edited.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile

## Out of scope

- Adding any other package to the hard list.
- Any behaviour change in `rbl.capture`.

## Comments

Summary (2026-10-29): Added `rbl.capture` to `[tool.rbl.type_gate].hard` in `pyproject.toml` and documented its rationale per ADR 0008. Verified `tools/type_gate.py` passes with 0 hard errors and 139 soft errors matching `tools/mypy_ratchet.txt`. Adversarial self-review confirmed intentional type annotations under `src/rbl/capture/` immediately fail the hard type gate. Full gate passed.
