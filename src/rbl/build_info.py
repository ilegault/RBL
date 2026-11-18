"""
build_info.py
Which build of RBL is running: version, git commit, build time.

WHY THIS EXISTS
---------------
The one-folder build is copied whole onto the control PC, and the only way to
tell one copy from another used to be renaming the folder (``RBL_3.86``). That
breaks shortcuts, is easy to forget, and says nothing once the app is running.
Worse, data files carried no record of the code that produced them: in a frozen
build there is no git, so ``calibration_writer.git_commit_hash()`` returns "".

So ``build.bat`` stamps the build. Before PyInstaller runs it writes
``build_info.json`` at the repo root (gitignored); ``rbl.spec`` bundles it; this
module reads it back at runtime. ``build.bat`` also drops a human-readable
``VERSION.txt`` next to ``RBL.exe`` so the folder can be identified from
Explorer without launching anything. The folder itself stays named ``RBL``.

Consumers: the window title, the first line of ``rbl.log``, the session
recorder manifest and the calibration sidecar.

Running from source (no bundled file) reports version ``dev`` and the commit
git reports, so a dev run is never mistaken for a deployed build. Nothing here
may raise: a missing or corrupt stamp degrades to "unknown", never to a crash.

This module sits at the package root (like ``util.py``) because every layer
reads it and it belongs to none of them.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

BUILD_INFO_FILENAME = "build_info.json"

DEV_VERSION = "dev"
UNKNOWN_VERSION = "unknown"


@dataclass(frozen=True)
class BuildInfo:
    version: str          # what the developer typed into build.bat, or "dev"
    commit: str           # short git hash, "" if unknown
    dirty: bool           # built with uncommitted changes
    built_at: str         # ISO-8601 local time of the build, "" for dev runs
    frozen: bool          # running from the PyInstaller bundle

    def label(self) -> str:
        """Short form for the window title, e.g. ``v4.0 (60e7b08)``."""
        return format_label(self)

    def as_dict(self) -> dict:
        return asdict(self)


def format_label(info: BuildInfo) -> str:
    unnumbered = info.version in (DEV_VERSION, UNKNOWN_VERSION)
    version = info.version if unnumbered else f"v{info.version}"
    if not info.commit:
        return version
    commit = info.commit + ("+dirty" if info.dirty else "")
    return f"{version} ({commit})"


def parse_build_info(data: dict, frozen: bool) -> BuildInfo:
    """Build a BuildInfo from the stamp's JSON object. Missing keys degrade."""
    return BuildInfo(
        version=str(data.get("version") or UNKNOWN_VERSION),
        commit=str(data.get("commit") or ""),
        dirty=bool(data.get("dirty", False)),
        built_at=str(data.get("built_at") or ""),
        frozen=frozen,
    )


def _bundle_dir() -> Path | None:
    """Where PyInstaller unpacked the data files, or None when not frozen."""
    meipass = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and meipass:
        return Path(meipass)
    return None


def _git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=Path(__file__).resolve().parent,
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        log.debug("build_info: git %s unavailable", args, exc_info=True)
    return ""


def load_build_info(path: Path | None = None) -> BuildInfo:
    """Read the stamp at ``path`` (default: inside the bundle).

    With no stamp to read, this is a dev run: version ``dev`` and the commit
    git reports for the source tree.
    """
    bundle = _bundle_dir()
    frozen = bundle is not None
    if path is None and bundle is not None:
        path = bundle / BUILD_INFO_FILENAME
    if path is not None:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                return parse_build_info(data, frozen=frozen)
            log.warning("build_info: %s is not a JSON object", path)
        except Exception:
            log.warning("build_info: cannot read %s", path, exc_info=True)
        return BuildInfo(UNKNOWN_VERSION, "", False, "", frozen)
    commit = _git("rev-parse", "--short", "HEAD")
    dirty = bool(_git("status", "--porcelain", "--untracked-files=no"))
    return BuildInfo(DEV_VERSION, commit, dirty if commit else False, "", False)


_cached: BuildInfo | None = None


def build_info() -> BuildInfo:
    """The running build, read once per process."""
    global _cached
    if _cached is None:
        _cached = load_build_info()
    return _cached
