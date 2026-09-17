"""Runnable check: `python tests/test_core.py` or `pytest`. No network."""

import copy
import io
import json
import os
import sys
import tempfile
import urllib.error
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wince import cli, config, github, inputs  # noqa: E402
from wince.core import diff, questions, render, rules, scoring  # noqa: E402
from wince.core.questions import FileAnswers  # noqa: E402

CFG = copy.deepcopy(config.DEFAULTS)
NONE = ([], [], [])
GIT_DIFF = """\
diff --git a/README.md b/README.md
index 1..2 100644
--- a/README.md
+++ b/README.md
@@ -1,3 +1,3 @@
 # Demo
-Teh quick fox
+The quick fox
 end
diff --git a/db/migrations/0182.sql b/db/migrations/0182.sql
new file mode 100644
--- /dev/null
+++ b/db/migrations/0182.sql
@@ -0,0 +1,3 @@
+-- add index
+--- this removed line starts with three dashes
+CREATE INDEX ix ON orders(id);
diff --git a/logo.png b/logo.png
Binary files a/logo.png and b/logo.png differ
diff --git a/old.py b/new.py
similarity index 100%
rename from old.py
rename to new.py
diff --git a/src/auth/permissions.ts b/src/auth/permissions.ts
--- a/src/auth/permissions.ts
+++ b/src/auth/permissions.ts
@@ -1 +1 @@
-a
+b
@@ -10 +10 @@
-c
+d
"""
QUOTED = ('diff --git "a/\\320\\272\\320\\273\\321\\216\\321\\207.pem" "b/\\320\\272\\320\\273\\321\\216\\321\\207.pem"\n'
          '--- "a/\\320\\272\\320\\273\\321\\216\\321\\207.pem"\n+++ "b/\\320\\272\\320\\273\\321\\216\\321\\207.pem"\n@@ -1 +1 @@\n-x\n+y\n'
          'diff --git "a/my key.pem" "b/my key.pem"\n--- "a/my key.pem"\n+++ "b/my key.pem"\n@@ -1 +1 @@\n-x\n+y\n'
          'diff --git "a/t\\tab.txt" "b/t\\tab.txt"\n--- "a/t\\tab.txt"\n+++ "b/t\\tab.txt"\n@@ -1 +1 @@\n-x\n+y\n')


def rename(src, dst, hunk=True):
    d = f"diff --git a/{src} b/{dst}\nsimilarity index {'90' if hunk else '100'}%\nrename from {src}\nrename to {dst}\n"
    return d + (f"--- a/{src}\n+++ b/{dst}\n@@ -1 +1 @@\n-x\n+y\n" if hunk else "")


def one(path, body="-a\n+b\n", header="@@ -1 +1 @@"):
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n{header}\n{body}"


def fa(blast=0.5, conf=0.9, bug=0.5, hunks=None, **flags):
    f = {"auth_logic": 0.0, "data_write": 0.0, "error_weakened": 0.0, "contract_break": 0.0, "runtime_effect": bug, "blast_radius": blast}
    f.update(flags)
    return FileAnswers(f, conf, hunks or {"h1": 1.0})


def test_parse():
    files = diff.parse(GIT_DIFF)
    assert [f.path for f in files] == ["README.md", "db/migrations/0182.sql", "logo.png", "new.py", "src/auth/permissions.ts"]
    assert [f.status for f in files] == ["modified", "added", "modified", "renamed", "modified"]
    sql = files[1]
    assert sql.hunks[0].body.count("\n") == 2 and "three dashes" in sql.hunks[0].body  # "---" inside a hunk stays in it
    assert files[2].binary and not files[2].hunks and not files[3].hunks and files[3].paths == ("new.py", "old.py")
    assert [h.id for h in files[4].hunks] == ["h1", "h2"] and files[4].changed_lines == 4
    assert not any(f.truncated for f in files)
    lying = diff.parse("--- a/x.py\n+++ b/x.py\n@@ -1,9 +1,9 @@\n-a\n+b\n@@ -5 +5 @@\n-c\n+d\ndiff --git a/y.py b/y.py\n--- a/y.py\n+++ b/y.py\n@@ -1 +1 @@\n-e\n+f\n")
    assert [(f.path, len(f.hunks)) for f in lying] == [("x.py", 2), ("y.py", 1)]  # wrong @@ counts don't swallow headers
    plain = diff.parse("--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n--- a/y.py\n+++ b/y.py\n@@ -1 +1 @@\n-c\n+d\n")
    assert [f.path for f in plain] == ["x.py", "y.py"]
    assert [f.path for f in diff.parse(QUOTED)] == ["ключ.pem", "my key.pem", "t\tab.txt"]  # git-quoted paths decoded
    mode = diff.parse("diff --git a/bin/deploy.sh b/bin/deploy.sh\nold mode 100755\nnew mode 100644\n")[0]
    assert mode.mode == "100755 -> 100644" and not mode.hunks and not mode.truncated


