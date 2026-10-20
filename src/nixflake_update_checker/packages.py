"""Extract package names/versions from a derivation closure and diff them.

``nix derivation show -r <system.drv>`` lists every derivation reachable from
the NixOS system derivation. That includes build-time dependencies, but it is
the only way to learn package versions without building or downloading the
system. Fixed-output derivations (source tarballs, patches, vendored crates)
are filtered out, as are obviously structural derivations such as ``etc`` or
``system-path``.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from functools import cmp_to_key
from pathlib import Path
from typing import Any, Iterable, Mapping

from .models import PackageChange, PackageDiff
from .versioning import compare_versions, normalize_pname, split_name_version

_NOISE_EXACT = {
    "etc",
    "system-path",
    "activation-script",
    "dbus-1",
    "user-units",
    "user-environment",
    "nixos-system",
    "specialisation",
    "system-generators",
    "nixos-activation",
    "nixos-version",
    "source",
}

_NOISE_PREFIXES = (
    "unit-",
    "x-restart-triggers-",
    "x-reload-triggers-",
    "__",
    "nixos-system-",
    "user-units-",
    "system-path-",
)

_NOISE_SUFFIXES = (
    "-get-version",
    "-source",
    "-src",
    "-vendor",
    "-vendor-dir",
    "-binlore",
    "-patched",
    "-builder",
    "-update-script",
    "-simple-program",
)

_NOISE_EXTENSIONS = (
    ".patch",
    ".diff",
    ".sh",
    ".bash",
    ".pl",
    ".py",
    ".fish",
    ".conf",
    ".txt",
    ".json",
    ".lock",
    ".a",
    ".so",
    ".crt",
    ".pem",
    ".pc",
    ".service",
    ".timer",
    ".rules",
    ".desktop",
    ".xml",
    ".html",
    ".htm",
    ".hs",
    ".el",
    ".vim",
    ".lua",
    ".rb",
    ".jar",
    ".whl",
    ".egg",
)

_KIND_ORDER = {
    "downgraded": 0,
    "removed": 1,
    "upgraded": 2,
    "changed": 3,
    "added": 4,
}

_WHITESPACE_RE = re.compile(r"\s+")
_PYTHON_PREFIX_RE = re.compile(r"^python\d+(?:\.\d+)?-")


def _is_noise(pname: str, derivation_name: str) -> bool:
    lowered = pname.lower()
    if lowered in _NOISE_EXACT:
        return True
    if lowered.startswith(_NOISE_PREFIXES):
        return True
    if lowered.endswith(_NOISE_SUFFIXES):
        return True
    name_lower = derivation_name.lower()
    return name_lower.endswith(_NOISE_EXTENSIONS)


def _is_fixed_output(drv: Mapping[str, Any]) -> bool:
    outputs = drv.get("outputs")
    if not isinstance(outputs, dict):
        return False
    return any(isinstance(output, dict) and output.get("hash") for output in outputs.values())


def _strings(mapping: Any) -> Mapping[str, str]:
    if not isinstance(mapping, dict):
        return {}
    return {
        str(key): value
        for key, value in mapping.items()
        if isinstance(value, str) and value
    }


def extract_package(drv: Mapping[str, Any]) -> tuple[str, str] | None:
    """Return ``(pname, version)`` for a derivation, or ``None`` if noise."""

    if _is_fixed_output(drv):
        return None

    name = drv.get("name")
    name = str(name) if isinstance(name, str) else ""

    env = _strings(drv.get("env"))
    structured = _strings(drv.get("structuredAttrs"))

    pname = env.get("pname") or structured.get("pname")
    version = env.get("version") or structured.get("version")

    if not pname or not version:
        parsed = split_name_version(name)
        if parsed:
            pname = pname or parsed[0]
            version = version or parsed[1]

    if not pname or not version:
        return None

    pname = normalize_pname(pname)
    version = _WHITESPACE_RE.sub("", version)

    if not version or len(version) > 64:
        return None
    if "/" in version or _is_noise(pname, name):
        return None
    return pname, version


def _reduce_derivation(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``json`` hook that keeps only the fields needed for version extraction.

    A full ``nix derivation show -r`` dump is tens of megabytes; keeping every
    derivation's environment and inputs in memory would cost around a gigabyte.
    The hook runs while the JSON is decoded, so the large nested objects are
    discarded immediately.
    """

    obj = dict(pairs)
    # Real derivations always have "inputs"; ``structuredAttrs`` objects also
    # contain "name" and "outputs", so "inputs" is what tells them apart.
    if not ({"name", "outputs", "inputs"} <= obj.keys()):
        return obj

    env = obj.get("env") if isinstance(obj.get("env"), dict) else {}
    structured = (
        obj.get("structuredAttrs") if isinstance(obj.get("structuredAttrs"), dict) else {}
    )
    outputs = obj.get("outputs") if isinstance(obj.get("outputs"), dict) else {}
    fixed_output = any(
        isinstance(output, dict) and output.get("hash") for output in outputs.values()
    )

    return {
        "name": obj.get("name") or "",
        "env": {key: env[key] for key in ("pname", "version") if key in env},
        "structuredAttrs": {
            key: structured[key] for key in ("pname", "version") if key in structured
        },
        "outputs": {"out": {"hash": True}} if fixed_output else {"out": {}},
    }


