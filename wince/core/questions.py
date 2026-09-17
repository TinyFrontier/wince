"""Questions to Jev (TypeSafe). Any wording change bumps QUESTIONS_VERSION."""

import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from typesafe_sdk import Choice, Noul, RetryPolicy, Score, TypeSafeClient

from .diff import FileChange

QUESTIONS_VERSION = "2026-09-17.2"
SURFACE = ("auth_logic", "data_write")  # where the change is; routes reviewers, amplifies risk
RISK = ("blast_radius", "error_weakened", "contract_break")  # how dangerous it is
NOUL = (*SURFACE, "error_weakened", "contract_break", "bug_risk")
BLAST_LABELS = ("isolated", "one feature", "core flow / data integrity")
LABELS = {
    "auth_logic": "auth logic changed",
    "data_write": "data write path changed",
    "error_weakened": "error handling weakened",
    "contract_break": "public contract broken",
    "bug_risk": "plausible runtime bug",
}


def build(hunk_ids: list[str]) -> dict:
    q = {
        "auth_logic": Noul(
            instructions="Does this change alter who can access what: authentication, authorization, "
            "permission checks, roles, tokens or sessions?",
            criteria={"true": "Executable access-control logic is added, removed or modified",
                      "false": "Access control is untouched, only formatting/comments changed, or the file is "
                               "documentation describing it"},
        ),
        "data_write": Noul(
            instructions="Does this change alter how data is written, updated or deleted in a database, "
            "file store, queue or external system?",
            criteria={"true": "Executable persistence logic changes: inserts, updates, deletes, migrations, schema",
                      "false": "Reads only, no persistence involved, or the file is documentation describing persistence"},
        ),
        "error_weakened": Noul(
            instructions="Does this change remove or weaken error handling: swallowed exceptions, removed "
            "validation, broadened catch blocks, ignored return codes, disabled checks?",
        ),
        "contract_break": Noul(
            instructions="Does this change break compatibility of a public API, event or payload: renamed or "
            "removed fields, changed types or semantics, removed endpoints or events?",
        ),
        "bug_risk": Noul(
            instructions="Could a mistake in this change plausibly cause wrong behaviour at runtime?",
            criteria={"true": "The file is executable code or executed configuration and the edited lines change "
                              "what the program does",
                      "false": "The file is documentation, a spec, a plan, a report, comments, or tests only, or the "
                               "edit is formatting/renaming with no behaviour change"},
        ),
        "blast_radius": Score(
            instructions="If this change contains a bug, how much breaks?",
            criteria=[
                "Isolated: one function, test or document; nothing else depends on it",
                "One feature: a single user-facing feature or module degrades",
                "Core flow or data integrity: shared infrastructure, many features, or stored data are affected",
            ],
        ),
    }
    if len(hunk_ids) >= 2:
        q["riskiest_hunk"] = Choice(
            instructions="Which hunk is most likely to introduce a bug?",
            criteria={h: None for h in hunk_ids},
        )
    return q


@dataclass
class FileAnswers:
    flags: dict[str, float]  # NOUL keys + blast_radius, all 0..1
    confidence: float  # of blast_radius
    hunk_probs: dict[str, float]  # hunk id -> P(riskiest)


def ask(client: TypeSafeClient, f: FileChange, title: str | None) -> FileAnswers:
    state: dict = {"path": f.path, "status": f.status,
                   "hunks": [{"id": h.id, "header": h.header, "body": h.body} for h in f.hunks]}
    if f.old_path and f.old_path != f.path:
        state["old_path"] = f.old_path
    if f.mode:
        state["mode_change"] = f.mode
    if title:
        state["title"] = title
    r = client.system_one(state=state, questions=build([h.id for h in f.hunks]))
    flags = {k: r.nouls[k].noul for k in NOUL}
    blast = r.scores["blast_radius"]
    flags["blast_radius"] = blast.score / (len(BLAST_LABELS) - 1)
    choice = r.choices.get("riskiest_hunk")
    probs = choice.probabilities if choice else {f.hunks[0].id: 1.0} if f.hunks else {}
    return FileAnswers(flags, blast.confidence, probs)


def ask_all(files: list[FileChange], title: str | None) -> list["FileAnswers | None"]:
    """One call per file, in parallel. A failed call yields None (F-20); it never aborts the run."""
    if not files:
        return []
    retry = RetryPolicy(max_retries=2, http_statuses={429, *range(500, 600)})  # 3 attempts total (N-2)

    def one(f: FileChange) -> "FileAnswers | None":
        try:
            return ask(client, f, title)
        except Exception as e:  # noqa: BLE001 - degrade, don't crash; class only, never the body (N-4)
            print(f"wince: {f.path}: model call failed ({type(e).__name__})", file=sys.stderr)
            return None

    with TypeSafeClient(retry=retry) as client, ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(one, files))
