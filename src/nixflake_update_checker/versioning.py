"""Nix version helpers and derivation-name parsing."""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"\d+|[A-Za-z]+")
_NAME_VERSION_RE = re.compile(r"^(?P<name>.+?)-(?P<version>\d[^-]*(?:[-.][^-]*)*)$")
_PYTHON_PREFIX_RE = re.compile(r"^python\d+(?:\.\d+)?-(?P<rest>.+)$")


def compare_versions(left: str, right: str) -> int:
    """Best-effort Nix-style version comparison.

    Returns -1, 0 or 1. This mirrors ``builtins.compareVersions`` closely enough
    for reporting purposes (numeric components compare numerically, alphabetic
    components compare lexically, extra components make a version newer).
    """

    left_tokens = _TOKEN_RE.findall(left)
    right_tokens = _TOKEN_RE.findall(right)

    for a, b in zip(left_tokens, right_tokens):
        if a.isdigit() and b.isdigit():
            if int(a) != int(b):
                return -1 if int(a) < int(b) else 1
        elif a != b:
            return -1 if a < b else 1

    if len(left_tokens) == len(right_tokens):
        return 0
    return -1 if len(left_tokens) < len(right_tokens) else 1


def split_name_version(name: str) -> tuple[str, str] | None:
    """Split a derivation name such as ``git-2.56.0`` into name and version."""

    match = _NAME_VERSION_RE.match(name)
    if not match:
        return None
    return match.group("name"), match.group("version")


def normalize_pname(pname: str) -> str:
    """Drop interpreter prefixes so ``python3.13-anyio`` and ``anyio`` match."""

    match = _PYTHON_PREFIX_RE.match(pname)
    return match.group("rest") if match else pname
