"""Deterministic rules: path globs, ignore/exclude partition, hard flags, size limits."""

import re
from functools import lru_cache

from .diff import FileChange


@lru_cache(maxsize=None)
def _rx(pattern: str) -> "re.Pattern[str]":
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(f"^(?:.*/)?{out}$")  # unanchored: "migrations/**" matches db/migrations/x.sql


def matches(path: str, patterns) -> "str | None":
    """First pattern matching the path, or None."""
    return next((p for p in patterns if _rx(p).match(path)), None)


def hit(f: FileChange, patterns) -> "str | None":
    """First pattern matching the file's new or old (rename source) path — for rules that must not be dodged."""
    return next((m for p in f.paths if (m := matches(p, patterns))), None)


def hit_all(f: FileChange, patterns) -> bool:
    """Every path matches — for opt-outs: a move out of (or into) an ignored area is still a real change."""
    return all(matches(p, patterns) for p in f.paths)


def partition(files: list[FileChange], cfg: dict) -> tuple[list[FileChange], list[FileChange], list[tuple[FileChange, str]], list[FileChange]]:
    """Drop `ignore`d files. Returns (kept, sendable, skipped, inert): skipped = kept files the model never sees
    although it should have (binary, exclude_from_api) -> not green; inert = `rules_only` files (tests, docs)."""
    kept = [f for f in files if not hit_all(f, cfg["ignore"])]
    sendable, skipped, inert = [], [], []
    for f in kept:
        if hit(f, cfg["exclude_from_api"]):
            skipped.append((f, "excluded from API"))
        elif hit_all(f, cfg["rules_only"]):
            inert.append(f)
        elif f.binary:
            skipped.append((f, "binary"))
        else:  # hunk-less too: a pure rename or a mode flip is a change the model can judge
            sendable.append(f)
    return kept, sendable, skipped, inert


def hard_flags(files: list[FileChange], cfg: dict) -> tuple[list[str], list[str], list[str]]:
    """-> (flags, reasons, reviewers). The model cannot override these."""
    flags, reasons, reviewers = [], [], []
    for group, patterns in cfg["sensitive_paths"].items():
        hits = {m for f in files if (m := hit(f, patterns))}
        if hits:
            flags.append(group)
            reasons += [f"{p} changed (hard flag: {group})" for p in sorted(hits)]
            if reviewer := cfg["reviewers"].get(group):
                reviewers.append(reviewer)
    lim, lines = cfg["limits"], sum(f.changed_lines for f in files)
    if lines > lim["max_lines"]:
        reasons.append(f"{lines} lines changed > max_lines {lim['max_lines']}: consider splitting")
    if len(files) > lim["max_files"]:
        reasons.append(f"{len(files)} files changed > max_files {lim['max_files']}: consider splitting")
    if lines > lim["max_lines"] or len(files) > lim["max_files"]:
        flags.append("size")
    return flags, reasons, reviewers