def test_globs_and_rules():
    assert rules.matches("db/migrations/0182.sql", ["migrations/**"]) == "migrations/**"
    assert rules.matches("x.sql", ["**/*.sql"]) and rules.matches("src/auth/permissions.ts", ["auth/**"])
    assert rules.matches("poetry.lock", CFG["ignore"]) and not rules.matches("src/lock.py", CFG["ignore"])
    files = diff.parse(GIT_DIFF)
    kept, sendable, skipped, inert = rules.partition(files, CFG)
    assert len(kept) == 5 and [f.path for f in sendable] == ["db/migrations/0182.sql", "new.py", "src/auth/permissions.ts"]
    assert [(f.path, why) for f, why in skipped] == [("logo.png", "binary")] and [f.path for f in inert] == ["README.md"]
    flags, reasons, reviewers = rules.hard_flags(kept, CFG)
    assert flags == ["dba", "security"] and reviewers == ["@dba", "@security"]
    assert "migrations/** changed (hard flag: dba)" in reasons
    big = copy.deepcopy(CFG)
    big["limits"]["max_files"] = 2
    assert "size" in rules.hard_flags(kept, big)[0]


def test_levels():
    f = diff.parse(GIT_DIFF)
    readme, perms = f[0], f[4]
    green = scoring.score([readme], [(readme, fa(blast=0.0, bug=0.1))], NONE, CFG)
    assert green.level == "green" and green.reading_order == [] and green.reviewers == []
    # F-5: hard flag floors the level at yellow
    assert scoring.score([readme], [(readme, fa(blast=0.0))], (["dba"], ["x"], ["@dba"]), CFG).level == "yellow"
    # F-20: API failure -> degraded, never green
    deg = scoring.score([readme], [(readme, None)], NONE, CFG)
    assert deg.level == "yellow" and deg.degraded and any("model unavailable" in r for r in deg.reasons)
    # low confidence blocks green, never escalates: risk 0.25 -> yellow, not red
    low = scoring.score([readme], [(readme, fa(blast=0.5, conf=0.2))], NONE, CFG)
    assert low.level == "yellow" and "low confidence 0.20 < 0.5" in low.reasons
    assert scoring.score([readme], [(readme, fa(blast=0.0, bug=0.1, conf=0.2))], NONE, CFG).level == "yellow"
    # every flag aggregates by worst file, never noisy-or
    two = scoring.score([readme, perms], [(readme, fa(error_weakened=0.3, auth_logic=0.5)), (perms, fa(error_weakened=0.4, auth_logic=0.5))], NONE, CFG)
    assert two.flags["error_weakened"] == 0.4 and two.flags["auth_logic"] == 0.5
    # red example: dangerous hunk h2 first, @security via model_reviewers, auth multiplier in reasons
    ans = fa(blast=1.0, bug=0.9, auth_logic=0.93, hunks={"h1": 0.1, "h2": 0.9})
    red = scoring.score([readme, perms], [(readme, fa(blast=0.0, bug=0.1)), (perms, ans)], NONE, CFG)
    assert red.level == "red" and red.reviewers == ["@security"]
    assert red.reading_order[0][:2] == ("src/auth/permissions.ts", "h2") and len(red.reading_order) == 1  # h1 < 0.1 dropped
    assert red.risk == round(min(1, 0.5 * 1.0 * 1.15), 3) and "auth logic changed (0.93)" in red.reasons
    assert red.files["README.md"]["blast_radius"] == 0.0 and red.flags["auth_logic"] == 0.93
    assert scoring.score([], [], NONE, CFG).level == "green"
    off = cli.evaluate(GIT_DIFF, CFG, offline=True)
    assert off.level == "yellow" and off.hard_flags == ["dba", "security"] and off.degraded


