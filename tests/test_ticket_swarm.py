"""
Tests for scripts/ticket_swarm.py — the pure parts that decide WHICH ticket an
agent is started on. A wrong answer here starts an agent on top of unmerged
code, or on a developer bench ticket, so these are worth pinning down.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "ticket_swarm.py"
_spec = importlib.util.spec_from_file_location("ticket_swarm", _PATH)
swarm = importlib.util.module_from_spec(_spec)
sys.modules["ticket_swarm"] = swarm
_spec.loader.exec_module(swarm)


def _ticket(set_, num, slug="x", status="ready-for-agent", runner="any",
            blocked="None (can start immediately)", auto="yes"):
    text = (f"# {num}: Title {num}\n\n**Status:** {status}\n\n**Runner:** {runner}\n\n"
            f"**Auto-merge:** {auto}\n\n**Blocked by:** {blocked}\n\n## What to build\n")
    return swarm.parse_ticket(f".scratch/{set_}/issues/{num}-{slug}.md", text)


def _index(*tickets):
    return {t.key: t for t in tickets}


@pytest.mark.parametrize("text, expected", [
    ("None (can start immediately)", ()),
    ("none", ()),
    ("63", ("63",)),
    ("63, 64", ("63", "64")),
    ("27, cup-settings/16", ("27", "cup-settings/16")),
    ("47, 48 (both need the recorder)", ("47", "48")),
])
def test_parse_blocked_by(text, expected):
    assert swarm.parse_blocked_by(text) == expected


def test_parse_ticket_reads_the_header_fields():
    t = _ticket("zwo-capture", "65", slug="skeleton", blocked="63, 64", auto="no")
    assert (t.set, t.number, t.status, t.runner) == ("zwo-capture", "65", "ready-for-agent", "any")
    assert t.blocked_by == ("63", "64")
    assert t.auto_merge is False
    assert t.title == "65: Title 65"
    assert t.branch == "ticket/zwo-capture-65-skeleton"


def test_non_ticket_paths_are_ignored():
    assert swarm.parse_ticket(".scratch/zwo-capture/spec.md", "# spec") is None


def test_bare_number_resolves_in_own_set_first():
    a = _ticket("cup-settings", "01", status="done")
    b = _ticket("cup-actuation", "01", status="ready-for-agent")
    child = _ticket("cup-actuation", "02", blocked="01")
    idx = _index(a, b, child)
    assert swarm.resolve_ref("01", "cup-actuation", idx) is b
    assert swarm.unmet_blockers(child, idx) == ["cup-actuation/01"]


def test_bare_number_falls_back_to_the_one_set_that_has_it():
    blocker = _ticket("logging-and-sessions", "33", status="done")
    child = _ticket("amplifier-load-and-spikes", "48", blocked="33")
    assert swarm.unmet_blockers(child, _index(blocker, child)) == []


def test_unknown_blocker_is_never_assumed_done():
    child = _ticket("zwo-capture", "64", blocked="999")
    assert swarm.unmet_blockers(child, _index(child)) == ["999?"]


def test_frontier_only_offers_unblocked_agent_tickets_in_number_order():
    idx = _index(
        _ticket("s", "63"),
        _ticket("s", "62"),
        _ticket("s", "64", blocked="63"),                  # blocker not merged yet
        _ticket("s", "78", runner="developer"),            # bench ticket
        _ticket("s", "79", status="ready-for-developer"),
        _ticket("s", "61", status="done"),
        _ticket("s", "70", blocked="61"),                  # blocker done on master
        _ticket("s", "71"),                                # someone already pushed a branch
        _ticket("s", "72"),                                # this swarm already took it
    )
    ready = swarm.frontier(idx, taken={"s/72"}, remote_branches={"ticket/s-71-x"})
    assert [t.number for t in ready] == ["62", "63", "70"]


def test_a_pr_the_swarm_saw_merged_counts_as_done_even_if_master_says_in_progress():
    # A human merged 64 the moment CI went green, before the finalize turn ran:
    # master still said in-progress and 65-77 stalled with nothing running.
    idx = _index(_ticket("s", "64", status="in-progress"), _ticket("s", "65", blocked="64"))
    assert swarm.frontier(idx, taken={"s/64"}, remote_branches=set()) == []
    ready = swarm.frontier(idx, taken={"s/64"}, remote_branches=set(), merged={"s/64"})
    assert [t.key for t in ready] == ["s/65"]


def test_a_job_that_died_without_a_pr_is_released_on_restart(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    t = {"set": "s", "number": "48", "branch": "ticket/s-48-x"}
    state.write_text(json.dumps({"jobs": {
        "s/48": {"ticket": t, "tool": "claude", "worktree": "w", "phase": "failed", "pr": None},
        "s/49": {"ticket": {**t, "number": "49"}, "tool": "claude", "worktree": "w",
                 "phase": "failed", "pr": 120},
    }}), encoding="utf-8")
    monkeypatch.setattr(swarm, "STATE_PATH", state)
    s = swarm.Swarm(type("Args", (), {})())
    assert set(s.jobs) == {"s/49"}   # a failed job WITH a PR stays for a human


def test_frontier_can_be_limited_to_sets():
    idx = _index(_ticket("a", "01"), _ticket("b", "02"))
    assert [t.key for t in swarm.frontier(idx, set(), set(), sets={"b"})] == ["b/02"]


@pytest.mark.parametrize("phase", sorted(swarm.PROMPTS))
@pytest.mark.parametrize("tool", ["claude", "agy"])
def test_every_prompt_formats(phase, tool, tmp_path):
    t = _ticket("zwo-capture", "63", slug="contract")
    job = swarm.Job(ticket={**swarm.asdict(t), "branch": t.branch}, tool=tool,
                    worktree=str(tmp_path), pr=12)
    text = swarm.build_prompt(phase, job, tool, failed="test", ci_log="boom", runs="1")
    assert t.path in text and t.branch in text
    assert "{" not in text.replace("{owner}", "").replace("{repo}", "")


def test_result_comes_from_claudes_final_message_not_the_prompt_it_read(tmp_path):
    log = tmp_path / "a.log"
    events = [
        {"type": "user", "message": "…`SWARM-RESULT: blocked — <why>`…", "session_id": "s1"},
        {"type": "result", "result": "PR opened.\nSWARM-RESULT: ok", "session_id": "s1"},
    ]
    log.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    assert swarm.swarm_result(log, "claude") == "ok"
    assert swarm.session_id_from_log(log) == "s1"


def test_missing_result_line_is_unknown(tmp_path):
    log = tmp_path / "b.log"
    log.write_text("the agent just stopped", encoding="utf-8")
    assert swarm.swarm_result(log, "agy") == "unknown"


@pytest.mark.parametrize("status, pr_state, landed", [
    ("done", None, True),
    ("done", "OPEN", True),
    ("in-progress", "MERGED", True),
    ("in-progress", "OPEN", False),
    ("ready-for-agent", None, False),
    ("blocked", "CLOSED", False),
])
def test_landed_trusts_master_status_or_a_merged_pr(status, pr_state, landed):
    assert (swarm.landed(status, pr_state) is not None) is landed


def _swarm_with(jobs, tickets, tmp_path, monkeypatch, pr_state=None):
    args = type("A", (), {"poll": 0, "claude": 1, "agy": 0})()
    monkeypatch.setattr(swarm, "STATE_PATH", tmp_path / "state.json")
    s = swarm.Swarm(args)
    s.jobs = jobs
    s.tickets = tickets
    monkeypatch.setattr(swarm, "gh_json",
                        lambda *a, **k: {"state": pr_state} if pr_state else None)
    monkeypatch.setattr(s, "cleanup", lambda job: None)
    monkeypatch.setattr(s, "find_pr", lambda job: None)
    return s


def _job(t, phase, pr=None):
    return swarm.Job(ticket={**swarm.asdict(t), "branch": t.branch}, tool="claude",
                     worktree="x", phase=phase, pr=pr)


@pytest.mark.parametrize("phase", ["implement", "watch", "blocked", "ready"])
def test_reconcile_closes_out_a_job_whose_ticket_is_done_on_master(phase, tmp_path, monkeypatch):
    t = _ticket("zwo-capture", "68", status="done")
    s = _swarm_with({t.key: _job(t, phase)}, _index(t), tmp_path, monkeypatch)
    s.reconcile()
    assert s.jobs[t.key].phase == swarm.MERGED


def test_reconcile_closes_out_a_blocked_job_whose_pr_merged(tmp_path, monkeypatch):
    t = _ticket("zwo-capture", "70", status="in-progress")
    s = _swarm_with({t.key: _job(t, "blocked", pr=123)}, _index(t), tmp_path, monkeypatch,
                    pr_state="MERGED")
    s.reconcile()
    assert s.jobs[t.key].phase == swarm.MERGED
    assert t.key in s.merged()


def test_reconcile_leaves_unfinished_work_alone(tmp_path, monkeypatch):
    t = _ticket("zwo-capture", "68", status="in-progress")
    s = _swarm_with({t.key: _job(t, "blocked", pr=129)}, _index(t), tmp_path, monkeypatch,
                    pr_state="OPEN")
    s.reconcile()
    assert s.jobs[t.key].phase == "blocked"


def test_reconcile_watches_a_pr_the_agent_opened_before_it_died(tmp_path, monkeypatch):
    t = _ticket("zwo-capture", "68", status="in-progress")
    s = _swarm_with({t.key: _job(t, "implement", pr=129)}, _index(t), tmp_path, monkeypatch,
                    pr_state="OPEN")
    s.reconcile()
    assert s.jobs[t.key].phase == swarm.WATCH


@pytest.mark.parametrize("branch_status, pr_state, buckets, ok", [
    ("in-progress", "OPEN", {"pass"}, True),
    ("in-progress", "OPEN", {"pass", "fail"}, False),
    ("in-progress", "OPEN", {"pass", "pending"}, False),
    ("in-progress", "OPEN", set(), False),
    ("blocked", "OPEN", {"pass"}, False),        # a real escalation stays with a human
    ("in-progress", "CLOSED", {"pass"}, False),
])
def test_recoverable_only_when_green_and_never_escalated(branch_status, pr_state, buckets, ok):
    assert swarm.recoverable(branch_status, pr_state, buckets) is ok
