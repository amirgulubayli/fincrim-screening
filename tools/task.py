#!/usr/bin/env python3
"""GitHub-Issues task board helper for humans and AI agents.

Standard library only. Shells out to `gh` and `git`.

Workflow:
    python tools/task.py list                 # what is ready to pick up
    python tools/task.py claim 12 --agent claude
    python tools/task.py note "parser done, starting tests"
    python tools/task.py sync                 # rebase on origin/main
    python tools/task.py done                 # checks, push, PR, status:in-review

Default agent comes from env TASK_AGENT (claude|grok|cursor|codex|human), else `human`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

AGENTS = ("claude", "grok", "cursor", "codex", "human")
PRIORITIES = ("p0", "p1", "p2")
STATUS_LABELS = ("status:ready", "status:claimed", "status:in-review", "status:blocked")
REPO_ROOT = Path(__file__).resolve().parent.parent


class TaskError(Exception):
    """User-facing error; printed without a traceback."""


# ---------------------------------------------------------------- pure helpers

def slugify(title: str, max_words: int = 5) -> str:
    """Lowercase, ascii-alnum words, at most `max_words`, hyphen-joined."""
    words = re.findall(r"[a-z0-9]+", title.lower())
    return "-".join(words[:max_words]) or "task"


def branch_name(number: int, title: str) -> str:
    return f"{number}-{slugify(title)}"


def issue_number_from_branch(branch: str) -> int | None:
    """`12-fix-parser` -> 12. Also accepts `feature/12-x`. None if no prefix."""
    m = re.match(r"^(?:[^/]+/)*(\d+)-", branch or "")
    return int(m.group(1)) if m else None


def label_names(labels) -> list[str]:
    """Normalize gh's label JSON (list of dicts or strings) to names."""
    out = []
    for lab in labels or []:
        out.append(lab["name"] if isinstance(lab, dict) else str(lab))
    return out


def label_value(labels, prefix: str) -> str | None:
    """First value for a `prefix:value` label, e.g. ('area') -> 'engine'."""
    for name in label_names(labels):
        if name.startswith(prefix + ":"):
            return name.split(":", 1)[1]
    return None


def status_of(labels) -> str:
    return label_value(labels, "status") or "-"


def logins(assignees) -> list[str]:
    return [a["login"] if isinstance(a, dict) else str(a) for a in assignees or []]


def parse_eval_summary(output: str, max_lines: int = 15) -> str:
    lines = [ln.rstrip() for ln in output.strip().splitlines() if ln.strip()]
    return "\n".join(lines[-max_lines:])


# ---------------------------------------------------------------- subprocess

def run(cmd: list[str], check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    exe = cmd[0]
    if shutil.which(exe) is None:
        raise TaskError(f"`{exe}` not found on PATH. Install it (gh: https://cli.github.com) and retry.")
    try:
        proc = subprocess.run(
            cmd, cwd=REPO_ROOT, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except OSError as e:
        raise TaskError(f"failed to run {' '.join(cmd)}: {e}") from e
    if check and proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() if capture else ""
        raise TaskError(f"command failed ({proc.returncode}): {' '.join(cmd)}\n{err}")
    return proc


def gh(*args: str, check: bool = True) -> str:
    return run(["gh", *args], check=check).stdout or ""


def git(*args: str, check: bool = True) -> str:
    return (run(["git", *args], check=check).stdout or "").strip()


def gh_json(*args: str):
    out = gh(*args)
    try:
        return json.loads(out or "null")
    except json.JSONDecodeError as e:
        raise TaskError(f"could not parse gh output as JSON: {out[:200]}") from e


_ME: str | None = None


def me() -> str:
    global _ME
    if _ME is None:
        _ME = gh("api", "user", "-q", ".login").strip()
        if not _ME:
            raise TaskError("could not determine GitHub login; run `gh auth login`.")
    return _ME


ISSUE_FIELDS = "number,title,state,labels,assignees,url"


def get_issue(number: int) -> dict:
    return gh_json("issue", "view", str(number), "--json", ISSUE_FIELDS)


def current_branch() -> str:
    b = git("rev-parse", "--abbrev-ref", "HEAD")
    if b == "HEAD":
        raise TaskError("detached HEAD; switch to a task branch first.")
    return b


def current_issue_number() -> int:
    b = current_branch()
    n = issue_number_from_branch(b)
    if n is None:
        raise TaskError(f"branch `{b}` has no issue-number prefix (expected `<N>-slug`). "
                        "Use `task.py claim N` to create one.")
    return n


def default_agent() -> str:
    a = os.environ.get("TASK_AGENT", "human").strip().lower() or "human"
    if a not in AGENTS:
        raise TaskError(f"TASK_AGENT={a!r} invalid; choose one of {', '.join(AGENTS)}")
    return a


def comment(number: int, body: str) -> None:
    gh("issue", "comment", str(number), "--body", body)


def print_table(rows: list[list[str]], headers: list[str]) -> None:
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    widths[-1] = min(widths[-1], 80)
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in widths]))
    for r in rows:
        print(fmt.format(*[str(x)[:80] for x in r]))


