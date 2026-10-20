"""Data structures shared across the checker."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEME_RE = re.compile(r"^(?:git\+|file\+|https?://|ssh://)+", re.IGNORECASE)


@dataclass(frozen=True)
class LockedInput:
    """A single locked flake input (one node in ``flake.lock``)."""

    name: str
    original: dict[str, Any]
    locked: dict[str, Any]

    @property
    def type(self) -> str:
        return str(self.locked.get("type") or self.original.get("type") or "unknown")

    @property
    def rev(self) -> str | None:
        rev = self.locked.get("rev")
        return str(rev) if rev else None

    @property
    def short_rev(self) -> str:
        return (self.rev or "—")[:8]

    @property
    def nar_hash(self) -> str | None:
        value = self.locked.get("narHash")
        return str(value) if value else None

    @property
    def last_modified(self) -> int | None:
        value = self.locked.get("lastModified")
        return int(value) if isinstance(value, int) else None

    @property
    def date(self) -> str | None:
        timestamp = self.last_modified
        if timestamp is None:
            return None
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")

    @property
    def ref(self) -> str | None:
        value = self.original.get("ref") or self.locked.get("ref")
        return str(value) if value else None

    @property
    def label(self) -> str:
        """Human readable source label, e.g. ``NixOS/nixpkgs``."""
        if self.type == "github":
            owner = self.original.get("owner") or self.locked.get("owner")
            repo = self.original.get("repo") or self.locked.get("repo")
            if owner and repo:
                return f"{owner}/{repo}"

        url = self.original.get("url") or self.locked.get("url")
        if url:
            text = _SCHEME_RE.sub("", str(url)).rstrip("/")
            if text.endswith(".git"):
                text = text[:-4]
            return text

        path = self.original.get("path") or self.locked.get("path")
        if path:
            return str(path)
        return self.type

    @property
    def github_repo(self) -> tuple[str, str] | None:
        if self.type != "github":
            return None
        owner = self.original.get("owner") or self.locked.get("owner")
        repo = self.original.get("repo") or self.locked.get("repo")
        if owner and repo:
            return str(owner), str(repo)
        return None


@dataclass
class InputChange:
    """Difference for one input between the local and the updated lock file."""

    name: str
    old: LockedInput | None
    new: LockedInput | None
    commits: int | None = None
    parent: str | None = None
    alias: str | None = None

    @property
    def display_name(self) -> str:
        if self.parent:
            return f"{self.parent} › {self.alias or self.name}"
        return self.name

    @property
    def status(self) -> str:
        if self.old is None:
            return "added"
        if self.new is None:
            return "removed"
        if self.old.rev or self.new.rev:
            return "updated" if self.old.rev != self.new.rev else "up-to-date"
        return "updated" if self.old.nar_hash != self.new.nar_hash else "up-to-date"

    @property
    def changed(self) -> bool:
        return self.status != "up-to-date"


@dataclass(frozen=True)
class PackageChange:
    """A package whose version set changed between the two closures."""

    pname: str
    old_versions: tuple[str, ...]
    new_versions: tuple[str, ...]
    kind: str
    explicit: bool = False

    @property
    def old_display(self) -> str:
        return ", ".join(self.old_versions) or "—"

    @property
    def new_display(self) -> str:
        return ", ".join(self.new_versions) or "—"


@dataclass
class PackageDiff:
    """Result of comparing two derivation closures."""

    changes: list[PackageChange] = field(default_factory=list)
    old_total: int = 0
    new_total: int = 0

    def count(self, kind: str) -> int:
        return sum(1 for change in self.changes if change.kind == kind)

    @property
    def explicit_changes(self) -> list[PackageChange]:
        return [change for change in self.changes if change.explicit]

    @property
    def other_changes(self) -> list[PackageChange]:
        return [change for change in self.changes if not change.explicit]

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)


@dataclass
class DownloadEstimate:
    """What an update would download/build, from ``nix build --dry-run``."""

    fetch_paths: int = 0
    download_bytes: int = 0
    unpacked_bytes: int = 0
    build_derivations: int = 0
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.error is None

    @property
    def has_work(self) -> bool:
        return self.fetch_paths > 0 or self.build_derivations > 0


@dataclass
class Report:
    """Everything the renderer needs."""

    flake_path: Path
    host: str
    old_label: str | None = None
    new_label: str | None = None
    inputs: list[InputChange] = field(default_factory=list)
    packages: PackageDiff | None = None
    download: DownloadEstimate | None = None
    warnings: list[str] = field(default_factory=list)
    duration: float = 0.0
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def changed_inputs(self) -> list[InputChange]:
        return [item for item in self.inputs if item.changed]

    @property
    def has_updates(self) -> bool:
        if self.changed_inputs:
            return True
        return bool(self.packages and self.packages.has_changes)

    def to_dict(self) -> dict[str, Any]:
        def input_dict(item: InputChange) -> dict[str, Any]:
            return {
                "name": item.name,
                "display_name": item.display_name,
                "parent": item.parent,
                "status": item.status,
                "type": (item.new or item.old).type if (item.new or item.old) else "unknown",
                "repository": (item.new or item.old).label if (item.new or item.old) else "",
                "old_rev": item.old.rev if item.old else None,
                "new_rev": item.new.rev if item.new else None,
                "old_date": item.old.date if item.old else None,
                "new_date": item.new.date if item.new else None,
                "commits": item.commits,
            }

        def package_dict(change: PackageChange) -> dict[str, Any]:
            return {
                "name": change.pname,
                "old_versions": list(change.old_versions),
                "new_versions": list(change.new_versions),
                "change": change.kind,
                "explicit": change.explicit,
            }

        packages: dict[str, Any] | None = None
        if self.packages is not None:
            packages = {
                "summary": {
                    "changed": len(self.packages.changes),
                    "upgraded": self.packages.count("upgraded"),
                    "downgraded": self.packages.count("downgraded"),
                    "added": self.packages.count("added"),
                    "removed": self.packages.count("removed"),
                    "changed_version": self.packages.count("changed"),
                    "explicit": len(self.packages.explicit_changes),
                    "old_total": self.packages.old_total,
                    "new_total": self.packages.new_total,
                },
                "explicit": [package_dict(item) for item in self.packages.explicit_changes],
                "other": [package_dict(item) for item in self.packages.other_changes],
            }

        return {
            "generated_at": self.generated_at.isoformat(),
            "flake": str(self.flake_path),
            "host": self.host,
            "nixos_label": {"old": self.old_label, "new": self.new_label},
            "has_updates": self.has_updates,
            "duration_seconds": round(self.duration, 2),
            "inputs": [input_dict(item) for item in self.inputs],
            "packages": packages,
            "download": None
            if self.download is None
            else {
                "available": self.download.available,
                "error": self.download.error,
                "fetch_paths": self.download.fetch_paths,
                "download_bytes": self.download.download_bytes,
                "unpacked_bytes": self.download.unpacked_bytes,
                "build_derivations": self.download.build_derivations,
            },
            "warnings": list(self.warnings),
        }
