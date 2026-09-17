# wince

**wince tells your team where review attention should go.**

🟢 GREEN — routine review · 🟡 YELLOW — slow down · 🔴 RED — high-attention change, and who should look.

It doesn't review the code. It routes the review.

Feed it any unified diff — a local branch, a PR, a coding agent's output, a dependency bump — and get a level, suggested reviewers, and the reasons. Seconds, not minutes; deterministic path rules the model cannot override.

![wince demo](assets/demo.gif)

```
wince  RED  high-attention change
review score 91/100 · confidence 0.74 · heuristic

Suggested reviewers: @security

Why:
- auth/** changed (hard flag: security)
- auth logic changed (0.97)
- blast radius: core flow / data integrity (0.92)
- public contract broken (0.90)

Reading order (experimental):
1. src/auth/permissions.ts:h2     0.83
2. src/ui/OrdersTable.tsx:h1      0.44
```

What it does **not** do: write a review, hunt for specific bugs, judge whether the change matches the ticket, approve or block a merge.

## Does it work?

Measured on 60 merged MRs of one Python backend, with "a fix commit touched this MR's lines within 14 days" as the label:

- The score ranks risky MRs somewhat better than diff size: **AUC 0.73 vs 0.68**. Inside large MRs (>1000 lines), where size stops discriminating (0.45), the score still does (0.73).
- **RED at score ≥ 80** would mark the top third of MRs, and **~90% of those needed a follow-up fix** (base rate 62%).
- GREEN is never earned by the model on code changes; the lowest-scored MRs needed fixes as often as the rest. Green means "nothing for the model to worry about" — docs, tests, no-ops.
- The per-hunk **reading order did not beat "largest hunk first"** on the 16 MRs with a traceable fix. It stays in the output, marked experimental, until it does.

Small sample, one repo, uncalibrated weights: treat the level as triage, not a measurement. The eval data is not in the repo (private MRs).

## How it works

```
git diff / stdin
   ↓
deterministic rules      — path globs and size limits; the model cannot override these
   ↓
Jev (TypeSafe)           — one typed call per file, in parallel, diff only (no title, no ticket):
 ├─ auth_logic, data_write           surface: who should look
 ├─ blast_radius, error_weakened,    risk: how dangerous
 │  contract_break, runtime_effect
 └─ riskiest_hunk                    experimental: where to look first
   ↓
review score + confidence
   ↓
green / yellow / red
```

Questions go to [TypeSafe](https://docs.typesafe.ai/introduction) as typed primitives (yes/no probability, rubric score, choice) — no prompt, no text parsing. The score is computed in code from the answers.

## Install

```bash
pipx install git+https://github.com/TinyFrontier/wince   # or: uv tool install git+https://github.com/TinyFrontier/wince
export TYPESAFE_API_KEY=...
```

Not on PyPI yet. Python 3.10+. The key is read from the environment; a `.env` file in the repo (`TYPESAFE_API_KEY=...`) fills it in when the variable isn't set. Keep `.env` out of git.

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

Unless you run with `--offline`, the hunks of every changed file that isn't matched by `ignore`, `rules_only` or `exclude_from_api` are sent to the TypeSafe API. Put secrets, keys and anything you can't share into `exclude_from_api`. Binary files are never sent. Only the diff goes: no PR title, description or ticket. The API key is read from `TYPESAFE_API_KEY` and never printed or logged.

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

risk_weights:               # score = weighted sum, capped at 1
  blast_radius: 0.50
  error_weakened: 0.25
  contract_break: 0.25

surface_multipliers:        # surface flag -> score amplifier
  auth_logic: { above: 0.7, factor: 1.15 }
  data_write: { above: 0.7, factor: 1.15 }

thresholds: { green: 0.15, red: 0.50, min_confidence: 0.5, min_hunk_priority: 0.1 }
limits: { max_lines: 800, max_files: 40 }    # exceeded -> at least yellow, "consider splitting"
```

Globs match at any depth: `migrations/**` matches `db/migrations/0182.sql`. Alembic-style repos want `**/database/versions/**` under `dba`.

**The weights and thresholds are heuristics.** See [Does it work?](#does-it-work) for what one repo's data says about them; `red: 0.80` fit that repo better than the default `0.50`.

## The verdict

- **green** — score below `green`, confidence at or above `min_confidence`, no hard flags, and every non-ignored file answered by the model.
- **red** — score at or above `red`. Low confidence never escalates; it only blocks green.
- **yellow** — everything else, including any hard flag, oversize changes, `--offline`, and any file the model did not answer (`degraded: true`): a failed call, a binary, or a file in `exclude_from_api`. An API failure never crashes the run, and an unevaluated file never yields green — add noise like images to `ignore` if you want it out of the verdict entirely.

`review score` is a policy score, not a probability: `0.50·blast_radius + 0.25·error_weakened + 0.25·contract_break`, amplified when auth or data-write logic changed. Every flag is the worst file's value — not noisy-or, which would turn twenty files at 0.15 into a confident 0.96 — and `blast_radius` lends its confidence. `runtime_effect` is "does this change behaviour at runtime" — it separates code from docs and no-ops, nothing more.

Tests, docs and specs (`rules_only`) still trip path rules and size limits but are not sent to the model: a doc that *describes* an API change would otherwise score as one, and a docs-only change is green. Renames are checked against both the old and the new path, so moving `auth/check.py` elsewhere still trips the `security` flag, and moving `gen/check.py` into `auth/` is not ignored. Hunk-less changes — a pure rename, a mode flip on `bin/deploy.sh` — are sent to the model with `old_path` / `mode_change` instead of hunks. Input that isn't a unified diff, or is cut off, is an error (exit 2), not "no changes".

Reading order (experimental) = `P(riskiest_hunk) × runtime_effect × blast_radius` per hunk, top 5, hunks under `min_hunk_priority` hidden.

`--format json` prints the `Verdict` — the public contract for other tools:

```json
{
  "level": "red", "risk": 0.91, "confidence": 0.74,
  "reading_order": [["src/auth/permissions.ts", "h2", 0.83]],
  "reviewers": ["@security"],
  "hard_flags": ["security"],
  "flags": {"auth_logic": 0.97, "data_write": 0.3, "error_weakened": 0.2, "contract_break": 0.9, "runtime_effect": 0.95, "blast_radius": 0.92},
  "reasons": ["auth/** changed (hard flag: security)", "auth logic changed (0.97)"],
  "degraded": false,
  "questions_version": "2026-09-17.3",
  "files": {"src/auth/permissions.ts": {"auth_logic": 0.97, "...": 0}}
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

`examples/` holds four patches:

| Patch | Expected | Shows |
|---|---|---|
| `1-readme-typo.patch` | green | docs only, nothing sent to the model |
| `2-migration-write.patch` | yellow or red, `@dba` | hard flag + `data_write` |
| `3-big-pr-permissions.patch` | red, `@security` | surface flags, reviewer routing |
| `4-agent-diff.patch` via `--stdin` | JSON verdict | any diff, not just a branch |

```bash
wince check --diff examples/3-big-pr-permissions.patch
cat examples/4-agent-diff.patch | wince check --stdin --format json
```

## Development

```bash
uv venv && uv pip install -e . pytest
python tests/test_core.py      # or: pytest
vhs assets/demo.tape           # re-record the GIF
```
