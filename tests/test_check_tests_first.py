"""
tests/test_check_tests_first.py
Test suite for the tests-first CI gate in scripts/check_tests_first.py.

Verifies:
1. Changes touching src/ without tests/ fail.
2. Changes touching src/ AND tests/ pass.
3. Changes touching only docs, scripts, tests, or config pass.
4. Escape mechanisms (commit tags, PR labels, explicit reason) grant exemptions.
5. CLI arguments and GitHub Actions event payload extraction work correctly.
"""
from __future__ import annotations

import json
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.check_tests_first import (
    categorize_files,
    evaluate_tests_first,
    extract_event_info,
    find_escape_reasons,
    main,
    normalize_path,
)


def test_normalize_path():
    assert normalize_path("src\\rbl\\gui\\app.py") == "src/rbl/gui/app.py"
    assert normalize_path("./tests/test_app.py") == "tests/test_app.py"
    assert normalize_path(".\\docs\\adr\\0001.md") == "docs/adr/0001.md"


def test_categorize_files():
    files = [
        "src/rbl/config/theme.py",
        "src/rbl/state/beamline.py",
        "tests/test_theme.py",
        "docs/adr/0001.md",
        "scripts/check_layers.py",
        ".github/workflows/tests.yml",
    ]
    srcs, tests, others = categorize_files(files)
    assert srcs == ["src/rbl/config/theme.py", "src/rbl/state/beamline.py"]
    assert tests == ["tests/test_theme.py"]
    assert len(others) == 3


def test_no_src_changes_passes():
    files = [
        "docs/adr/0001-tests-first-and-no-muted-failures.md",
        "scripts/check_layers.py",
        ".github/workflows/tests.yml",
        "pyproject.toml",
    ]
    passed, msg = evaluate_tests_first(files)
    assert passed is True
    assert "No application source files ('src/') modified" in msg


def test_test_only_changes_passes():
    files = ["tests/test_raster_model.py", "tests/conftest.py"]
    passed, msg = evaluate_tests_first(files)
    assert passed is True
    assert "No application source files ('src/') modified" in msg


def test_src_and_test_changes_passes():
    files = [
        "src/rbl/hardware/raster_model.py",
        "tests/test_raster_model.py",
    ]
    passed, msg = evaluate_tests_first(files)
    assert passed is True
    assert "Application source changes (1 file(s)) accompanied by test changes (1 file(s))" in msg


def test_src_changes_without_test_fails():
    files = [
        "src/rbl/hardware/raster_model.py",
        "docs/spec.md",
    ]
    passed, msg = evaluate_tests_first(files)
    assert passed is False
    assert "FAIL: Application source files modified under 'src/' without corresponding test changes under 'tests/'" in msg
    assert "src/rbl/hardware/raster_model.py" in msg


def test_escape_via_explicit_reason():
    files = ["src/rbl/gui/widgets/inputs.py"]
    reasons = ["Explicit exemption: Refactoring UI layout styling without behavioural change"]
    passed, msg = evaluate_tests_first(files, escape_reasons=reasons)
    assert passed is True
    assert "OK (EXEMPT)" in msg
    assert "Refactoring UI layout styling" in msg


def test_find_escape_reasons_from_tags():
    texts = [
        "fix: typo in docstring\n\n[no-test-needed: typo fix only]",
        "refactor: clean up comments [tests-exempt: pure comments]",
        "chore: skip check [skip-test-gate]",
    ]
    reasons = find_escape_reasons(texts=texts)
    assert len(reasons) == 3
    assert any("typo fix only" in r for r in reasons)
    assert any("pure comments" in r for r in reasons)
    assert any("skip-test-gate" in r for r in reasons)


def test_find_escape_reasons_from_pr_labels():
    labels = ["tests-exempt", "enhancement"]
    reasons = find_escape_reasons(labels=labels)
    assert len(reasons) == 1
    assert "PR label 'tests-exempt'" in reasons[0]

    labels2 = ["skip-test-gate"]
    reasons2 = find_escape_reasons(labels=labels2)
    assert len(reasons2) == 1
    assert "PR label 'skip-test-gate'" in reasons2[0]


def test_extract_event_info(tmp_path):
    event_file = tmp_path / "event.json"
    event_data = {
        "pull_request": {
            "title": "Update config [no-test-needed: config constant adjust]",
            "body": "This PR updates theme colors.\n\n[tests-exempt: UI theme only]",
            "labels": [{"name": "tests-exempt"}, {"name": "ui"}],
            "base": {"sha": "abc1234"},
        }
    }
    event_file.write_text(json.dumps(event_data), encoding="utf-8")

    labels, texts, base_ref = extract_event_info(event_file)
    assert "tests-exempt" in labels
    assert "ui" in labels
    assert base_ref == "abc1234"
    assert len(texts) == 2

    reasons = find_escape_reasons(texts=texts, labels=labels)
    assert len(reasons) >= 3


def test_cli_end_to_end_scenarios(tmp_path, monkeypatch, capsys):
    # 1. No src changes -> exit 0
    code = main(["--files", "docs/README.md", "tests/test_foo.py"])
    assert code == 0

    # 2. Src changes without test changes -> exit 1
    code = main(["--files", "src/rbl/state/beamline.py"])
    assert code == 1

    # 3. Src changes with test changes -> exit 0
    code = main(["--files", "src/rbl/state/beamline.py", "tests/test_beamline.py"])
    assert code == 0

    # 4. Src changes with explicit exempt reason -> exit 0
    code = main(["--files", "src/rbl/state/beamline.py", "--exempt-reason", "Refactor only"])
    assert code == 0

    # 5. Src changes with message annotation tag -> exit 0
    code = main([
        "--files", "src/rbl/state/beamline.py",
        "--message", "commit message [no-test-needed: checked manually]",
    ])
    assert code == 0

    # 6. Src changes with PR label -> exit 0
    code = main([
        "--files", "src/rbl/state/beamline.py",
        "--label", "tests-exempt",
    ])
    assert code == 0
