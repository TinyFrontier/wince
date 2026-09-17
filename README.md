# wince

**Wince is a semantic risk router for code changes.** It doesn't review your code. It tells you how carefully it should be reviewed, who should review it, and where they should look first.

Feed it any unified diff — a local branch, a PR, a coding agent's output, a dependency bump — and get back a level (green / yellow / red), a reading order for the hunks, and suggested reviewers. Seconds, not minutes.

![wince demo](assets/demo.gif)

```
wince  RED  risk 0.62  confidence 0.81

Review first:
1. src/auth/permissions.ts:h3   0.91
2. db/migrations/0182.sql:h1    0.84
3. src/api/orders.ts:h2         0.37

Suggested reviewers: @security @dba

Why:
- migrations/** changed (hard flag: dba)
- auth logic changed (0.93)
- blast radius: core flow / data integrity (0.85)
```

What it does **not** do: write a review, hunt for specific bugs, judge whether the change matches the ticket, approve or block a merge.

## How it works

```
git diff / stdin
   ↓
deterministic rules      — path globs and size limits; the model cannot override these
   ↓
Jev (TypeSafe)           — one typed call per file, in parallel:
 ├─ auth_logic, data_write          surface: who should look
 ├─ blast_radius, error_weakened,   risk: how dangerous
 │  contract_break, bug_risk
 └─ riskiest_hunk                   where to look first
   ↓
risk score + confidence
   ↓
green / yellow / red
```

