"""
scripts/ticket_swarm.py
Run Claude Code and Antigravity (``agy``) side by side to work the ticket
frontier: one fresh agent per ticket, one worktree + branch + PR per ticket,
and the PRs watched until they are green (and, with ``--merge``, merged).

    python scripts/ticket_swarm.py --dry-run            # show the frontier, start nothing
    python scripts/ticket_swarm.py --claude 2 --agy 1   # 3 agents at a time
    python scripts/ticket_swarm.py --claude 2 --agy 2 --merge

Stop it with Ctrl+C at any time; run it again and it picks up where it left
off (state lives in ``.swarm/state.json``, agent logs in ``.swarm/logs/``).

WHY THIS EXISTS
---------------
Each ticket set is a dependency graph (``Blocked by:``) of tickets that are
meant to be worked one-ticket-per-branch, one-PR-per-ticket (AGENTS.md
"Implementing a ticket — the protocol"). Doing that by hand means opening a
terminal per ticket, remembering which tickets are unblocked, and babysitting
each PR's CI. This script is that bookkeeping and nothing more: the agents do
the engineering, under the same AGENTS.md rules a human-started session obeys.

HOW A TICKET MOVES THROUGH IT
-----------------------------
    frontier ─► implement ─► watch PR ─┬─ red ──────► fix (max 2) ─► watch
                (new agent)            ├─ conflict ─► merge master ─► watch
                                       ├─ changes requested ─► address ─► watch
                                       ├─ green, Status not done ─► finalize ─► watch
                                       └─ green + done ─► merged (--merge) / ready
    red after the fix budget ─► escalate (Status: blocked, PR to draft)

DECISIONS THAT MATTER
---------------------
* The frontier is read from ``origin/master``, never from a working tree.
  A blocker counts as finished only when its ``Status: done`` has been merged,
  so an agent never starts on top of code that is still sitting in a PR. The
  one exception: a PR this swarm watched reach MERGED also counts. A human who
  merges the moment CI goes green beats the finalize turn, master is left
  saying ``in-progress``, and without the exception the whole set behind that
  ticket stalls with nothing running (64 did exactly this to 65-77).
* A ticket whose agent died before any PR existed (a crash, a timeout) is
  released on the next start and claimed again; its branch and worktree are
  reused, so the new agent continues rather than starts over.
* A ticket is claimable only when it is ``ready-for-agent``, its ``Runner`` is
  not ``developer``, every blocker is done on master, no ``ticket/<set>-<NN>-``
  branch exists on the remote, and this script has not already taken it.
* Every ticket gets a brand-new agent process. Follow-up turns on the SAME
  ticket (CI fix, conflict, finalize) go back to that ticket's agent: Claude is
  resumed by session id; ``agy`` is re-run in the same worktree with the full
  context written into the prompt, because its resume flag is not stable.
* The orchestrator, not the agent, watches CI. That keeps an expensive agent
  from sitting idle for the many minutes the Windows runner takes, and it is
  where AGENTS.md's two-fix-cycle budget is enforced.
* Merging is opt-in (``--merge``) and never happens for ``Auto-merge: no``.
  Without ``--merge`` the dependents of an open PR simply wait.
* Prompts are written to a file inside the worktree and the agent is told to
  read it. Long multi-line command-line arguments do not survive Windows'
  ``.cmd`` shims (claude.cmd / agy.cmd), a file always does.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SWARM_DIR = REPO / ".swarm"
STATE_PATH = SWARM_DIR / "state.json"
LOG_DIR = SWARM_DIR / "logs"
PROMPT_NAME = ".swarm-prompt.md"
BASE = "master"

DONE_WORDS = {"done", "complete", "completed", "resolved"}
CLAIMABLE = "ready-for-agent"

# Job phases. "agent phases" have a live agent process behind them.
IMPLEMENT, FIX, CONFLICT, REVIEW, FINALIZE, ESCALATE = (
    "implement", "fix", "conflict", "review", "finalize", "escalate")
AGENT_PHASES = {IMPLEMENT, FIX, CONFLICT, REVIEW, FINALIZE, ESCALATE}
WATCH, MERGED, READY, BLOCKED, FAILED = "watch", "merged", "ready", "blocked", "failed"
FINISHED = {MERGED, READY, BLOCKED, FAILED}


# ─────────────────────────────────────────────────────────────────────────────
# Tickets: parsing and the frontier (pure — no git, no processes)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Ticket:
    set: str            # the .scratch/<set>/ directory name
    number: str         # "63" — kept as the string from the file name
    slug: str           # "capture-contract-and-rules"
    path: str           # repo-relative, forward slashes
    title: str
    status: str
    runner: str
    auto_merge: bool
    blocked_by: tuple[str, ...]   # raw refs: "63" or "cup-settings/16"

    @property
    def key(self) -> str:
        return f"{self.set}/{self.number}"

    @property
    def branch(self) -> str:
        return f"ticket/{self.set}-{self.number}-{self.slug}"


_FIELD = re.compile(r"^\*{0,2}([A-Za-z -]+?):\*{0,2}\s*(.*)$")
_REF = re.compile(r"(?:([a-z0-9][a-z0-9-]*)/)?(\d+)")
_FILE = re.compile(r"^\.scratch/([^/]+)/issues/(\d+)-(.+)\.md$")


def parse_blocked_by(text: str) -> tuple[str, ...]:
    """'63, 64' -> ('63', '64'); 'None (can start immediately)' -> ()."""
    text = re.sub(r"\(.*?\)", "", text).strip()
    if not text or text.lower().startswith("none"):
        return ()
    return tuple(f"{s}/{n}" if s else n for s, n in _REF.findall(text))


def parse_ticket(path: str, text: str) -> Ticket | None:
    m = _FILE.match(path)
    if not m:
        return None
    fields: dict[str, str] = {}
    title = ""
    for line in text.splitlines()[:40]:
        if not title and line.startswith("# "):
            title = line[2:].strip()
        fm = _FIELD.match(line.strip())
        if fm and line.strip().startswith("**"):
            fields.setdefault(fm.group(1).strip().lower(), fm.group(2).strip())
    return Ticket(
        set=m.group(1), number=m.group(2), slug=m.group(3), path=path,
        title=title or m.group(3),
        status=fields.get("status", "").split()[0].lower() if fields.get("status") else "",
        runner=(fields.get("runner", "any").split() or ["any"])[0].lower(),
        auto_merge=fields.get("auto-merge", "yes").lower().startswith("yes"),
        blocked_by=parse_blocked_by(fields.get("blocked by", "")),
    )


def resolve_ref(ref: str, home_set: str, tickets: dict[str, Ticket]) -> Ticket | None:
    """A bare number means "this set first, else the one set that has it"."""
    if "/" in ref:
        s, n = ref.split("/", 1)
        return tickets.get(f"{s}/{n}")
    if f"{home_set}/{ref}" in tickets:
        return tickets[f"{home_set}/{ref}"]
    matches = [t for t in tickets.values() if t.number == ref]
    return matches[0] if len(matches) == 1 else None


def unmet_blockers(t: Ticket, tickets: dict[str, Ticket],
                   merged: set[str] = frozenset()) -> list[str]:
    """``merged``: ticket keys whose PR this swarm saw merged (see DECISIONS)."""
    out = []
    for ref in t.blocked_by:
        b = resolve_ref(ref, t.set, tickets)
        if b is None:
            out.append(f"{ref}?")          # unknown / ambiguous: never assume done
        elif b.status not in DONE_WORDS and b.key not in merged:
            out.append(b.key)
    return out


def frontier(tickets: dict[str, Ticket], taken: set[str],
             remote_branches: set[str], sets: set[str] | None = None,
             merged: set[str] = frozenset()) -> list[Ticket]:
    ready = []
    for t in tickets.values():
        if sets and t.set not in sets:
            continue
        if t.status != CLAIMABLE or t.runner == "developer" or t.key in taken:
            continue
        prefix = f"ticket/{t.set}-{t.number}-"
        if any(b.startswith(prefix) for b in remote_branches):
            continue
        if unmet_blockers(t, tickets, merged):
            continue
        ready.append(t)
    return sorted(ready, key=lambda t: (int(t.number), t.set))


# ─────────────────────────────────────────────────────────────────────────────
# Shell helpers
# ─────────────────────────────────────────────────────────────────────────────

def run(args: list[str], cwd: Path = REPO, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed:\n{p.stderr.strip()}")
    return p


def git(*args: str, cwd: Path = REPO, check: bool = True) -> str:
    return run(["git", *args], cwd=cwd, check=check).stdout


def gh_json(*args: str, cwd: Path = REPO):
    p = run(["gh", *args], cwd=cwd, check=False)
    try:
        return json.loads(p.stdout) if p.stdout.strip() else None
    except json.JSONDecodeError:
        return None


def load_master_tickets() -> dict[str, Ticket]:
    git("fetch", "--quiet", "origin", BASE)
    names = git("ls-tree", "-r", "--name-only", f"origin/{BASE}", ".scratch/").splitlines()
    tickets = {}
    for name in names:
        if not _FILE.match(name):
            continue
        t = parse_ticket(name, git("show", f"origin/{BASE}:{name}"))
        if t:
            tickets[t.key] = t
    return tickets


def remote_ticket_branches() -> set[str]:
    out = git("ls-remote", "--heads", "origin", check=False)
    return {line.split("refs/heads/", 1)[1] for line in out.splitlines()
            if "refs/heads/ticket/" in line}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Prompts — the agents follow AGENTS.md; these only add what the swarm changes
# ─────────────────────────────────────────────────────────────────────────────

def venv_python() -> str:
    for p in (REPO / ".venv" / "Scripts" / "python.exe", REPO / ".venv" / "bin" / "python"):
        if p.exists():
            return str(p)
    return "python"


COMMON = """\
You are an autonomous agent started by scripts/ticket_swarm.py. You own exactly ONE
ticket in the RBL repository and nothing else.

