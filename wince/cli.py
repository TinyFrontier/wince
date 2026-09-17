"""wince check / wince ci github. Exit codes: 0 ok, 1 red with --fail-on red, 2 config or input error."""

import argparse
import os
import subprocess
import sys

import yaml

from . import config, github, inputs
from .core import diff, questions, render, rules, scoring


def die(msg: str) -> None:
    print(f"wince: {msg}", file=sys.stderr)
    sys.exit(2)


def evaluate(text: str, title: "str | None", cfg: dict, offline: bool) -> scoring.Verdict:
    parsed = diff.parse(text)
    if not parsed and text.strip():
        raise ValueError("input is not a unified diff")
    if cut := [f.path for f in parsed if f.truncated]:
        raise ValueError(f"input looks truncated (headers without a complete hunk): {', '.join(cut[:3])}")
    files, sendable, skipped, inert = rules.partition(parsed, cfg)
    hard = rules.hard_flags(files, cfg)
    answers = [None] * len(sendable) if offline else questions.ask_all(sendable, title)
    return scoring.score(files, list(zip(sendable, answers)), hard, cfg, offline, skipped, len(parsed) - len(files), len(inert))


def check(a: argparse.Namespace) -> int:
    cfg = config.load()
    config.load_env()
    if a.stdin:
        text, title = inputs.stdin(), a.title
    elif a.diff:
        text, title = inputs.file(a.diff), a.title
    else:
        text, title = inputs.local(a.base or cfg["target_branch"])
        title = a.title or title
    if not a.offline and not os.environ.get("TYPESAFE_API_KEY"):
        die("TYPESAFE_API_KEY is not set (use --offline for hard flags only)")
    v = evaluate(text, title, cfg, a.offline)
    print(render.json_(v) if a.format == "json" else render.text(v))
    return 1 if a.fail_on == "red" and v.level == "red" else 0


def ci_github(a: argparse.Namespace) -> int:
    cfg = config.load()
    ev = github.event()
    pr = ev.get("pull_request") or die("not a pull_request event")
    token = os.environ.get("GITHUB_TOKEN") or die("GITHUB_TOKEN is not set")
    offline = (pr["head"].get("repo") or {}).get("fork", True) or not os.environ.get("TYPESAFE_API_KEY")  # G-3
    text, _ = inputs.local(pr["base"]["ref"])
    v = evaluate(text, pr["title"], cfg, offline)
    note = "offline: hard flags only (fork PR or no TYPESAFE_API_KEY)" if offline else None
    print(render.text(v))
    try:
        github.upsert_comment(ev["repository"]["full_name"], pr["number"], render.markdown(v, note), token)
    except OSError as e:  # fork PRs get a read-only GITHUB_TOKEN on `pull_request`: keep the job green, report is above
        code = getattr(e, "code", None)
        print(f"wince: could not post the PR comment ({'HTTP ' + str(code) if code else e}); "
              "fork PRs get a read-only token — see the report in this log", file=sys.stderr)
    return 0


def main(argv: "list[str] | None" = None) -> int:
    p = argparse.ArgumentParser(prog="wince", description="Semantic risk router for code changes. "
                                "Diff hunks of non-ignored files are sent to the TypeSafe API.")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="rate the local branch, --stdin or --diff PATH")
    c.add_argument("--base", metavar="BRANCH", help="target branch (default: config target_branch)")
    src = c.add_mutually_exclusive_group()
    src.add_argument("--stdin", action="store_true", help="read a unified diff from stdin")
    src.add_argument("--diff", metavar="PATH", help="read a unified diff from a file")
    c.add_argument("--title", help="change title given to the model (default: last commit subject for a branch)")
    c.add_argument("--offline", action="store_true", help="hard flags only, no network")
    c.add_argument("--format", choices=["text", "json"], default="text")
    c.add_argument("--fail-on", choices=["red"], help="exit 1 when the level is red")
    ci = sub.add_parser("ci", help="CI integrations").add_subparsers(dest="provider", required=True)
    ci.add_parser("github", help="post the verdict as one PR comment")
    a = p.parse_args(argv)
    try:
        return check(a) if a.cmd == "check" else ci_github(a)
    except subprocess.CalledProcessError as e:
        die(f"git failed: {((e.stderr or '').strip().splitlines() or [''])[0]}")
    except (OSError, yaml.YAMLError, KeyError, TypeError, ValueError) as e:
        die(f"{type(e).__name__}: {e}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