def test_render_stable():
    v = cli.evaluate(GIT_DIFF, CFG, offline=True)
    a, b = render.json_(v), render.json_(cli.evaluate(GIT_DIFF, CFG, offline=True))
    assert a == b and json.loads(a)["level"] == "yellow"
    md = render.markdown(v, note="offline")
    assert md.startswith(render.MARKER) and "<details>" in md and "_offline_" in md
    t = render.text(v)
    assert "Suggested reviewers: @dba @security" in t and "review score 0/100" in t and "risk" not in t.split("\n")[0]


class FakeClient:
    """Stands in for TypeSafeClient: answers from the questions asked, fails on path 'boom'."""
    calls = []

    def __init__(self, **kw): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass

    def system_one(self, state, questions):
        FakeClient.calls.append((state, questions))
        if state["path"] == "boom":
            raise RuntimeError("api down")
        nouls = {k: NS(noul=0.8 if k == "auth_logic" else 0.2) for k in questions if k not in ("blast_radius", "riskiest_hunk")}
        choices = {"riskiest_hunk": NS(probabilities={"h1": 0.3, "h2": 0.7})} if "riskiest_hunk" in questions else {}
        return NS(nouls=nouls, scores={"blast_radius": NS(score=1.5, confidence=0.9)}, choices=choices)


def test_ask_wiring():
    files = diff.parse(GIT_DIFF)
    perms, readme, moved = files[4], files[0], files[3]
    boom = diff.FileChange("boom", "modified", [diff.Hunk("h1", "@@", "+x")])
    FakeClient.calls = []
    with patch.object(questions, "TypeSafeClient", FakeClient), patch("sys.stderr", io.StringIO()):
        ans = questions.ask_all([perms, readme, boom, moved])
    assert ans[2] is None and ans[0].flags["blast_radius"] == 0.75 and ans[0].confidence == 0.9
    assert ans[0].hunk_probs == {"h1": 0.3, "h2": 0.7} and ans[1].hunk_probs == {"h1": 1.0} and ans[3].hunk_probs == {}  # F-10
    state, qs = FakeClient.calls[0]
    assert "title" not in state and [h["id"] for h in state["hunks"]] == ["h1", "h2"]  # diff only, no context
    assert set(qs) == {*questions.NOUL, "blast_radius", "riskiest_hunk"} and "riskiest_hunk" not in FakeClient.calls[1][1]
    assert FakeClient.calls[3][0]["old_path"] == "old.py" and FakeClient.calls[3][0]["hunks"] == []
    v = scoring.score(files, list(zip([perms, readme, boom], ans)), NONE, CFG)
    assert v.degraded and v.level == "red" and v.reviewers == ["@security"]
    assert v.reading_order == [("README.md", "h1", 0.15), ("src/auth/permissions.ts", "h2", 0.105)]  # F-18, h1 of perms < 0.1


def test_load_env():
    d = Path(tempfile.mkdtemp())
    (d / ".env").write_text("# c\nexport WINCE_T_A='from-file'\nWINCE_T_B=\"x=y\"\n")
    with patch.dict(os.environ, {"WINCE_T_A": "from-env"}, clear=False):
        os.environ.pop("WINCE_T_B", None)
        config.load_env(d)
        assert os.environ["WINCE_T_A"] == "from-env" and os.environ["WINCE_T_B"] == "x=y"  # env wins, .env fills


def test_quoted_paths_stay_excluded():  # P1: git-quoted "\320..." paths must still match exclude_from_api
    kept, sendable, skipped, _ = rules.partition(diff.parse(QUOTED), CFG)
    assert [f.path for f in sendable] == ["t\tab.txt"] and [f.path for f, _ in skipped] == ["ключ.pem", "my key.pem"]


def test_unevaluated_files_never_green():  # P1: excluded / binary files are not evidence of safety
    secret = one("secrets/token.txt")
    wasm = "diff --git a/app.wasm b/app.wasm\nindex 1..2 100644\nBinary files a/app.wasm and b/app.wasm differ\n"
    for text, why in ((secret, "excluded from API"), (wasm, "binary")):
        v = cli.evaluate(text, CFG, offline=False)  # nothing sendable -> no network
        assert v.level == "yellow" and v.degraded and v.confidence == 0.0 and any(why in r for r in v.reasons), (text, v)
        assert cli.evaluate(text, CFG, offline=True).level == "yellow"