- Ticket file: `{path}`  ({title})
- Your working directory is a git worktree: `{wt}`
- Your branch is `{branch}` (already checked out). Never switch branch, never commit to
  `{base}`, never touch another ticket's file.
- Python with the dev dependencies: `{python}` (pytest.ini puts THIS worktree's `src`
  first, so tests run against your code).
- Do not commit `{prompt}` — it is this instruction file.
- AGENTS.md is binding, including ADR 0001: a failing test is fixed or escalated,
  never muted (no xfail/skip, no weakened assertion, no loosened tolerance).
- The swarm watches CI for you. Do NOT run `gh pr checks --watch`; exit when your
  step below is done.
- Finish your final message with exactly one line:
  `SWARM-RESULT: ok` or `SWARM-RESULT: blocked — <why>` or `SWARM-RESULT: failed — <why>`
"""

PROMPTS = {
    IMPLEMENT: """\
## Your step: implement the ticket and open its pull request

1. Read AGENTS.md (sections 1-11 and the ACTIVE-PLAN block), the ticket, every spec/ADR it
   names, and the module docstring of every file before you edit it.
2. Set the ticket's `Status:` to `in-progress`, commit `{number}: claim ticket (in-progress)`
   and `git push -u origin {branch}` straight away.
3. Run `pytest -q` once before starting, so you know the baseline.
4. Implement the ticket tests-first, exactly as written. Update docstrings' reasoning.
5. Before pushing, run what CI runs: `ruff check .`, `python scripts/check_tests_first.py`,
   `python scripts/check_layers.py`, `python tools/type_gate.py`, `pytest -q`. All clean.
