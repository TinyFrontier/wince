"""Unified diff -> files and hunks."""

import re
from dataclasses import dataclass, field

_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
# a/x b/x, or git-quoted "a/\320\272.pem" "b/\320\272.pem" (core.quotePath, spaces, control chars)
_GIT = re.compile(r'^diff --git (?:"a/((?:[^"\\]|\\.)*)"|a/(.*?)) (?:"b/((?:[^"\\]|\\.)*)"|b/(.*))$')
_QUOTED = re.compile(r'^"((?:[^"\\]|\\.)*)"')
_ESC = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v", "\\": "\\", '"': '"'}


@dataclass
class Hunk:
    id: str  # h1, h2...
    header: str
    body: str


@dataclass
class FileChange:
    path: str
    status: str  # added | modified | deleted | renamed
    hunks: list[Hunk] = field(default_factory=list)
    binary: bool = False
    old_path: "str | None" = None  # source of a rename/copy; rules check it too
    mode: "str | None" = None  # "100755 -> 100644" when only the mode changed
    truncated: bool = False  # ---/+++ headers without a hunk, or input cut inside a hunk

    @property
    def paths(self) -> tuple[str, ...]:
        return (self.path,) if not self.old_path or self.old_path == self.path else (self.path, self.old_path)

    @property
    def changed_lines(self) -> int:
        return sum(1 for h in self.hunks for line in h.body.splitlines() if line[:1] in "+-")


def _unescape(s: str) -> str:
    """Inside of a git-quoted path: \\ooo octal UTF-8 bytes and C escapes."""
    out, i = bytearray(), 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            m = re.match(r"[0-7]{1,3}", s[i + 1:i + 4])
            if m:
                out.append(int(m[0], 8))
                i += 1 + len(m[0])
            else:
                out += _ESC.get(s[i + 1], s[i + 1]).encode()
                i += 2
        else:
            out += s[i].encode()
            i += 1
    return out.decode("utf-8", "replace")


def _path(raw: str, prefix: bool = True) -> str:
    """Path from a ---/+++/rename line: git-quoted or bare (tab-separated timestamp dropped)."""
    m = _QUOTED.match(raw)
    raw = _unescape(m[1]) if m else raw.split("\t", 1)[0]
    return raw[2:] if prefix and raw[:2] in ("a/", "b/") else raw


def parse(text: str) -> list[FileChange]:
    files: list[FileChange] = []
    cur: FileChange | None = None
    hunk: Hunk | None = None
    body: list[str] = []
    old = new = 0  # lines still expected in the open hunk (from the @@ header)

    for line in text.splitlines():
        if hunk is not None:
            c = line[:1]
            if c in (" ", ""):  # "" = context line with stripped whitespace
                old -= 1
                new -= 1
            elif c == "-":
                old -= 1
            elif c == "+":
                new -= 1
            elif c != "\\":  # a header inside a hunk: the @@ counts lied, close and reprocess
                hunk.body = "\n".join(body)
                hunk, body = None, []
            if hunk is not None:
                body.append(line)
                if old <= 0 and new <= 0:
                    hunk.body = "\n".join(body)
                    hunk, body = None, []
                continue
        m = _GIT.match(line)
        if m:
            a = _unescape(m[1]) if m[1] is not None else m[2]
            b = _unescape(m[3]) if m[3] is not None else m[4]
            cur = FileChange(b, "modified", old_path=a)
            files.append(cur)
        elif line.startswith("--- "):
            if cur is None or cur.hunks:  # plain diff without "diff --git": "---" opens a file
                cur = FileChange(_path(line[4:]), "modified")
                files.append(cur)
            if line[4:].startswith("/dev/null"):
                cur.status = "added"
            else:
                cur.old_path = _path(line[4:])
        elif cur is None:
            continue
        elif line.startswith("+++ "):
            cur.truncated = True  # git always follows +++ with a hunk; cleared when one arrives
            if line[4:].startswith("/dev/null"):
                cur.status = "deleted"
            else:
                cur.path = _path(line[4:])
        elif line.startswith("old mode "):
            cur.mode = line[9:]
        elif line.startswith("new mode "):
            cur.mode = f"{cur.mode} -> {line[9:]}"
        elif line.startswith("new file mode"):
            cur.status = "added"
        elif line.startswith("deleted file mode"):
            cur.status = "deleted"
        elif line.startswith("rename from "):
            cur.old_path = _path(line[12:], prefix=False)
        elif line.startswith("rename to "):
            cur.status, cur.path = "renamed", _path(line[10:], prefix=False)
        elif line.startswith(("Binary files", "GIT binary patch")):
            cur.binary = True
        elif line.startswith("@@"):
            m = _HUNK.match(line)
            if m:
                old = int(m[1]) if m[1] is not None else 1
                new = int(m[2]) if m[2] is not None else 1
                hunk = Hunk(f"h{len(cur.hunks) + 1}", line, "")
                cur.hunks.append(hunk)
                cur.truncated = False
    if hunk is not None:  # input ended inside a hunk
        hunk.body = "\n".join(body)
        cur.truncated = old > 0 or new > 0
    return files
