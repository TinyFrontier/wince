"""Verdict -> terminal text / JSON / GitHub markdown."""

import json
import sys
from dataclasses import asdict

from .scoring import Verdict

TOP = 5
MARKER = "<!-- wince -->"
_ANSI = {"green": "32", "yellow": "33", "red": "31"}
_DOT = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
_KEYS = ("auth_logic", "data_write", "error_weakened", "contract_break", "bug_risk", "blast_radius")


def text(v: Verdict) -> str:
    lvl = v.level.upper()
    if sys.stdout.isatty():
        lvl = f"\033[1;{_ANSI[v.level]}m{lvl}\033[0m"
    out = [f"wince  {lvl}  risk {v.risk:.2f}  confidence {v.confidence:.2f}", ""]
    if v.degraded:
        out += ["warning: model answers missing for some files; level cannot be green", ""]
    if v.reading_order:
        rows = [(f"{i}. {p}:{h}", s) for i, (p, h, s) in enumerate(v.reading_order[:TOP], 1)]
        width = max(len(r) for r, _ in rows) + 3
        out += ["Review first:", *(f"{r.ljust(width)}{s:.2f}" for r, s in rows), ""]
    out.append("Suggested reviewers: " + (" ".join(v.reviewers) or "none"))
    if v.reasons:
        out += ["", "Why:", *(f"- {r}" for r in v.reasons)]
    return "\n".join(out)


def json_(v: Verdict) -> str:
    return json.dumps(asdict(v), indent=2, ensure_ascii=False)


def markdown(v: Verdict, note: str | None = None) -> str:
    out = [MARKER, f"**wince** {_DOT[v.level]} **{v.level.upper()}** · risk {v.risk:.2f} · confidence {v.confidence:.2f}", ""]
    if note:
        out += [f"_{note}_", ""]
    if v.degraded:
        out += ["⚠️ model answers missing for some files; level cannot be green", ""]
    if v.reading_order:
        out += ["**Review first**", "", *(f"{i}. `{p}:{h}` — {s:.2f}" for i, (p, h, s) in enumerate(v.reading_order[:TOP], 1)), ""]
    out += ["**Suggested reviewers:** " + (" ".join(v.reviewers) or "none"), ""]
    if v.reasons:
        out += ["**Why**", "", *(f"- {r}" for r in v.reasons), ""]
    if v.files:
        out += ["<details><summary>Details by file</summary>", "",
                "| File | " + " | ".join(_KEYS) + " |", "|" + "---|" * (len(_KEYS) + 1)]
        for path, fl in v.files.items():
            cells = [f"{fl[k]:.2f}" for k in _KEYS] if fl else ["-"] * len(_KEYS)
            out.append(f"| `{path}` | " + " | ".join(cells) + " |")
        out += ["", "</details>"]
    return "\n".join(out)