Questions go to [TypeSafe](https://docs.typesafe.ai/introduction) as typed primitives (yes/no probability, rubric score, choice) — no prompt, no text parsing.

## Install

```bash
pipx install git+https://github.com/TinyFrontier/wince   # or: uv tool install git+https://github.com/TinyFrontier/wince
export TYPESAFE_API_KEY=...
```

Not on PyPI yet.

Python 3.10+. The key is read from the environment; a `.env` file in the repo (`TYPESAFE_API_KEY=...`) fills it in when the variable isn't set. Keep `.env` out of git.

## Use

```bash
wince check                          # diff of merge-base(HEAD, origin/main)...HEAD
wince check --base develop
git diff | wince check --stdin       # any diff, e.g. from a coding agent
wince check --diff changes.patch
wince check --format json            # Verdict as JSON for other tools
wince check --offline                # hard flags only, nothing leaves the machine
wince check --fail-on red            # exit 1 on red
wince ci github                      # v0.2: post the verdict as one PR comment
```

Exit codes: `0` ok · `1` red with `--fail-on red` · `2` config or input error.

## ⚠️ Your diff is sent to an external API

Unless you run with `--offline`, the hunks of every changed file that isn't matched by `ignore` or `exclude_from_api` are sent to the TypeSafe API. Put secrets, keys and anything you can't share into `exclude_from_api`. Binary files are never sent. The API key is read from `TYPESAFE_API_KEY` and never printed or logged.

## Configuration

Optional `.wince.yml` in the repo root. Every key has the default shown here.

```yaml
target_branch: main
ignore: ["**/*.lock", "gen/**"]              # dropped entirely (a rename out of or into an ignored area is kept)
exclude_from_api: ["secrets/**", "**/*.pem"] # never sent to the model; counts as unevaluated (no green)
rules_only:                                  # hard flags and size limits only; never sent, never blocks green
  ["tests/**", "test/**", "**/test_*", "**/*_test.*", "**/*.test.*", "**/*.spec.*", "**/conftest.py",
   "docs/**", "specs/**", "**/*.md", "**/*.rst"]

sensitive_paths:                             # hard flags: level is at least yellow
  dba:      ["migrations/**", "**/*.sql"]
  security: ["auth/**", "**/permissions*"]

reviewers:
  dba: "@dba"
  security: "@security"

model_reviewers:            # surface flag -> reviewers
  security: { flag: auth_logic, above: 0.6 }
  dba:      { flag: data_write, above: 0.8 }

risk_weights:               # risk = weighted sum, capped at 1
  blast_radius: 0.50
  error_weakened: 0.25
  contract_break: 0.25

surface_multipliers:        # surface flag -> risk amplifier
  auth_logic: { above: 0.7, factor: 1.15 }
  data_write: { above: 0.7, factor: 1.15 }

thresholds: { green: 0.15, red: 0.50, min_confidence: 0.5, min_hunk_priority: 0.1 }
limits: { max_lines: 800, max_files: 40 }    # exceeded -> at least yellow, "consider splitting"
```

Globs match at any depth: `migrations/**` matches `db/migrations/0182.sql`.

**The weights and thresholds are heuristics.** They are not calibrated against real outcomes (reverts, hotfixes, incidents). Treat the score as a triage hint, not a measurement; tune the config to your repo.

## The verdict

- **green** — risk below `green`, confidence at or above `min_confidence`, no hard flags, and every non-ignored file answered by the model.
- **red** — risk at or above `red`. Low confidence never escalates; it only blocks green.
- **yellow** — everything else, including any hard flag, oversize changes, `--offline`, and any file the model did not answer (`degraded: true`): a failed call, a binary, or a file in `exclude_from_api`. An API failure never crashes the run, and an unevaluated file never yields green — add noise like images to `ignore` if you want it out of the verdict entirely.

Tests, docs and specs (`rules_only`) still trip path rules and size limits but are not sent to the model: a doc that *describes* an API change would otherwise score as one, and a docs-only change is green. Renames are checked against both the old and the new path, so moving `auth/check.py` elsewhere still trips the `security` flag, and moving `gen/check.py` into `auth/` is not ignored. Hunk-less changes — a pure rename, a mode flip on `bin/deploy.sh` — are sent to the model with `old_path` / `mode_change` instead of hunks. Input that isn't a unified diff, or is cut off (headers without a complete hunk), is an error (exit 2), not "no changes".

Reading order = `P(riskiest_hunk) × bug_risk × blast_radius` per hunk, top 5 in the terminal, hunks under `min_hunk_priority` hidden. Every flag is the worst file's value — not noisy-or, which would turn twenty files at 0.15 into a confident 0.96 — and `blast_radius` lends its confidence.

`--format json` prints the `Verdict` — the public contract for other tools:

```json
{
  "level": "red", "risk": 0.62, "confidence": 0.81,
  "reading_order": [["src/auth/permissions.ts", "h3", 0.91]],
  "reviewers": ["@security", "@dba"],
  "hard_flags": ["dba"],
  "flags": {"auth_logic": 0.93, "data_write": 0.4, "error_weakened": 0.1, "contract_break": 0.2, "bug_risk": 0.8, "blast_radius": 0.85},
  "reasons": ["migrations/** changed (hard flag: dba)", "auth logic changed (0.93)"],
  "degraded": false,
  "questions_version": "2026-09-17.2",
  "files": {"src/auth/permissions.ts": {"auth_logic": 0.93, "...": 0}}
}
```

## GitHub (v0.2)

`wince ci github` computes the same verdict and posts it as a single PR comment (marker `<!-- wince -->`), edited in place on re-runs. PRs from forks have no secrets, so they run `--offline` and the comment says so; on a plain `pull_request` event their `GITHUB_TOKEN` is read-only, so the comment cannot be posted — the report stays in the job log and the job still passes.

```yaml
on: pull_request
permissions:
  contents: read
  pull-requests: write
jobs:
  wince:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - run: pipx install git+https://github.com/TinyFrontier/wince
      - run: wince ci github
        env:
          TYPESAFE_API_KEY: ${{ secrets.TYPESAFE_API_KEY }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

## Demo

`examples/` holds four patches, one per pillar of the MVP:

| Patch | Expected | Shows |
|---|---|---|
| `1-readme-typo.patch` | green | high confidence, no reviewers |
| `2-migration-write.patch` | yellow or red, `@dba` | hard flag + `data_write` |
| `3-big-pr-permissions.patch` | red, `@security`, the `can()` hunk first | `riskiest_hunk` and reading order |
| `4-agent-diff.patch` via `--stdin` | JSON verdict | any diff, not just a branch |

```bash
wince check --diff examples/3-big-pr-permissions.patch
cat examples/4-agent-diff.patch | wince check --stdin --format json
```

## Status

MVP, uncalibrated. Measured on 60 merged MRs of one Python backend, with "a fix commit touched this MR's lines within 14 days" as the label: the risk score ranks risky MRs slightly better than diff size (AUC 0.73 vs 0.68); `red` at 0.80 would mark the top third, ~90% of which needed a follow-up fix; green is never earned by the model on code changes. The per-hunk reading order did **not** beat "largest hunk first" on the 16 MRs with a traceable fix — treat it as unproven. The eval data is not in the repo (private MRs).

## Development

```bash
uv venv && uv pip install -e . pytest
python tests/test_core.py      # or: pytest
```
