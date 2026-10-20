"""Look up how far behind a GitHub input is (commit distance).

This is purely cosmetic information for the report: the authoritative "new"
revision always comes from the freshly generated lock file. Network failures
degrade gracefully to ``None``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

from .models import InputChange

API_ROOT = "https://api.github.com"


def github_compare(
    owner: str,
    repo: str,
    base_rev: str,
    head_rev: str,
    *,
    timeout: float = 15.0,
    token: str | None = None,
) -> int | None:
    """Return the number of commits ``head_rev`` is ahead of ``base_rev``."""

    url = f"{API_ROOT}/repos/{owner}/{repo}/compare/{base_rev}...{head_rev}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "nixflake-update-checker",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None

    ahead = payload.get("ahead_by") if isinstance(payload, dict) else None
    return int(ahead) if isinstance(ahead, int) else None


def _lookup(change: InputChange, token: str | None, timeout: float) -> int | None:
    if change.status != "updated" or change.old is None or change.new is None:
        return None
    repo = change.old.github_repo
    if not repo or not change.old.rev or not change.new.rev:
        return None
    return github_compare(
        repo[0],
        repo[1],
        change.old.rev,
        change.new.rev,
        timeout=timeout,
        token=token,
    )


def attach_commit_counts(
    changes: Iterable[InputChange],
    *,
    token: str | None = None,
    timeout: float = 15.0,
    max_workers: int = 4,
) -> None:
    """Fill ``InputChange.commits`` for GitHub inputs, in parallel."""

    if token is None:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

    candidates = [change for change in changes if change.status == "updated"]
    if not candidates:
        return

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            id(change): pool.submit(_lookup, change, token, timeout)
            for change in candidates
        }
        for change in candidates:
            try:
                change.commits = futures[id(change)].result()
            except Exception:  # noqa: BLE001 - purely informational
                change.commits = None
