"""Defaults + optional .wince.yml (looked up from cwd to the repo root)."""

import copy
import os
from pathlib import Path

import yaml

DEFAULTS = {
    "target_branch": "main",
    "ignore": ["**/*.lock", "gen/**"],
    "exclude_from_api": ["secrets/**", "**/*.pem"],
    "rules_only": [  # hard flags and size limits only; never sent to the model, never blocks green
        "tests/**", "test/**", "**/test_*", "**/*_test.*", "**/*.test.*", "**/*.spec.*", "**/conftest.py",
        "docs/**", "specs/**", "**/*.md", "**/*.rst",
    ],
    "sensitive_paths": {
        "dba": ["migrations/**", "**/*.sql"],
        "security": ["auth/**", "**/permissions*"],
    },
    "reviewers": {"dba": "@dba", "security": "@security"},
    "model_reviewers": {  # surface flag -> reviewers
        "security": {"flag": "auth_logic", "above": 0.6},
        "dba": {"flag": "data_write", "above": 0.8},
    },
    "risk_weights": {"blast_radius": 0.50, "error_weakened": 0.25, "contract_break": 0.25},
    "surface_multipliers": {  # surface flag -> risk amplifier
        "auth_logic": {"above": 0.7, "factor": 1.15},
        "data_write": {"above": 0.7, "factor": 1.15},
    },
    "thresholds": {"green": 0.15, "red": 0.50, "min_confidence": 0.5, "min_hunk_priority": 0.1},
    "limits": {"max_lines": 800, "max_files": 40},
}


def _merge(base: dict, over: dict) -> None:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v


def _dirs(start: "Path | None") -> list[Path]:
    """cwd up to the repo root (a dir holding .git), inclusive."""
    start = (start or Path.cwd()).resolve()
    out = []
    for d in (start, *start.parents):
        out.append(d)
        if (d / ".git").exists():
            break
    return out


def load(start: "Path | None" = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    for d in _dirs(start):
        p = d / ".wince.yml"
        if p.exists():
            _merge(cfg, yaml.safe_load(p.read_text(encoding="utf-8")) or {})
            break
    return cfg


def load_env(start: "Path | None" = None) -> None:
    """Nearest .env fills in variables the environment doesn't already set; real env vars always win."""
    for d in _dirs(start):
        p = d / ".env"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip().removeprefix("export ").strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))
            return