6. Commit as `{number}: <what it does>`, push, then open the PR:
   `gh pr create --base {base} --head {branch} --title "{number}: <title>" --body-file <file>`
   The body names the ticket file, summarises the change, and says how it was tested.
7. Leave `Status:` at `in-progress` — the swarm asks you to set `done` once CI is green.
8. If a failing test genuinely cannot be fixed, escalate per AGENTS.md: commit what is done,
   set `Status: blocked`, explain under the ticket's `## Comments`, push, and open the PR
   with `--draft`. Then end with `SWARM-RESULT: blocked — ...`.
""",
    FIX: """\
## Your step: CI is red on PR #{pr} (fix cycle {cycle} of {budget})

Failing checks: {failed}

Failed-step log (from `gh run view --log-failed`, tail):
```
{ci_log}
```
Read the failure before theorising. Find the ROOT CAUSE, classify it (real defect /
harness defect / environment difference — see AGENTS.md "Watching CI") and state the
classification in the commit message. Never mute a test. Run the local checks, commit,
push. If you need more log: `gh run view <run-id> --log-failed` (runs: {runs}).
""",
    ESCALATE: """\
## Your step: escalate — CI is still red on PR #{pr} after {budget} fix cycles

Failing checks: {failed}
```
{ci_log}
```
Follow AGENTS.md "When the budget runs out": set the ticket's `Status:` to `blocked` and
append under `## Comments`: what CI said (quoted), what each attempt concluded and changed,
why it is still red, and what decision a human must make. Commit, push, then run
`gh pr ready {pr} --undo`. End with `SWARM-RESULT: blocked — <one line>`.
""",
    CONFLICT: """\
## Your step: PR #{pr} has a merge conflict with {base}

