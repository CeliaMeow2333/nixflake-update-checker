from __future__ import annotations

import json
from pathlib import Path

from nixflake_update_checker.packages import (
    diff_package_maps,
    explicit_pnames,
    extract_package,
    load_package_map,
)


def drv(
    name: str,
    *,
    pname: str | None = None,
    version: str | None = None,
    fixed_output: bool = False,
) -> dict:
    env: dict[str, str] = {"out": "/nix/store/..."}
    if pname:
        env["pname"] = pname
    if version:
        env["version"] = version
    outputs = {"out": {"hash": "sha256-abc", "method": "flat"}} if fixed_output else {"out": {"path": "/nix/store/..."}}
    return {"name": name, "env": env, "outputs": outputs}


def test_extract_prefers_env_metadata() -> None:
    entry = drv("python3.13-anyio-4.15.1", pname="anyio", version="4.15.1")
    assert extract_package(entry) == ("anyio", "4.15.1")


def test_extract_falls_back_to_name() -> None:
    assert extract_package(drv("git-2.56.0")) == ("git", "2.56.0")


def test_extract_skips_fixed_output() -> None:
    assert extract_package(drv("bison-3.8.2.tar.gz", fixed_output=True)) is None


def test_extract_skips_structural_derivations() -> None:
    assert extract_package(drv("etc")) is None
    assert extract_package(drv("system-path")) is None
    assert extract_package(drv("unit-dbus.service")) is None
    assert extract_package(drv("cargo-vendor-dir-1.0")) is None


def test_structured_attrs_are_supported() -> None:
    entry = {
        "name": "hello-2.12",
        "outputs": {"out": {}},
        "structuredAttrs": {"pname": "hello", "version": "2.12"},
    }
    assert extract_package(entry) == ("hello", "2.12")


def test_load_package_map_handles_both_formats(tmp_path: Path) -> None:
    flat = {"a.drv": drv("git-2.56.0"), "b.drv": drv("hello-2.12", pname="hello", version="2.12")}
    flat_path = tmp_path / "flat.json"
    flat_path.write_text(json.dumps(flat))
    assert load_package_map(flat_path) == {"git": frozenset({"2.56.0"}), "hello": frozenset({"2.12"})}

    wrapped_path = tmp_path / "wrapped.json"
    wrapped_path.write_text(json.dumps({"version": 3, "derivations": flat}))
    assert load_package_map(wrapped_path) == {"git": frozenset({"2.56.0"}), "hello": frozenset({"2.12"})}


def test_diff_classifies_changes() -> None:
    old = {
        "git": frozenset({"2.55.0"}),
        "anyio": frozenset({"4.14.2"}),
        "gone": frozenset({"1.0"}),
        "coexist": frozenset({"1.0"}),
        "weird": frozenset({"2.0"}),
    }
    new = {
        "git": frozenset({"2.56.0"}),
        "anyio": frozenset({"4.14.2", "4.15.1"}),
        "fresh": frozenset({"3.0"}),
        "coexist": frozenset({"1.0", "2.0"}),
        "weird": frozenset({"1.5"}),
    }
    diff = diff_package_maps(old, new, explicit_pnames={"git"})
    by_name = {change.pname: change for change in diff.changes}

    assert by_name["git"].kind == "upgraded"
    assert by_name["git"].explicit is True
    assert by_name["anyio"].kind == "upgraded"
    assert by_name["anyio"].new_versions == ("4.14.2", "4.15.1")
    assert by_name["gone"].kind == "removed"
    assert by_name["fresh"].kind == "added"
    assert by_name["coexist"].kind == "upgraded"
    assert by_name["weird"].kind == "downgraded"
    assert diff.changes[0].pname == "git"  # explicit entries come first


def test_diff_ignores_identical_maps() -> None:
    packages = {"git": frozenset({"2.56.0"})}
    assert diff_package_maps(packages, packages).changes == []


def test_explicit_pnames_parses_names_and_pnames() -> None:
    entries = [
        {"name": "nil-2026-07-23", "pname": "nil", "version": "2026-07-23"},
        {"name": "git-2.56.0", "pname": None, "version": None},
        {"name": "sing-box-for-desktop-configured", "pname": None, "version": None},
    ]
    names = explicit_pnames(entries)
    assert "nil" in names
    assert "git" in names
    assert "sing-box-for-desktop-configured" in names
