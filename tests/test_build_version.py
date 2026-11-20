"""build.bat's version helpers (scripts/build_version.py) and the files a build writes.

Two bugs these guard: every build reported "uncommitted changes" because the build
rewrote tracked files (version_info.txt, and build_info.json, which was committed
despite .gitignore); and the version-resource rewrite updated the numbers but left
the FileVersion/ProductVersion strings stale.
"""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "build_version", ROOT / "scripts" / "build_version.py")
assert _spec is not None and _spec.loader is not None
bv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bv)

TEMPLATE = (ROOT / "version_info.txt").read_text(encoding="ascii")
BUILD_BAT = (ROOT / "build.bat").read_text(encoding="utf-8", errors="ignore")


# --- parse_version ---------------------------------------------------------

def test_parse_version_pads_to_four_fields():
    assert bv.parse_version("4.2") == (4, 2, 0, 0)
    assert bv.parse_version("3.87.1") == (3, 87, 1, 0)
    assert bv.parse_version("5") == (5, 0, 0, 0)
    assert bv.parse_version("1.2.3.4") == (1, 2, 3, 4)


@pytest.mark.parametrize("bad", [
    "", "4.", ".4", "v4.2", "4.2a", "1.2.3.4.5", "4.-1", "70000", " 4.2", "4.2 ", "٤.2",
])
def test_parse_version_rejects_malformed(bad):
    with pytest.raises(ValueError):
        bv.parse_version(bad)


# --- stamp_version_resource -----------------------------------------------

def test_stamp_sets_numbers_and_strings():
    stamped = bv.stamp_version_resource(TEMPLATE, "4.2")
    assert "filevers=(4, 2, 0, 0)" in stamped
    assert "prodvers=(4, 2, 0, 0)" in stamped
    assert "StringStruct('FileVersion', '4.2.0.0')" in stamped
    assert "StringStruct('ProductVersion', '4.2.0.0')" in stamped
    before, after = TEMPLATE.splitlines(), stamped.splitlines()
    assert len(before) == len(after)
    changed = [b for b, a in zip(before, after) if b != a]
    assert len(changed) == 4


def test_stamp_refuses_a_template_missing_a_field():
    broken = "\n".join(
        line for line in TEMPLATE.splitlines() if "'ProductVersion'" not in line)
    with pytest.raises(ValueError, match="ProductVersion"):
        bv.stamp_version_resource(broken, "4.2")


def test_committed_template_is_unstamped():
    assert "filevers=(0, 0, 0, 0)" in TEMPLATE
    assert "prodvers=(0, 0, 0, 0)" in TEMPLATE
    assert "'FileVersion', '0.0.0.0'" in TEMPLATE
    assert "'ProductVersion', '0.0.0.0'" in TEMPLATE


# --- the "Last deployed build" line ----------------------------------------

RECORD = {"version": "4.2", "commit": "e207658", "dirty": False,
          "built_at": "2026-11-20T08:23:24"}


def test_describe_last_build_when_none_recorded():
    assert bv.describe_last_build(None, None) == \
        "Last deployed build: none recorded on this PC"


def test_describe_last_build_clean_with_commits_since():
    assert bv.describe_last_build(RECORD, 3) == (
        "Last deployed build: v4.2  commit e207658  built 2026-11-20 08:23"
        "  (3 commits since)")


def test_describe_last_build_dirty_at_head():
    line = bv.describe_last_build({**RECORD, "dirty": True}, 0)
    assert line == ("Last deployed build: v4.2  commit e207658+dirty"
                    "  built 2026-11-20 08:23  (HEAD is that commit)")


def test_describe_last_build_one_commit_is_singular():
    assert bv.describe_last_build(RECORD, 1).endswith("(1 commit since)")


def test_describe_last_build_without_git_omits_the_count():
    assert bv.describe_last_build(RECORD, None).endswith("built 2026-11-20 08:23")


def test_describe_last_build_missing_keys_degrade():
    assert bv.describe_last_build({}, None) == "Last deployed build: version unknown"


def test_read_last_build_tolerates_missing_corrupt_and_bom(tmp_path):
    p = tmp_path / "last_build.json"
    assert bv.read_last_build(p) is None
    p.write_text("{not json", encoding="utf-8")
    assert bv.read_last_build(p) is None
    p.write_text("[1]", encoding="utf-8")
    assert bv.read_last_build(p) is None
    p.write_text("﻿" + json.dumps(RECORD), encoding="utf-8")
    assert bv.read_last_build(p) == RECORD


# --- the CLI build.bat calls -----------------------------------------------

def test_main_check_and_resource(tmp_path, capsys):
    (tmp_path / "version_info.txt").write_text(TEMPLATE, encoding="ascii")
    assert bv.main(["check", "4.2"], tmp_path) == 0
    assert bv.main(["check", "x"], tmp_path) == 2
    assert 'Invalid version "x"' in capsys.readouterr().out
    assert bv.main(["resource", "4.2"], tmp_path) == 0
    stamped = (tmp_path / "build" / "version_info.txt").read_text(encoding="ascii")
    assert "'FileVersion', '4.2.0.0'" in stamped
    assert (tmp_path / "version_info.txt").read_text(encoding="ascii") == TEMPLATE


def test_main_last_reads_the_record(tmp_path, capsys):
    assert bv.main(["last"], tmp_path) == 0
    assert "none recorded" in capsys.readouterr().out
    (tmp_path / "last_build.json").write_text(json.dumps(RECORD), encoding="utf-8")
    assert bv.main(["last"], tmp_path) == 0
    assert "v4.2  commit e207658" in capsys.readouterr().out


def test_main_rejects_unknown_arguments():
    assert bv.main([], ROOT) == 2
    assert bv.main(["resource"], ROOT) == 2


# --- the files a build writes stay out of git -------------------------------

def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def test_build_stamp_files_are_ignored_and_untracked():
    assert _git("ls-files", "build_info.json", "last_build.json").stdout == ""
    assert _git("check-ignore", "-q", "build_info.json").returncode == 0
    assert _git("check-ignore", "-q", "last_build.json").returncode == 0


def test_build_bat_never_writes_the_committed_template():
    assert "WriteAllText('version_info.txt'" not in BUILD_BAT
    assert r"scripts\build_version.py resource" in BUILD_BAT


def test_build_bat_records_last_build_only_after_deploy():
    deploy = BUILD_BAT.index('xcopy /E /I /Y "dist"')
    assert BUILD_BAT.index("copy /Y build_info.json last_build.json") > deploy
