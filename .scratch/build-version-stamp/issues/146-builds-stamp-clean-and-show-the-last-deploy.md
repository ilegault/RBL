# 146: A clean build is stamped clean, and build.bat shows the last deployed build

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Claimed-by:** none (receipt: the planner session made this change directly at the developer's request, 2026-11-20)

**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

## Why

Every build stamped itself "(uncommitted changes)", even from a clean tree. `build.bat` rewrote the
tracked `version_info.txt` before its `git status --porcelain --untracked-files=no` check, and
`build_info.json` was listed in `.gitignore` but had been committed in #202, so git still tracked it.
Separately, the PowerShell rewrite of `version_info.txt` updated `filevers`/`prodvers` but left the
`FileVersion`/`ProductVersion` strings at `3.86.0.0`. And the version prompt gave no hint of what the
last build was.

## What changed

- `scripts/build_version.py` (new): `parse_version`, `stamp_version_resource` (each of the four fields
  must match exactly once, so a stamp can't silently no-op), `read_last_build`, `commits_since`,
  `describe_last_build`, and a CLI: `last`, `check VERSION`, `resource VERSION`.
- `build.bat`: refuses to start without `python` on PATH; prints the last deployed build
  (e.g. `Last deployed build: v4.2  commit e207658  built 2026-11-20 08:23  (3 commits since)`);
  validates the typed version and re-asks on a bad one; writes the stamped resource to
  `build\version_info.txt` after cleaning; after a successful deploy copies `build_info.json` to
  `last_build.json`. The inline PowerShell that edited `version_info.txt` is gone.
- `version_info.txt`: now an unstamped template (`0.0.0.0`) that no build edits.
- `rbl.spec`: with `RBL_VERSION_RESOURCE=1`, uses `build\version_info.txt`, else falls back to the
  template with a warning. Still off by default.
- `.gitignore`: `last_build.json`.
- `build_info.json`: removed from the index (`git rm --cached build_info.json`, run by the developer).

## Acceptance criteria

- [x] Test `tests/test_build_version.py` (new): version parsing and rejection, stamping sets numbers and
      strings and changes exactly four lines, a template missing a field is refused, the committed
      template is unstamped, the last-build line in each shape, the CLI, `build_info.json` and
      `last_build.json` untracked and ignored, `build.bat` never writes the template and records the
      last build only after `xcopy` succeeds. Seen red before the change (5 failing), green after
      except the untracked check, which passes once `git rm --cached build_info.json` is run.
- [x] `ruff check` clean on the new files.
- [ ] By hand: from a clean tree, `build.bat` stamps `commit <hash>` with no "(uncommitted changes)",
      and `git status` is clean afterwards; the next run's first line names the build just deployed.