def test_hunkless_files_are_evaluated():  # pure rename / mode flip: sent to the model, never a silent green
    for text in (rename("app/utils.py", "app/util.py", hunk=False),
                 "diff --git a/bin/deploy.sh b/bin/deploy.sh\nold mode 100755\nnew mode 100644\n"):
        assert [f.path for f in rules.partition(diff.parse(text), CFG)[1]]  # sendable
        v = cli.evaluate(text, CFG, offline=True)
        assert v.level == "yellow" and v.degraded, text
    FakeClient.calls = []
    with patch.object(questions, "TypeSafeClient", FakeClient):
        cli.evaluate("diff --git a/bin/deploy.sh b/bin/deploy.sh\nold mode 100755\nnew mode 100644\n", CFG, offline=False)
    assert FakeClient.calls[0][0]["mode_change"] == "100755 -> 100644"


def test_rename_keeps_source_path():  # P2: auth/check.py -> utils/check.py still trips the security rule
    f = diff.parse(rename("auth/check.py", "utils/check.py"))[0]
    assert f.status == "renamed" and f.paths == ("utils/check.py", "auth/check.py")
    assert rules.hard_flags([f], CFG)[0] == ["security"]
    assert rules.partition(diff.parse(rename("secrets/k", "cfg/k")), CFG)[2]  # excluded via the old path


def test_ignore_needs_every_path():  # a move out of (or into) gen/ is a real change; gen/ -> gen/ is not
    for src, dst in (("gen/check.py", "auth/check.py"), ("auth/check.py", "gen/check.py")):
        v = cli.evaluate(rename(src, dst), CFG, offline=True)
        assert v.hard_flags == ["security"] and v.level == "yellow", (src, dst)
    assert cli.evaluate(rename("gen/a.py", "gen/b.py"), CFG, offline=True).reasons == ["all 1 changed file(s) ignored"]
    assert [f.path for f in rules.partition(diff.parse(rename("tests/x.py", "app/x.py")), CFG)[1]] == ["app/x.py"]  # rules_only too


def test_rules_only_files():  # tests/docs: path rules + size, never the model, never block green
    v = cli.evaluate(one("docs/api.md", "-Returns an array.\n+Returns {items}.\n"), CFG, offline=False)
    assert v.level == "green" and not v.degraded and v.confidence == 1.0 and "1 file(s) rules-only" in v.reasons[0]
    assert cli.evaluate(one("tests/fixtures/seed.sql"), CFG, offline=True).hard_flags == ["dba"]  # path rules still apply
    assert cli.evaluate(one("secrets/notes.md"), CFG, offline=False).degraded  # exclude_from_api wins over rules_only


def test_bad_input_is_an_error():  # P2: not-a-diff or a cut-off diff must not become "no changes"
    for text in ("this is not a diff",
                 "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n",  # headers, no hunk
                 "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,3 +1,3 @@\n-a\n"):  # cut inside a hunk
        try:
            cli.evaluate(text, CFG, offline=True)
            raise AssertionError(f"expected ValueError for {text!r}")
        except ValueError:
            pass
    assert cli.evaluate("   \n", CFG, offline=True).level == "green"  # whitespace-only = no changes
    with patch("sys.stdin", io.StringIO("this is not a diff")), patch("sys.stderr", io.StringIO()):
        try:
            cli.main(["check", "--stdin", "--offline"])
            raise AssertionError("expected exit 2")
        except SystemExit as e:
            assert e.code == 2


def test_fork_pr_posting_fails_softly():  # P2: read-only GITHUB_TOKEN on fork PRs must not fail the job
    ev = {"pull_request": {"number": 7, "title": "t", "base": {"ref": "main"}, "head": {"repo": {"fork": True}}},
          "repository": {"full_name": "o/r"}}
    path = Path(tempfile.mkdtemp()) / "event.json"
    path.write_text(json.dumps(ev))
    posted = []

    def refuse(*a):
        posted.append(a)
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)

    with patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(path), "GITHUB_TOKEN": "t"}), \
         patch.object(inputs, "local", lambda target: GIT_DIFF), \
         patch.object(github, "upsert_comment", refuse), \
         patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()) as err:
        assert cli.main(["ci", "github"]) == 0 and len(posted) == 1  # offline (fork), tried once, job still green
        assert "could not post" in err.getvalue()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
