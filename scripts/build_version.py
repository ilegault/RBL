"""
build_version.py
The version helpers build.bat calls: check a typed version, stamp the Windows
version resource, and show the last build this PC deployed.

WHY THIS EXISTS
---------------
build.bat used to stamp versions with inline PowerShell, and two things went
wrong. It rewrote the tracked ``version_info.txt`` before checking git for
uncommitted changes, so every build reported itself dirty. And its rewrite
updated the version numbers but silently left the version strings at an old
value. So the logic lives here, where it is tested (tests/test_build_version.py),
and a build never edits a tracked file: ``version_info.txt`` is a template, and
the stamped copy goes to ``build/version_info.txt`` (gitignored, wiped each
build).

After a successful deploy build.bat copies ``build_info.json`` to
``last_build.json`` (gitignored). The ``last`` command reads it back so the
version prompt shows what the new build follows.

Usage (from the repo root, venv active):
    python scripts/build_version.py last
    python scripts/build_version.py check 4.3
    python scripts/build_version.py resource 4.3
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_NAME = "version_info.txt"
STAMP_NAME = "build_info.json"
LAST_BUILD_NAME = "last_build.json"

_VERSION_RE = re.compile(r"\d+(\.\d+){0,3}", re.ASCII)
_MAX_FIELD = 65535  # Windows version-resource fields are 16-bit

_INVALID_HINT = (
    "use 1 to 4 whole numbers separated by dots, each 0-65535 (e.g. 4.3 or 4.2.1)."
)


def parse_version(text: str) -> tuple[int, int, int, int]:
    """``"4.2"`` -> ``(4, 2, 0, 0)``. Anything else raises ValueError."""
    if not _VERSION_RE.fullmatch(text):
        raise ValueError(f'Invalid version "{text}": {_INVALID_HINT}')
    fields = [int(part) for part in text.split(".")]
    if any(value > _MAX_FIELD for value in fields):
        raise ValueError(f'Invalid version "{text}": {_INVALID_HINT}')
    fields += [0] * (4 - len(fields))
    return fields[0], fields[1], fields[2], fields[3]


def stamp_version_resource(template: str, version: str) -> str:
    """Return ``template`` with all four version fields set to ``version``.

    Each field must appear exactly once, so an edited template can never turn
    the stamp into a silent no-op.
    """
    a, b, c, d = parse_version(version)
    numbers = f"({a}, {b}, {c}, {d})"
    dotted = f"{a}.{b}.{c}.{d}"
    replacements = [
        ("filevers", r"filevers=\([^)]*\)", f"filevers={numbers}"),
        ("prodvers", r"prodvers=\([^)]*\)", f"prodvers={numbers}"),
        ("FileVersion", r"StringStruct\('FileVersion',\s*'[^']*'\)",
         f"StringStruct('FileVersion', '{dotted}')"),
        ("ProductVersion", r"StringStruct\('ProductVersion',\s*'[^']*'\)",
         f"StringStruct('ProductVersion', '{dotted}')"),
    ]
    result = template
    for field, pattern, new in replacements:
        result, count = re.subn(pattern, lambda _m, new=new: new, result)
        if count != 1:
            raise ValueError(
                f"{TEMPLATE_NAME}: expected exactly one {field} field, found {count}"
            )
    return result


def read_last_build(path: Path) -> dict | None:
    """The recorded last deploy, or None if missing or unreadable. Never raises."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def commits_since(commit: str, repo: Path) -> int | None:
    """How many commits HEAD is past ``commit``, or None if git can't say."""
    if not commit:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{commit}..HEAD"],
            cwd=repo, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def _format_built_at(value: str) -> str:
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}):\d{2}", value)
    return f"{match.group(1)} {match.group(2)}" if match else value


def describe_last_build(record: dict | None, since: int | None) -> str:
    """The one line build.bat prints above its version prompt."""
    if record is None:
        return "Last deployed build: none recorded on this PC"
    version = str(record.get("version") or "")
    parts = [f"Last deployed build: v{version}" if version
             else "Last deployed build: version unknown"]
    commit = str(record.get("commit") or "")
    if commit:
        parts.append(f"commit {commit}" + ("+dirty" if record.get("dirty") else ""))
    built_at = str(record.get("built_at") or "")
    if built_at:
        parts.append(f"built {_format_built_at(built_at)}")
    if since == 0:
        parts.append("(HEAD is that commit)")
    elif since == 1:
        parts.append("(1 commit since)")
    elif since is not None:
        parts.append(f"({since} commits since)")
    return "  ".join(parts)


_USAGE = "usage: build_version.py last | check VERSION | resource VERSION"


def main(argv: list[str], root: Path = REPO_ROOT) -> int:
    if argv == ["last"]:
        record = read_last_build(root / LAST_BUILD_NAME)
        commit = str(record.get("commit") or "") if record else ""
        print(describe_last_build(record, commits_since(commit, root)))
        return 0
    if len(argv) == 2 and argv[0] == "check":
        try:
            parse_version(argv[1])
        except ValueError as exc:
            print(f"  {exc}")
            return 2
        return 0
    if len(argv) == 2 and argv[0] == "resource":
        try:
            with open(root / TEMPLATE_NAME, encoding="ascii", newline="") as f:
                template = f.read()
            stamped = stamp_version_resource(template, argv[1])
            out_dir = root / "build"
            out_dir.mkdir(parents=True, exist_ok=True)
            with open(out_dir / TEMPLATE_NAME, "w", encoding="ascii", newline="") as f:
                f.write(stamped)
        except (OSError, ValueError) as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)
            return 2
        print(f"  -> build\\{TEMPLATE_NAME} stamped "
              + ".".join(str(n) for n in parse_version(argv[1])))
        return 0
    print(_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