`git fetch origin {base}` then `git merge origin/{base}` (merge — never rebase or
force-push). Resolve keeping the behaviour of BOTH sides; regenerate generated files with
the repo's tooling. Run the local checks and `pytest -q`, commit the merge, push.
If both sides changed the same logic and either choice loses behaviour, do not guess:
end with `SWARM-RESULT: blocked — <what needs deciding>`.
""",
    REVIEW: """\
## Your step: a reviewer requested changes on PR #{pr}

Read them: `gh pr view {pr} --comments` and
`gh api repos/{{owner}}/{{repo}}/pulls/{pr}/comments`.
Implement small, local asks (nits, renames, an added test, a one-function refactor), run
the local checks, commit, push, and reply on each thread saying what changed. For larger
asks (multi-file refactors, API or design changes) reply on the thread with a proposal
instead of pushing. End every GitHub comment with
`\\n\\n---\\n_Generated by [Claude Code](https://claude.ai/code)_` if you are Claude.
""",
    FINALIZE: """\
## Your step: CI is green on PR #{pr} — close out the ticket

In the ticket file only: tick each acceptance-criterion checkbox you actually satisfied
(`- [ ]` → `- [x]`; leave any you did not satisfy unticked and say why under
`## Comments`), and set `Status:` to `done`. Commit `{number}: done`, push. Change nothing else.
""",
}


def build_prompt(phase: str, job: "Job", tool: str, **extra) -> str:
    t = job.ticket_dict()
    fields = dict(path=t["path"], title=t["title"], number=t["number"], wt=job.worktree,
                  branch=t["branch"], base=BASE, python=venv_python(), prompt=PROMPT_NAME,
                  pr=job.pr or "?", cycle=job.fix_cycles, budget=job.budget, failed="",
                  ci_log="", runs="")
    fields.update(extra)
    text = COMMON.format(**fields) + "\n" + PROMPTS[phase].format(**fields)
    if tool == "agy" and phase != IMPLEMENT:
        # agy is re-run fresh each turn, so it gets the history Claude would remember.
        text += ("\n## Context from earlier turns on this ticket\n"
                 "You are continuing work another run of you started. Read the ticket, "
                 f"`git log --oneline origin/{BASE}..HEAD` and `git diff origin/{BASE}...HEAD` "
                 "first to see what has been done.\n")
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Agents
# ─────────────────────────────────────────────────────────────────────────────

def agent_command(tool: str, job: "Job", args) -> list[str]:
    short = (f"Read the file {PROMPT_NAME} in the current directory and follow its "
             "instructions exactly.")
    if tool == "claude":
        exe = shutil.which(args.claude_cmd) or args.claude_cmd
        cmd = [exe, "-p", short, "--output-format", "stream-json", "--verbose",
               *args.claude_args.split()]
        if job.session_id:
            cmd += ["--resume", job.session_id]
        return cmd
    exe = shutil.which(args.agy_cmd) or args.agy_cmd
    return [exe, *args.agy_args.split(), "-p", short]


def session_id_from_log(path: Path) -> str | None:
    """Every `claude -p --output-format stream-json` event carries the session_id."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for m in reversed(list(re.finditer(r'"session_id"\s*:\s*"([^"]+)"', text))):
        return m.group(1)
    return None


def final_answer(path: Path, tool: str) -> str:
    """The agent's last message only. The log also holds the prompt file the agent read
    (which itself mentions every SWARM-RESULT word), so searching the whole log lies."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if tool == "claude":
        for line in reversed(text.splitlines()):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "result":
                return str(event.get("result", ""))
        return ""
    return text[-3000:]


def swarm_result(path: Path, tool: str) -> str:
    m = list(re.finditer(r"SWARM-RESULT:\s*(ok|blocked|failed)", final_answer(path, tool)))
    return m[-1].group(1) if m else "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Jobs: one per ticket
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Job:
    ticket: dict
    tool: str
    worktree: str
    phase: str = IMPLEMENT
    pr: int | None = None
    session_id: str | None = None
    fix_cycles: int = 0
    conflict_rounds: int = 0
    review_rounds: int = 0
    finalize_rounds: int = 0
    budget: int = 2
    turn: int = 0
    started: float = 0.0
    last_head: str = ""
    last_review: str = ""
    note: str = ""
    pid: int | None = field(default=None, compare=False)

    def ticket_dict(self) -> dict:
        return self.ticket

    @property
    def key(self) -> str:
        return f"{self.ticket['set']}/{self.ticket['number']}"

    @property
    def log_path(self) -> Path:
        t = self.ticket
        return LOG_DIR / f"{t['set']}-{t['number']}-{self.turn:02d}-{self.phase}.log"


class Swarm:
    def __init__(self, args):
        self.args = args
        self.jobs: dict[str, Job] = {}
        self.procs: dict[str, subprocess.Popen] = {}
        self.tickets: dict[str, Ticket] = {}
        self.load_state()

    # ── state ────────────────────────────────────────────────────────────────
    def load_state(self) -> None:
        if not STATE_PATH.exists():
            return
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        for key, d in raw.get("jobs", {}).items():
            job = Job(**{k: v for k, v in d.items() if k in Job.__dataclass_fields__})
            job.pid = None
            if job.phase == FAILED and not job.pr:
                log(f"{key}: agent died before opening a PR last run — releasing it to retry")
                continue
            if job.phase in AGENT_PHASES:
                # The agent died with the last run of the swarm. If a PR exists we just
                # watch it; otherwise the implement turn is repeated as a "continue".
                job.phase = WATCH if job.pr else IMPLEMENT
                job.note = "resumed after restart"
            self.jobs[key] = job

    def save_state(self) -> None:
        SWARM_DIR.mkdir(exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"jobs": {k: asdict(j) for k, j in self.jobs.items()}},
                                  indent=2), encoding="utf-8")
        tmp.replace(STATE_PATH)

    # ── starting work ────────────────────────────────────────────────────────
    def running(self, tool: str) -> int:
        return sum(1 for k in self.procs if self.jobs[k].tool == tool)

    def free_tool(self) -> str | None:
        slots = {"claude": self.args.claude, "agy": self.args.agy}
        free = [t for t, n in slots.items() if n and self.running(t) < n]
        return min(free, key=lambda t: self.running(t) / slots[t]) if free else None

    def merged(self) -> set[str]:
        return {k for k, j in self.jobs.items() if j.phase == MERGED}

    def open_jobs(self) -> int:
        return sum(1 for j in self.jobs.values() if j.phase not in FINISHED)

    def claim(self, t: Ticket, tool: str) -> Job:
        wt = Path(self.args.worktree_root) / f"{t.set}-{t.number}"
        if not wt.exists():
            wt.parent.mkdir(parents=True, exist_ok=True)
            if git("branch", "--list", t.branch).strip():
                git("worktree", "add", str(wt), t.branch)
            else:
                git("worktree", "add", "-b", t.branch, str(wt), f"origin/{BASE}")
        job = Job(ticket={**asdict(t), "branch": t.branch}, tool=tool, worktree=str(wt),
                  budget=self.args.fix_budget)
        self.jobs[t.key] = job
        log(f"{t.key} → {tool}: {t.title}")
        return job

    def start_agent(self, job: Job, phase: str, **extra) -> None:
        job.phase = phase
        job.turn += 1
        job.started = time.time()
        wt = Path(job.worktree)
        (wt / PROMPT_NAME).write_text(build_prompt(phase, job, job.tool, **extra),
                                      encoding="utf-8")
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        out = open(job.log_path, "w", encoding="utf-8")
        cmd = agent_command(job.tool, job, self.args)
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(cmd, cwd=wt, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, **kwargs)
        job.pid = proc.pid
        self.procs[job.key] = proc
        log(f"{job.key} [{job.tool}] {phase} started (log: {job.log_path.name})")
        self.save_state()

    # ── agent finished ───────────────────────────────────────────────────────
    def reap(self) -> None:
        for key, proc in list(self.procs.items()):
            job = self.jobs[key]
            timed_out = time.time() - job.started > self.args.agent_timeout * 60
            if proc.poll() is None and not timed_out:
                continue
            if timed_out and proc.poll() is None:
                proc.kill()
                proc.wait()
                job.note = f"{job.phase} timed out"
            del self.procs[key]
            job.pid = None
            if job.tool == "claude":
                job.session_id = session_id_from_log(job.log_path) or job.session_id
            result = "failed" if timed_out else swarm_result(job.log_path, job.tool)
            log(f"{key} [{job.tool}] {job.phase} finished: {result} (exit {proc.returncode})")
            self.after_agent(job, result)
            self.save_state()

    def after_agent(self, job: Job, result: str) -> None:
        job.pr = job.pr or self.find_pr(job)
        if result == "blocked" or job.phase == ESCALATE:
            if job.pr:
                run(["gh", "pr", "ready", str(job.pr), "--undo"], check=False)
            job.phase, job.note = BLOCKED, f"blocked — see {job.ticket['path']} ## Comments"
            return
        if job.pr is None:
            if self.branch_has_commits(job) and job.phase == IMPLEMENT:
                job.pr = self.open_pr_ourselves(job)
            if job.pr is None:
                job.phase, job.note = FAILED, f"no PR after {job.phase} (see {job.log_path.name})"
                return
        job.phase = WATCH

    def find_pr(self, job: Job) -> int | None:
        prs = gh_json("pr", "list", "--head", job.ticket["branch"], "--state", "all",
                      "--json", "number,state")
        return prs[0]["number"] if prs else None

    def branch_has_commits(self, job: Job) -> bool:
        out = git("rev-list", "--count", f"origin/{BASE}..origin/{job.ticket['branch']}",
                  cwd=Path(job.worktree), check=False).strip()
        return out.isdigit() and int(out) > 0

    def open_pr_ourselves(self, job: Job) -> int | None:
        t = job.ticket
        body = (f"Ticket: `{t['path']}`\n\nOpened by scripts/ticket_swarm.py because the "
                f"agent pushed but did not open a PR.\n")
        run(["gh", "pr", "create", "--base", BASE, "--head", t["branch"],
             "--title", t["title"], "--body", body], cwd=Path(job.worktree), check=False)
        return self.find_pr(job)

    # ── watching PRs ─────────────────────────────────────────────────────────
    def watch(self, job: Job) -> None:
        if job.key in self.procs:
            return
        wt = Path(job.worktree)
        info = gh_json("pr", "view", str(job.pr), "--json",
                       "state,isDraft,mergeable,reviewDecision,headRefOid,reviews")
        if not info:
            return
        if info["state"] == "MERGED":
            job.phase = MERGED
            self.cleanup(job)
            log(f"{job.key} merged")
            on_master = self.tickets.get(job.key)
            if on_master and on_master.status not in DONE_WORDS:
                job.note = (f"merged with Status: {on_master.status} — counted as done "
                            f"here; set it to done on master")
                log(f"{job.key}: {job.note}")
            return
        if info["state"] == "CLOSED":
            job.phase, job.note = FAILED, "PR closed without merging"
            return
        if job.phase == READY:
            return  # waiting for a human to merge; only MERGED/CLOSED change it
        if info["mergeable"] == "CONFLICTING":
            if not self.has_slot(job):
                return
            if job.conflict_rounds >= 2:
                job.phase, job.note = BLOCKED, "merge conflict persists after 2 rounds"
                return
            job.conflict_rounds += 1
            git("fetch", "--quiet", "origin", BASE, cwd=wt, check=False)
            self.start_agent(job, CONFLICT)
            return

        checks = gh_json("pr", "checks", str(job.pr), "--json", "name,bucket,link") or []
        buckets = {c["bucket"] for c in checks}
        if not checks or "pending" in buckets:
            return  # CI not reported yet / still running
        failed = [c for c in checks if c["bucket"] in ("fail", "cancel")]
        head = info["headRefOid"]

        if failed:
            if head == job.last_head:
                # The last turn on this red head ended without pushing a new commit.
                job.phase, job.note = BLOCKED, "agent's fix turn pushed nothing; CI still red"
                return
            if not self.has_slot(job):
                return
            job.last_head = head
            runs = sorted({m.group(1) for c in failed
                           if (m := re.search(r"/runs/(\d+)", c.get("link", "")))})
            ci_log = self.failed_log(runs)
            names = ", ".join(c["name"] for c in failed)
            if job.fix_cycles >= job.budget:
                self.start_agent(job, ESCALATE, failed=names, ci_log=ci_log, runs=" ".join(runs))
            else:
                job.fix_cycles += 1
                self.start_agent(job, FIX, failed=names, ci_log=ci_log, runs=" ".join(runs))
            return

        # Green from here on.
        asks = [r for r in info.get("reviews") or [] if r.get("state") == "CHANGES_REQUESTED"]
        latest = str(asks[-1].get("id") or asks[-1].get("submittedAt")) if asks else ""
        if (info["reviewDecision"] == "CHANGES_REQUESTED" and latest != job.last_review
                and job.review_rounds < 3):
            if not self.has_slot(job):
                return
            job.review_rounds += 1
            job.last_review = latest
            self.start_agent(job, REVIEW)
            return
        git("fetch", "--quiet", "origin", job.ticket["branch"], cwd=wt, check=False)
        status = self.branch_ticket_status(job)
        if status not in DONE_WORDS:
            if not self.has_slot(job):
                return
            if job.finalize_rounds >= 2:
                job.phase, job.note = BLOCKED, "could not get Status: done committed"
                return
            job.finalize_rounds += 1
            self.start_agent(job, FINALIZE)
            return
        if info["isDraft"]:
            job.phase, job.note = READY, "green but draft — needs a human"
        elif self.args.merge and job.ticket["auto_merge"]:
            p = run(["gh", "pr", "merge", str(job.pr), "--merge", "--delete-branch"],
                    cwd=wt, check=False)
            if p.returncode == 0:
                job.phase = MERGED
                self.cleanup(job)
                log(f"{job.key} merged PR #{job.pr}")
            else:
                job.note = f"merge refused: {p.stderr.strip()[:200]}"
                job.phase = READY
        else:
            why = "Auto-merge: no" if not job.ticket["auto_merge"] else "run with --merge to merge"
            job.phase, job.note = READY, f"green and done — {why}"
        log(f"{job.key} → {job.phase} {job.note}")

    def has_slot(self, job: Job) -> bool:
        """A follow-up turn is an agent run like any other and waits for a free slot."""
        return self.running(job.tool) < getattr(self.args, job.tool)

    def branch_ticket_status(self, job: Job) -> str:
        p = run(["git", "show", f"origin/{job.ticket['branch']}:{job.ticket['path']}"],
                cwd=Path(job.worktree), check=False)
        t = parse_ticket(job.ticket["path"], p.stdout) if p.returncode == 0 else None
        return t.status if t else ""

    def failed_log(self, runs: list[str]) -> str:
        chunks = []
        for r in runs[:2]:
            out = run(["gh", "run", "view", r, "--log-failed"], check=False).stdout
            chunks.append("\n".join(out.splitlines()[-self.args.log_lines:]))
        return "\n".join(chunks).replace("```", "'''") or "(no failed-step log available)"

    def cleanup(self, job: Job) -> None:
        git("worktree", "remove", "--force", job.worktree, check=False)
        git("branch", "-D", job.ticket["branch"], check=False)

    # ── main loop ────────────────────────────────────────────────────────────
    def tick(self) -> None:
        self.reap()
        self.tickets = load_master_tickets()
        for job in list(self.jobs.values()):
            if job.phase in (WATCH, READY) and job.pr:
                self.watch(job)
            elif job.phase == IMPLEMENT and job.key not in self.procs:
                if self.has_slot(job):
                    self.start_agent(job, IMPLEMENT)   # restart after a crash
        taken = set(self.jobs)
        ready = frontier(self.tickets, taken, remote_ticket_branches(), self.args.sets,
                         self.merged())
        while ready and self.open_jobs() < self.args.max_open:
            tool = self.free_tool()
            if tool is None:
                break
            job = self.claim(ready.pop(0), tool)
            self.start_agent(job, IMPLEMENT)
        self.save_state()

    def report(self) -> None:
        lines = [f"{'ticket':<32} {'agent':<7} {'phase':<10} {'PR':<6} note"]
        for j in sorted(self.jobs.values(), key=lambda j: int(j.ticket["number"])):
            lines.append(f"{j.key:<32} {j.tool:<7} {j.phase:<10} "
                         f"{('#' + str(j.pr)) if j.pr else '-':<6} {j.note}")
        text = "\n".join(lines)
        (SWARM_DIR / "status.txt").write_text(text + "\n", encoding="utf-8")
        print(text, flush=True)

    def idle(self) -> bool:
        """READY counts as busy: a human merging it may unblock more tickets."""
        return not self.procs and all(j.phase in FINISHED - {READY}
                                      for j in self.jobs.values())

    def shutdown(self) -> None:
        for key, proc in self.procs.items():
            log(f"stopping {key} (pid {proc.pid})")
            if os.name == "nt":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.terminate()
        self.save_state()


# ─────────────────────────────────────────────────────────────────────────────

def ensure_ignored() -> None:
    """Keep swarm files out of every worktree's `git status` (info/exclude is shared)."""
    common = Path(git("rev-parse", "--git-common-dir").strip())
    if not common.is_absolute():
        common = REPO / common
    exclude = common / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    have = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    missing = [p for p in (".swarm/", PROMPT_NAME) if p not in have.split()]
    if missing:
        with exclude.open("a", encoding="utf-8") as f:
            f.write("\n" + "\n".join(missing) + "\n")


def dry_run(args) -> None:
    tickets = load_master_tickets()
    taken: set[str] = set()
    merged: set[str] = set()
    if STATE_PATH.exists():
        jobs = json.loads(STATE_PATH.read_text(encoding="utf-8"))["jobs"]
        # Mirror Swarm.load_state: a job that died without a PR is retried.
        taken = {k for k, j in jobs.items() if not (j["phase"] == FAILED and not j.get("pr"))}
        merged = {k for k, j in jobs.items() if j["phase"] == MERGED}
    ready = frontier(tickets, taken, remote_ticket_branches(), args.sets, merged)
    print("Would start, in this order:")
    for t in ready:
        print(f"  {t.key:<32} {t.title}{'' if t.auto_merge else '   [Auto-merge: no]'}")
    if not ready:
        print("  (nothing — every ready-for-agent ticket is blocked, taken, or in a PR)")
    print("\nWaiting on blockers:")
    for t in sorted(tickets.values(), key=lambda t: int(t.number)):
        if args.sets and t.set not in args.sets:
            continue
        if t.status == CLAIMABLE and t.runner != "developer":
            unmet = unmet_blockers(t, tickets, merged)
            if unmet:
                print(f"  {t.key:<32} needs {', '.join(unmet)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--claude", type=int, default=2, help="Claude agents at once (default 2)")
    ap.add_argument("--agy", type=int, default=1, help="Antigravity agents at once (default 1)")
    ap.add_argument("--max-open", type=int, default=6,
                    help="max tickets in flight incl. PRs waiting on CI (default 6)")
    ap.add_argument("--merge", action="store_true",
                    help="merge green, done PRs whose ticket says Auto-merge: yes")
    ap.add_argument("--sets", nargs="*", help="only these .scratch/<set> names")
    ap.add_argument("--fix-budget", type=int, default=2, help="CI fix cycles before escalating")
    ap.add_argument("--poll", type=int, default=60, help="seconds between PR checks")
    ap.add_argument("--agent-timeout", type=int, default=180, help="minutes per agent turn")
    ap.add_argument("--log-lines", type=int, default=150, help="CI log lines given to a fix turn")
    ap.add_argument("--worktree-root", default=str(REPO.parent / f"{REPO.name}-swarm"))
    ap.add_argument("--claude-cmd", default="claude")
    ap.add_argument("--claude-args", default="--dangerously-skip-permissions",
                    help="extra args for every claude run")
    ap.add_argument("--agy-cmd", default="agy")
    ap.add_argument("--agy-args", default="--dangerously-skip-permissions",
                    help="extra args for every agy run (check `agy --help` for your version)")
    ap.add_argument("--forget", nargs="*", default=[], metavar="SET/NN",
                    help="drop these tickets from the swarm's state, then exit")
    ap.add_argument("--dry-run", action="store_true", help="print the frontier and exit")
    args = ap.parse_args(argv)
    args.sets = set(args.sets) if args.sets else None

    if args.dry_run:
        dry_run(args)
        return 0

    SWARM_DIR.mkdir(exist_ok=True)
    ensure_ignored()
    swarm = Swarm(args)
    if args.forget:
        for key in args.forget:
            swarm.jobs.pop(key, None)
        swarm.save_state()
        log(f"forgot {', '.join(args.forget)}")
        return 0

    for tool, n in (("claude", args.claude), ("agy", args.agy)):
        cmd = args.claude_cmd if tool == "claude" else args.agy_cmd
        if n and not shutil.which(cmd):
            print(f"error: `{cmd}` not found on PATH (use --{tool} 0 to run without it)")
            return 2
    if not shutil.which("gh"):
        print("error: GitHub CLI `gh` not found — install it and run `gh auth login`")
        return 2

    merge = "on" if args.merge else "off"
    log(f"swarm started: claude×{args.claude} agy×{args.agy}, merge={merge}")
    last_report = ""
    try:
        while True:
            swarm.tick()
            snapshot = json.dumps({k: (j.phase, j.pr, j.note) for k, j in swarm.jobs.items()})
            if snapshot != last_report:
                swarm.report()
                last_report = snapshot
            if swarm.idle() and not frontier(swarm.tickets, set(swarm.jobs),
                                             remote_ticket_branches(), args.sets,
                                             swarm.merged()):
                log("nothing left to start and nothing running — done.")
                return 0
            time.sleep(args.poll if not swarm.procs else min(args.poll, 20))
    except KeyboardInterrupt:
        log("Ctrl+C — stopping agents; run again to resume.")
        swarm.shutdown()
        return 130


if __name__ == "__main__":
    sys.exit(main())
