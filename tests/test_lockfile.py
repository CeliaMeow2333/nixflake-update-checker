from __future__ import annotations

import json
from pathlib import Path

from nixflake_update_checker.lockfile import (
    collect_inputs,
    diff_inputs,
    input_origins,
    read_lock,
)


def node(*, rev: str, last_modified: int = 100, owner: str = "NixOS", repo: str = "nixpkgs") -> dict:
    return {
        "locked": {
            "lastModified": last_modified,
            "narHash": f"sha256-{rev}",
            "owner": owner,
            "repo": repo,
            "rev": rev,
            "type": "github",
        },
        "original": {
            "owner": owner,
            "ref": "nixos-unstable",
            "repo": repo,
            "type": "github",
        },
    }


def test_collect_inputs_skips_follows_nodes() -> None:
    lock = {
        "nodes": {
            "nixpkgs": node(rev="aaa"),
            "follows": {"inputs": {"nixpkgs": ["nixpkgs"]}},
        },
        "root": "root",
        "version": 7,
    }
    inputs = collect_inputs(lock)
    assert set(inputs) == {"nixpkgs"}
    assert inputs["nixpkgs"].rev == "aaa"
    assert inputs["nixpkgs"].label == "NixOS/nixpkgs"


def test_diff_inputs_statuses() -> None:
    old = collect_inputs({"nodes": {"nixpkgs": node(rev="aaa"), "old": node(rev="ddd", repo="old")}, "root": "root"})
    new = collect_inputs({"nodes": {"nixpkgs": node(rev="bbb", last_modified=200), "new": node(rev="ccc", repo="new")}, "root": "root"})

    changes = {change.name: change for change in diff_inputs(old, new)}
    assert changes["nixpkgs"].status == "updated"
    assert changes["nixpkgs"].old.short_rev == "aaa"
    assert changes["nixpkgs"].new.date is not None
    assert changes["old"].status == "removed"
    assert changes["new"].status == "added"


def test_read_lock_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "flake.lock"
    path.write_text(json.dumps({"nodes": {"nixpkgs": node(rev="aaa")}, "root": "root", "version": 7}))
    data = read_lock(path)
    assert data["root"] == "root"


def test_input_origins_finds_nested_inputs() -> None:
    lock = {
        "nodes": {
            "root": {"inputs": {"nixpkgs": "nixpkgs", "sfd-nix": "sfd-nix"}},
            "sfd-nix": {"inputs": {"nixpkgs": "nixpkgs_2"}, "locked": {"type": "git", "rev": "bbb"}},
            "nixpkgs": node(rev="aaa"),
            "nixpkgs_2": node(rev="ccc"),
        },
        "root": "root",
    }
    origins = input_origins(lock)
    assert origins["nixpkgs_2"] == ("sfd-nix", "nixpkgs")
    assert origins["nixpkgs"] == ("root", "nixpkgs")