def load_package_map(json_path: Path) -> dict[str, frozenset[str]]:
    """Read a ``nix derivation show -r`` dump as ``{pname: {versions}}``."""

    with open(json_path, encoding="utf-8") as handle:
        data = json.load(handle, object_pairs_hook=_reduce_derivation)

    if isinstance(data, dict) and "derivations" in data:
        derivations = data["derivations"]
    else:  # older Nix versions use the flat format
        derivations = data

    if not isinstance(derivations, dict):
        raise ValueError(f"Unexpected derivation dump structure in {json_path}")

    packages: dict[str, set[str]] = defaultdict(set)
    for drv in derivations.values():
        if not isinstance(drv, dict):
            continue
        entry = extract_package(drv)
        if entry is None:
            continue
        packages[entry[0]].add(entry[1])

    return {pname: frozenset(versions) for pname, versions in packages.items()}


def _max_version(versions: Iterable[str]) -> str | None:
    values = list(versions)
    if not values:
        return None
    return max(values, key=cmp_to_key(compare_versions))


def _classify(old: frozenset[str], new: frozenset[str]) -> str:
    added = new - old
    removed = old - new

    if added and not removed:
        return "upgraded"
    if removed and not added:
        old_max, new_max = _max_version(old), _max_version(new)
        if old_max and new_max and compare_versions(new_max, old_max) < 0:
            return "downgraded"
        return "changed"

    old_max, new_max = _max_version(old), _max_version(new)
    if old_max and new_max:
        comparison = compare_versions(new_max, old_max)
        if comparison > 0:
            return "upgraded"
        if comparison < 0:
            return "downgraded"
    return "changed"


def diff_package_maps(
    old: Mapping[str, frozenset[str]],
    new: Mapping[str, frozenset[str]],
    explicit_pnames: Iterable[str] = (),
) -> PackageDiff:
    """Compare two package maps and return a sorted list of changes."""

    explicit = set(explicit_pnames)
    changes: list[PackageChange] = []

    for pname in sorted(set(old) | set(new)):
        old_versions = old.get(pname)
        new_versions = new.get(pname)
        if old_versions == new_versions:
            continue

        if old_versions is None:
            kind = "added"
        elif new_versions is None:
            kind = "removed"
        else:
            kind = _classify(old_versions, new_versions)

        changes.append(
            PackageChange(
                pname=pname,
                old_versions=tuple(sorted(old_versions or ())),
                new_versions=tuple(sorted(new_versions or ())),
                kind=kind,
                explicit=pname in explicit,
            )
        )

    changes.sort(
        key=lambda change: (
            not change.explicit,
            _KIND_ORDER.get(change.kind, 9),
            change.pname,
        )
    )
    return PackageDiff(changes=changes, old_total=len(old), new_total=len(new))


def explicit_pnames(entries: Iterable[Mapping[str, Any]]) -> set[str]:
    """Derive package names from ``config.environment.systemPackages``."""

    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        pname = entry.get("pname")
        if isinstance(pname, str) and pname:
            names.add(normalize_pname(pname))
            continue
        name = entry.get("name")
        if isinstance(name, str) and name:
            parsed = split_name_version(name)
            names.add(normalize_pname(parsed[0] if parsed else name))
    return names


def looks_like_python(pname: str) -> bool:
    """Kept for backwards compatibility with earlier report code."""

    return bool(_PYTHON_PREFIX_RE.match(pname))
