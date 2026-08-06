# Move the amp-test work onto its own branch

Give this to Claude. It is a git-only task — no code changes.

## Situation

- `origin/master` is at `a79d6a7` and is clean.
- Local `master` has **three unpushed** amp-test commits (`edf7976`, `65b23ff`, `1fb1ac5`).
  Because nothing was pushed, they can be moved off `master` with no history rewrite.
- Phase 3 is in progress and uncommitted: `rbl/services/amp_drive.py` (new),
  `rbl/services/calibration_runner.py` (delegation edits), plus `docs/`.
- **106 of the 108 files `git status` reports as modified are pure line-ending
  churn (CRLF → LF), not real edits.** Verify with
  `git diff --ignore-all-space --stat` — it reports only two files. Never
  `git add .` or `git add -A` in this repo until §3 is done.

## 1. Create the branch and take the commits with it

```bash
git switch -c feature/amp-test-matrix     # uncommitted work follows you
git branch -f master origin/master        # rewind master; the 3 commits stay here
```

`git branch -f` only moves a pointer — it does not touch the working tree, so
nothing in progress is lost. Confirm before continuing:

```bash
git log --oneline -1 master                       # must be a79d6a7
git log --oneline origin/master..feature/amp-test-matrix   # must list the 3 commits
```

## 2. Commit the in-progress Phase 3 work — named files only

```bash
git add rbl/services/amp_drive.py rbl/services/calibration_runner.py docs/
git commit -m "feat(amp-test): phase 3 — AmpDrive extraction (WIP)"
```

Leave `processing/analyze_calibration.py` out unless it is deliberate amp-test
work; it changed independently and should be judged on its own.

## 3. Kill the line-ending noise — once, on this branch

```bash
printf '* text=auto\n' > .gitattributes
git add .gitattributes && git add --renormalize .
git commit -m "chore: normalize line endings (.gitattributes)"
```

This is one noisy commit touching ~106 files and it makes every later diff on
this branch readable. Do it now, before the branch accumulates more work.

## 4. Continue

Stay on `feature/amp-test-matrix` for Phases 3–11. Commit at each phase boundary
as the implementation document already specifies.

---

## Running it without it being "the app"

The feature reaches the GUI only at Phase 10, when `calibration_tab.py` and
`app.py` are edited. Until then the branch is additive — new files only — and
`master` is unaffected either way.

To use it: `git switch feature/amp-test-matrix`, run `python -m rbl.main`, do the
bench session. To go back to the normal app: `git switch master`. Data written to
`data/amp_tests/` is gitignored and survives both.

## Coming back later

```bash
git switch feature/amp-test-matrix
git log --oneline origin/master..HEAD      # what this branch adds
git diff master --stat                     # what it changes in the shared app
```

Push it so it is not just on one machine:

```bash
git push -u origin feature/amp-test-matrix
```

**Do not merge to `master` unless you decide the feature is permanent.** A branch
that is never merged is a perfectly good place for it to live. If you do merge
later, rebase onto `master` first so the three amp-test commits land as a clean
sequence rather than a merge bubble.

## Do not

- `git add .` / `git add -A` before §3 — it stages 106 whitespace-only files.
- `git reset --hard` anywhere in this sequence — Phase 3 is uncommitted until §2.
- `git push origin master` while the amp-test commits are still on it — check
  `git log --oneline -1 master` reads `a79d6a7` first.
