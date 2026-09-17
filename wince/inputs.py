"""Where the diff comes from: git branch, stdin, file."""

import subprocess
import sys
from pathlib import Path


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def local(target: str) -> str:
    """merge-base(HEAD, origin/<target>)...HEAD with -U5 --find-renames (F-1)."""
    ref = f"origin/{target}"
    if subprocess.run(["git", "rev-parse", "--verify", "-q", ref], capture_output=True).returncode != 0:
        ref = target
    return _git("diff", "--unified=5", "--find-renames", f"{ref}...HEAD")


def stdin() -> str:
    return sys.stdin.read()


def file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")
