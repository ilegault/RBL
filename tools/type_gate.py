"""
type_gate.py
Run mypy once over the whole application source tree and apply the layered
gate from docs/adr/0001-tests-first-and-no-muted-failures.md, decision 4:
an error under a "hard" module (declared in pyproject.toml's
[tool.rbl.type_gate]) fails the build immediately. Every other error is
printed and counted against the ratchet recorded in tools/mypy_ratchet.txt,
which may only move down.

WHY A WRAPPER INSTEAD OF MYPY'S OWN CONFIG
-------------------------------------------
mypy has no notion of "count these errors and compare the count to a stored
figure" — that bookkeeping has to live outside mypy. This script runs the
single, unrestricted scan declared by pyproject.toml's `files = ["src/rbl"]`
(no module is excluded by name) and buckets the errors mypy already
reported in full. That is also why this replaces the old `exclude` regex
rather than reusing it: `exclude` only stops mypy from treating a file as a
scan *root* — it does not stop mypy from following an import into that file,
so the excluded module's errors were being reported anyway whenever a
checked module imported it, which is exactly what the old CI never caught
because the run never got past mypy's exit code.
"""
import pathlib
import re
import subprocess
import sys
import tomllib

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_RATCHET_FILE = _REPO_ROOT / "tools" / "mypy_ratchet.txt"
_ERROR_RE = re.compile(r"^(?P<path>.+?):\d+:(?:\d+:)?\s*error:")


def _hard_prefixes() -> list[str]:
    with open(_REPO_ROOT / "pyproject.toml", "rb") as f:
        config = tomllib.load(f)
    modules = config["tool"]["rbl"]["type_gate"]["hard"]
    return ["src/" + module.replace(".", "/") for module in modules]


def _is_hard(path: str, hard_prefixes: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    return any(normalized.startswith(prefix + "/") for prefix in hard_prefixes)


def main() -> int:
    result = subprocess.run(["mypy"], cwd=_REPO_ROOT, capture_output=True, text=True)
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)

    if result.returncode not in (0, 1):
        print(f"type_gate: mypy exited {result.returncode} unexpectedly "
              "— treating as a hard failure.", file=sys.stderr)
        return 1

    hard_prefixes = _hard_prefixes()
    hard_errors = []
    soft_errors = []
    for line in result.stdout.splitlines():
        m = _ERROR_RE.match(line)
        if not m:
            continue
        if _is_hard(m.group("path"), hard_prefixes):
            hard_errors.append(line)
        else:
            soft_errors.append(line)

    ratchet = int(_RATCHET_FILE.read_text().strip())

    print()
    print(f"type_gate: {len(hard_errors)} hard-layer error(s) "
          f"({', '.join(hard_prefixes)}); {len(soft_errors)} soft-layer "
          f"error(s) (ratchet: {ratchet}).")

    if hard_errors:
        print("type_gate: hard-layer errors fail the build unconditionally:")
        for line in hard_errors:
            print(f"  {line}")
        return 1

    if len(soft_errors) > ratchet:
        print(
            f"type_gate: soft-layer error count rose from {ratchet} to "
            f"{len(soft_errors)}. Fix the new error(s), or if every error "
            f"above is pre-existing, lower "
            f"{_RATCHET_FILE.relative_to(_REPO_ROOT)} to match — it may "
            "only decrease."
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
