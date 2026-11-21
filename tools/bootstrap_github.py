#!/usr/bin/env python3
"""Idempotently set up GitHub labels and seed issues for the task board.

    python tools/bootstrap_github.py --dry-run
    python tools/bootstrap_github.py [--repo OWNER/NAME] [--seed tools/seed_issues.json]

(a) creates/updates the standard labels (colors + descriptions);
(b) creates issues from the seed JSON whose exact title does not already exist
    (open or closed). Issues with depends_on get status:blocked plus a
    "Depends on: #a, #b" line; others get status:ready.

Seed schema: JSON list of {"key", "title", "labels": [...], "depends_on": [keys], "body"}.
Standard library only; shells out to `gh`.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SEED = REPO_ROOT / "tools" / "seed_issues.json"

LABELS: dict[str, tuple[str, str]] = {
    "area:engine": ("1d76db", "Screening engine / matching core"),
    "area:rules": ("5319e7", "Policy rules and rule compiler"),
    "area:data": ("0e8a16", "Datasets, importers, fixtures"),
    "area:dashboard": ("fbca04", "Reviewer dashboard / UI"),
    "area:demo": ("f9d0c4", "Demo script, pitch, recordings"),
    "area:docs": ("c5def5", "Documentation"),
    "area:infra": ("bfdadc", "CI, tooling, repo setup"),
    "status:ready": ("0e8a16", "Ready to be claimed"),
    "status:claimed": ("fbca04", "Someone is working on it"),
    "status:in-review": ("1d76db", "PR open, awaiting review"),
    "status:blocked": ("b60205", "Blocked by a dependency or question"),
    "agent:claude": ("d4c5f9", "Worked by Claude Code"),
    "agent:grok": ("d4c5f9", "Worked by Grok Build"),
    "agent:cursor": ("d4c5f9", "Worked by Cursor"),
    "agent:codex": ("d4c5f9", "Worked by Codex"),
    "agent:human": ("d4c5f9", "Worked by a human"),
    "priority:p0": ("b60205", "Must have for the demo"),
    "priority:p1": ("d93f0b", "Should have"),
    "priority:p2": ("fef2c0", "Nice to have"),
    "contract-change": ("e11d21", "Changes a shared interface/contract; needs lead review"),
}


class BootstrapError(Exception):
    pass


def gh(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    if shutil.which("gh") is None:
        raise BootstrapError("`gh` not found on PATH. Install GitHub CLI: https://cli.github.com")
    p = subprocess.run(["gh", *args], cwd=REPO_ROOT, text=True, encoding="utf-8", errors="replace",
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and p.returncode != 0:
        raise BootstrapError(f"gh {' '.join(args)} failed:\n{(p.stderr or p.stdout).strip()}")
    return p


def gh_json(args: list[str]):
    out = gh(args).stdout
    try:
        return json.loads(out or "null")
    except json.JSONDecodeError as e:
        raise BootstrapError(f"bad JSON from gh: {out[:200]}") from e


def load_seed(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise BootstrapError(f"{path}: invalid JSON: {e}") from e
    if not isinstance(data, list):
        raise BootstrapError(f"{path}: expected a JSON list")
    keys = set()
    for i, item in enumerate(data):
        for f in ("key", "title"):
            if not isinstance(item.get(f), str) or not item[f]:
                raise BootstrapError(f"{path}[{i}]: missing string field '{f}'")
        if item["key"] in keys:
            raise BootstrapError(f"{path}: duplicate key {item['key']!r}")
        keys.add(item["key"])
    for item in data:
        for d in item.get("depends_on") or []:
            if d not in keys:
                raise BootstrapError(f"{path}: {item['key']!r} depends on unknown key {d!r}")
    return data


def topo_order(items: list[dict]) -> list[dict]:
    """Dependencies first so their issue numbers are known. Errors on cycles."""
    by_key = {i["key"]: i for i in items}
    out, state = [], {}

    def visit(k: str, stack: tuple = ()):
        if state.get(k) == 2:
            return
        if state.get(k) == 1:
            raise BootstrapError(f"dependency cycle: {' -> '.join(stack + (k,))}")
        state[k] = 1
        for d in by_key[k].get("depends_on") or []:
            visit(d, stack + (k,))
        state[k] = 2
        out.append(by_key[k])

    for i in items:
        visit(i["key"])
    return out


def ensure_labels(repo: str, dry: bool) -> None:
    existing = {lab["name"]: lab for lab in gh_json(
        ["label", "list", "--repo", repo, "--limit", "500", "--json", "name,color,description"]) or []}
    for name, (color, desc) in LABELS.items():
        cur = existing.get(name)
        if cur and cur.get("color", "").lower() == color and (cur.get("description") or "") == desc:
            print(f"label ok       {name}")
            continue
        action = "update" if cur else "create"
        print(f"label {action:<8} {name}" + ("  (dry-run)" if dry else ""))
        if not dry:
            gh(["label", "create", name, "--repo", repo, "--color", color,
                "--description", desc, "--force"])


def seed_issues(repo: str, seed_path: Path, dry: bool) -> None:
    items = load_seed(seed_path)
    if not items:
        print(f"no seed issues ({seed_path} missing or empty); skipping issue creation.")
        return
    existing = gh_json(["issue", "list", "--repo", repo, "--state", "all", "--limit", "1000",
                        "--json", "number,title"]) or []
    by_title = {i["title"]: i["number"] for i in existing}
    numbers: dict[str, int | str] = {}
    for item in topo_order(items):
        title = item["title"]
        if title in by_title:
            numbers[item["key"]] = by_title[title]
            print(f"issue exists   #{by_title[title]} {title}")
            continue
        deps = item.get("depends_on") or []
        labels = [x for x in item.get("labels") or [] if not x.startswith("status:")]
        labels.append("status:blocked" if deps else "status:ready")
        body = (item.get("body") or "").rstrip()
        if deps:
            refs = ", ".join(f"#{numbers[d]}" for d in deps)
            body += f"\n\nDepends on: {refs}"
        if dry:
            numbers[item["key"]] = f"<{item['key']}>"
            print(f"issue create   {title}  labels={','.join(labels)}"
                  + (f"  deps={','.join(deps)}" if deps else "") + "  (dry-run)")
            continue
        url = gh(["issue", "create", "--repo", repo, "--title", title, "--body", body or title,
                  "--label", ",".join(labels)]).stdout.strip()
        num = int(url.rstrip("/").rsplit("/", 1)[-1])
        numbers[item["key"]] = num
        by_title[title] = num
        print(f"issue created  #{num} {title}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", help="OWNER/NAME (default: current repo)")
    p.add_argument("--seed", type=Path, default=DEFAULT_SEED, help=f"seed JSON (default: {DEFAULT_SEED})")
    p.add_argument("--dry-run", action="store_true", help="print actions without changing anything")
    p.add_argument("--skip-labels", action="store_true")
    p.add_argument("--skip-issues", action="store_true")
    a = p.parse_args(argv)
    try:
        repo = a.repo or gh_json(["repo", "view", "--json", "nameWithOwner"])["nameWithOwner"]
        print(f"repo: {repo}" + ("  [DRY RUN]" if a.dry_run else ""))
        if not a.skip_labels:
            ensure_labels(repo, a.dry_run)
        if not a.skip_issues:
            seed_issues(repo, a.seed, a.dry_run)
    except BootstrapError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
