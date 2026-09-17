"""Aggregate hard flags + per-file answers into a Verdict."""

from dataclasses import dataclass, field

from .diff import FileChange
from .questions import BLAST_LABELS, LABELS, NOUL, QUESTIONS_VERSION, RISK, FileAnswers


@dataclass
class Verdict:
    level: str  # green | yellow | red
    risk: float  # heuristic policy score in [0, 1], not a probability; shown as "review score N/100"
    confidence: float
    reading_order: list[tuple[str, str, float]]  # file, hunk, priority — experimental, see README
    reviewers: list[str]
    hard_flags: list[str]
    flags: dict[str, float]
    reasons: list[str]
    degraded: bool
    questions_version: str
    files: dict[str, dict[str, float]] = field(default_factory=dict)  # per-file flags; {} = not evaluated


def score(files: list[FileChange], answered: list, hard: tuple, cfg: dict, offline: bool = False,
          skipped: "list | None" = None, ignored: int = 0, inert: int = 0) -> Verdict:
    """`answered` = [(file, FileAnswers | None)] for files sent to the model; `skipped` = [(file, why)] never sent."""
    hard_flags, reasons, reviewers = (list(x) for x in hard)
    skipped = skipped or []
    t, w = cfg["thresholds"], cfg["risk_weights"]
    got = [(f, a) for f, a in answered if a is not None]
    failed = [f for f, a in answered if a is None]

    # worst file per flag. Not noisy-or (F-12): per-file "probably not" values are correlated noise, and
    # 20 files at 0.15 would otherwise combine into a confident 0.96.
    flags = {k: round(max((a.flags[k] for _, a in got), default=0.0), 3) for k in (*NOUL, *RISK)}
    top = max(got, key=lambda fa: fa[1].flags["blast_radius"], default=None)  # F-13: confidence of the max-blast file
    confidence = round(top[1].confidence, 3) if top else (0.0 if answered or skipped else 1.0)

    base = sum(w[k] * flags[k] for k in RISK)  # F-15
    for k, m in cfg["surface_multipliers"].items():
        if flags.get(k, 0.0) > m["above"]:
            base *= m["factor"]
            reasons.append(f"{LABELS[k]} ({flags[k]:.2f})")
    risk = round(min(1.0, base), 3)
    if got:
        label = BLAST_LABELS[round(flags["blast_radius"] * (len(BLAST_LABELS) - 1))]
        reasons.append(f"blast radius: {label} ({flags['blast_radius']:.2f})")
        reasons += [f"{LABELS[k]} ({flags[k]:.2f})" for k in ("error_weakened", "contract_break") if flags[k] >= 0.5]
        if confidence < t["min_confidence"]:
            reasons.append(f"low confidence {confidence:.2f} < {t['min_confidence']}")
    if failed:
        reasons.append(f"{'offline' if offline else 'model unavailable'}: {len(failed)} file(s) rated by hard flags only")
    if skipped:  # not evaluated at all: the verdict is incomplete, never green
        shown = ", ".join(f"{f.path} ({why})" for f, why in skipped[:5]) + (", ..." if len(skipped) > 5 else "")
        reasons.append(f"{len(skipped)} file(s) not evaluated: {shown}")
    if inert:
        reasons.append(f"{inert} file(s) rules-only (tests/docs), not sent to the model")
    if not files:
        reasons.append(f"all {ignored} changed file(s) ignored" if ignored else "no changes")
    degraded = bool(failed or skipped)

    if risk >= t["red"]:  # F-16; low confidence blocks green but does not escalate to red
        level = "red"
    elif risk < t["green"] and confidence >= t["min_confidence"] and not hard_flags and not degraded:
        level = "green"
    else:
        level = "yellow"

    for group, rule in cfg["model_reviewers"].items():  # F-17
        if flags.get(rule["flag"], 0.0) > rule["above"]:
            reviewers.append(cfg["reviewers"].get(group, f"@{group}"))

    order = []  # F-18
    for f, a in got:
        for h in f.hunks:
            p = a.hunk_probs.get(h.id, 0.0) * a.flags["runtime_effect"] * a.flags["blast_radius"]
            if p >= t["min_hunk_priority"]:
                order.append((f.path, h.id, round(p, 3)))
    order.sort(key=lambda x: (-x[2], x[0], x[1]))

    per_file = {f.path: {} for f in files}
    for f, a in got:
        per_file[f.path] = {k: round(v, 3) for k, v in a.flags.items()}
    return Verdict(level, risk, confidence, order, list(dict.fromkeys(reviewers)), hard_flags, flags,
                   reasons, degraded, QUESTIONS_VERSION, per_file)
