"""Parsing and diffing ``flake.lock`` files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import InputChange, LockedInput


class LockFileError(RuntimeError):
    pass


def read_lock(path: Path) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as error:
        raise LockFileError(f"Lock file not found: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise LockFileError(f"Cannot read {path}: {error}") from error
    if not isinstance(data, dict):
        raise LockFileError(f"Unexpected flake.lock structure in {path}")
    return data


def collect_inputs(lock: dict[str, Any]) -> dict[str, LockedInput]:
    """Return all locked nodes, skipping ``follows``-only nodes."""

    nodes = lock.get("nodes")
    if not isinstance(nodes, dict):
        raise LockFileError("flake.lock has no 'nodes' object")

    inputs: dict[str, LockedInput] = {}
    for name, node in nodes.items():
        if not isinstance(node, dict):
            continue
        locked = node.get("locked")
        if not isinstance(locked, dict):
            # Nodes that only contain "inputs" are 'follows' aliases.
            continue
        original = node.get("original")
        inputs[str(name)] = LockedInput(
            name=str(name),
            original=original if isinstance(original, dict) else {},
            locked=locked,
        )
    return inputs


def input_origins(lock: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Map a node name to ``(parent node, input alias)``.

    ``nixpkgs_2`` is not a very helpful name; with this map it can be shown as
    ``sfd-nix › nixpkgs`` instead.
    """

    nodes = lock.get("nodes")
    if not isinstance(nodes, dict):
        return {}

    origins: dict[str, tuple[str, str]] = {}
    for node_name, node in nodes.items():
        if not isinstance(node, dict):
            continue
        node_inputs = node.get("inputs")
        if not isinstance(node_inputs, dict):
            continue
        for alias, target in node_inputs.items():
            if isinstance(target, str):
                origins[target] = (str(node_name), str(alias))
            elif isinstance(target, list) and target:
                origins[str(target[-1])] = (str(node_name), str(alias))
    return origins


def diff_inputs(
    old: dict[str, LockedInput],
    new: dict[str, LockedInput],
) -> list[InputChange]:
    """Compare two input maps, returning every node with a status."""

    changes: list[InputChange] = []
    for name in sorted(set(old) | set(new)):
        changes.append(InputChange(name=name, old=old.get(name), new=new.get(name)))

    def sort_key(change: InputChange) -> tuple[int, str]:
        order = {"updated": 0, "added": 1, "removed": 2, "up-to-date": 3}
        return order.get(change.status, 4), change.name

    changes.sort(key=sort_key)
    return changes


def lock_revision_summary(lock: dict[str, Any]) -> dict[str, int]:
    """Small helper used in tests and diagnostics."""

    nodes = lock.get("nodes") or {}
    locked = sum(1 for node in nodes.values() if isinstance(node, dict) and node.get("locked"))
    return {"nodes": len(nodes), "locked": locked}