# ---------------------------------------------------------------- commands

def cmd_list(a) -> None:
    args = ["issue", "list", "--state", "open", "--limit", "200", "--json", ISSUE_FIELDS]
    if not a.all:
        args += ["--label", "status:ready"]
    if a.area:
        args += ["--label", f"area:{a.area}"]
    issues = gh_json(*args) or []
    prio_rank = {p: i for i, p in enumerate(PRIORITIES)}
    issues.sort(key=lambda i: (prio_rank.get(label_value(i["labels"], "priority"), 9), i["number"]))
    if not issues:
        print("No matching issues." + ("" if a.all else " Try `list --all`."))
        return
    if a.all:
        rows = [[f"#{i['number']}", label_value(i["labels"], "priority") or "-",
                 label_value(i["labels"], "area") or "-", status_of(i["labels"]),
                 ",".join(logins(i["assignees"])) or "-", i["title"]] for i in issues]
        print_table(rows, ["#", "prio", "area", "status", "assignee", "title"])
    else:
        rows = [[f"#{i['number']}", label_value(i["labels"], "priority") or "-",
                 label_value(i["labels"], "area") or "-", i["title"]] for i in issues]
        print_table(rows, ["#", "prio", "area", "title"])


def cmd_claim(a) -> None:
    agent = a.agent or default_agent()
    n = a.number
    login = me()
    issue = get_issue(n)
    if issue["state"].upper() != "OPEN":
        raise TaskError(f"#{n} is {issue['state'].lower()}; cannot claim.")
    labels = label_names(issue["labels"])
    others = [x for x in logins(issue["assignees"]) if x != login]
    if others and ({"status:claimed", "status:in-review"} & set(labels)):
        raise TaskError(f"#{n} is already claimed by {', '.join(others)} ({status_of(labels)}). "
                        "Pick another task or ask them to `release`.")
    if "status:blocked" in labels:
        print(f"warning: #{n} is labelled status:blocked; claiming anyway.", file=sys.stderr)

    remove = ["status:ready"] if "status:ready" in labels else []
    edit = ["issue", "edit", str(n), "--add-assignee", "@me",
            "--add-label", f"status:claimed,agent:{agent}"]
    if remove:
        edit += ["--remove-label", ",".join(remove)]
    gh(*edit)

    after = get_issue(n)
    others = [x for x in logins(after["assignees"]) if x != login]
    if others:
        gh("issue", "edit", str(n), "--remove-assignee", "@me", "--remove-label", f"agent:{agent}",
           check=False)
        raise TaskError(f"race: #{n} was also claimed by {', '.join(others)}. "
                        "Your assignment was undone; pick another task.")

    branch = branch_name(n, issue["title"])
    comment(n, f"Claimed by @{login} via {agent} on branch `{branch}`")
    git("fetch", "origin")
    exists = run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], check=False)
    if exists.returncode == 0:
        git("switch", branch)
        print(f"Switched to existing branch {branch}")
    else:
        git("switch", "-c", branch, "origin/main")
        print(f"Created branch {branch} from origin/main")
    print(f"Claimed #{n}: {issue['title']}\n{issue['url']}")


def cmd_status(a) -> None:
    b = current_branch()
    print(f"branch:  {b}")
    n = issue_number_from_branch(b)
    if n is not None:
        try:
            i = get_issue(n)
            print(f"issue:   #{n} {i['title']} [{i['state'].lower()}]")
            print(f"labels:  {', '.join(label_names(i['labels'])) or '-'}")
            print(f"assign:  {', '.join(logins(i['assignees'])) or '-'}")
        except TaskError as e:
            print(f"issue:   #{n} (could not load: {e})")
    else:
        print("issue:   (branch has no N- prefix)")
    counts = git("rev-list", "--left-right", "--count", "origin/main...HEAD", check=False)
    if counts:
        behind, ahead = counts.split()
        print(f"vs origin/main: {ahead} ahead, {behind} behind (run `git fetch` for fresh numbers)")
    dirty = git("status", "--porcelain")
    print("dirty:   " + ("clean" if not dirty else ""))
    for line in dirty.splitlines():
        print(f"  {line}")


def cmd_note(a) -> None:
    n = current_issue_number()
    agent = a.agent or default_agent()
    comment(n, f"Progress note ({agent}): {a.text}")
    print(f"Noted on #{n}.")


def cmd_sync(a) -> None:
    if git("status", "--porcelain", "--untracked-files=no"):
        raise TaskError("uncommitted changes; commit (or WIP-commit) before syncing.")
    git("fetch", "origin")
    proc = run(["git", "rebase", "origin/main"], check=False)
    if proc.returncode != 0:
        run(["git", "rebase", "--abort"], check=False)
        raise TaskError("rebase onto origin/main hit conflicts and was aborted; your branch is unchanged.\n"
                        "Resolve manually: `git rebase origin/main`, fix files, `git add`, "
                        "`git rebase --continue`.\n" + (proc.stdout or "") + (proc.stderr or ""))
    print("Rebased onto origin/main.")


