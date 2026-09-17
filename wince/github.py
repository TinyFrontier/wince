"""v0.2: publish the verdict as one PR comment, edited in place on re-runs (G-1)."""

import json
import os
import urllib.request
from pathlib import Path

from .core.render import MARKER


def event() -> dict:
    return json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))


def _api(method: str, url: str, token: str, body: "dict | None" = None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def upsert_comment(repo: str, number: int, body: str, token: str) -> None:
    base = f"{os.environ.get('GITHUB_API_URL', 'https://api.github.com')}/repos/{repo}/issues"
    for c in _api("GET", f"{base}/{number}/comments?per_page=100", token):
        if MARKER in (c.get("body") or ""):
            _api("PATCH", f"{base}/comments/{c['id']}", token, {"body": body})
            return
    _api("POST", f"{base}/{number}/comments", token, {"body": body})