def _run_checks() -> str:
    summary = ""
    py = sys.executable
    if (REPO_ROOT / "tests").is_dir():
        print("Running pytest ...")
        p = run([py, "-m", "pytest", "-q"], check=False, capture=False)
        if p.returncode != 0:
            raise TaskError("pytest failed; fix tests before `done` (or use --skip-checks).")
    if (REPO_ROOT / "data" / "dev").is_dir() and (REPO_ROOT / "system" / "screen.py").is_file():
        print("Running eval on data/dev ...")
        p = run([py, "system/screen.py", "eval", "--data", "data/dev"], check=False)
        out = (p.stdout or "") + (p.stderr or "")
        print(out)
        if p.returncode == 1:
            raise TaskError("eval exited 1: UNSAFE CLEARANCES detected. Do not open a PR; "
                            "fix the engine/rules first.")
        if p.returncode != 0:
            raise TaskError(f"eval failed with exit code {p.returncode}.")
        summary = parse_eval_summary(out)
    return summary


def cmd_done(a) -> None:
    n = current_issue_number()
    agent = a.agent or default_agent()
    if git("status", "--porcelain", "--untracked-files=no"):
        raise TaskError("uncommitted changes; commit them before `done`.")
    summary = "" if a.skip_checks else _run_checks()
    git("push", "-u", "origin", "HEAD")
    body = f"Closes #{n}\n\nAgent: {agent}\n"
    if summary:
        body += f"\n### Eval summary (data/dev)\n```\n{summary}\n```\n"
    elif a.skip_checks:
        body += "\nChecks skipped (--skip-checks).\n"
    pr = ["pr", "create", "--fill", "--body", body]
    if a.draft:
        pr.append("--draft")
    out = gh(*pr).strip()
    gh("issue", "edit", str(n), "--add-label", "status:in-review", "--remove-label", "status:claimed",
       check=False)
    print(out)
    print(f"#{n} marked status:in-review.")


def cmd_release(a) -> None:
    n = a.number or current_issue_number()
    issue = get_issue(n)
    labels = label_names(issue["labels"])
    remove = [x for x in labels if x == "status:claimed" or x.startswith("agent:")]
    args = ["issue", "edit", str(n), "--remove-assignee", "@me", "--add-label", "status:ready"]
    if remove:
        args += ["--remove-label", ",".join(remove)]
    gh(*args)
    reason = a.reason or "no reason given"
    comment(n, f"Released by @{me()}: {reason}")
    print(f"Released #{n}.")


def cmd_block(a) -> None:
    gh("issue", "edit", str(a.number), "--add-label", "status:blocked")
    comment(a.number, f"Blocked: {a.reason}")
    print(f"#{a.number} marked status:blocked.")


def cmd_new(a) -> None:
    labels = f"area:{a.area},status:ready,priority:{a.priority}"
    body = a.body or "Filed via tools/task.py new."
    out = gh("issue", "create", "--title", a.title, "--body", body, "--label", labels).strip()
    print(out)


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="task.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    s = sub.add_parser("list", help="list ready issues (or all open with --all)")
    s.add_argument("--area", help="filter by area (engine, rules, data, dashboard, demo, docs, infra)")
    s.add_argument("--all", action="store_true", help="show every open issue with status + assignee")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("claim", help="assign an issue to yourself and create its branch")
    s.add_argument("number", type=int)
    s.add_argument("--agent", choices=AGENTS, help="default: $TASK_AGENT or human")
    s.set_defaults(func=cmd_claim)

    s = sub.add_parser("status", help="show current branch, issue, ahead/behind, dirty files")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("note", help="post a progress note (handoff log) on the current issue")
    s.add_argument("text")
    s.add_argument("--agent", choices=AGENTS, help="default: $TASK_AGENT or human")
    s.set_defaults(func=cmd_note)

    s = sub.add_parser("sync", help="fetch and rebase onto origin/main (aborts cleanly on conflict)")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("done", help="run checks, push, open PR, mark status:in-review")
    s.add_argument("--draft", action="store_true", help="open the PR as a draft")
    s.add_argument("--skip-checks", action="store_true", help="skip pytest and eval")
    s.add_argument("--agent", choices=AGENTS, help="default: $TASK_AGENT or human")
    s.set_defaults(func=cmd_done)

    s = sub.add_parser("release", help="give an issue back to the pool (status:ready)")
    s.add_argument("number", type=int, nargs="?", help="default: issue of current branch")
    s.add_argument("--reason", help="why you are releasing it")
    s.set_defaults(func=cmd_release)

    s = sub.add_parser("block", help="mark an issue status:blocked with a reason")
    s.add_argument("number", type=int)
    s.add_argument("reason")
    s.set_defaults(func=cmd_block)

    s = sub.add_parser("new", help="file a follow-up issue (status:ready)")
    s.add_argument("title")
    s.add_argument("--area", required=True)
    s.add_argument("--priority", choices=PRIORITIES, default="p1")
    s.add_argument("--body")
    s.set_defaults(func=cmd_new)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        a.func(a)
    except TaskError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
