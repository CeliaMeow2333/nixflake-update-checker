"""Working with flake directories: snapshots, lock updates and evaluation."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Sequence

from . import nix
from .nix import NixError


class FlakeError(RuntimeError):
    """Raised for problems with the local flake directory."""


def _ignore(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name in (".git", ".hg", ".svn", ".direnv", "node_modules"):
            ignored.add(name)
        elif name == "result" or name.startswith("result-"):
            ignored.add(name)
        elif name.endswith((".qcow2", ".img")):
            ignored.add(name)
    return ignored


def validate_flake(source: Path) -> None:
    if not source.exists():
        raise FlakeError(f"Flake directory does not exist: {source}")
    if not source.is_dir():
        raise FlakeError(f"Not a directory: {source}")
    if not (source / "flake.nix").is_file():
        raise FlakeError(f"No flake.nix found in {source}")


def snapshot_flake(
    source: Path,
    destination: Path,
    *,
    allow_sudo: bool = False,
    on_sudo: Callable[[], None] | None = None,
) -> Path:
    """Copy a flake directory into a writable temporary location.

    The original directory is never modified: all lock updates happen on the
    copy. Most system flakes (``/etc/nixos``) are world readable, so no sudo is
    needed. If a file is not readable and ``allow_sudo`` is set, ``sudo`` is
    invoked and prompts for the password itself on the controlling terminal.
    """

    validate_flake(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source, destination, symlinks=True, ignore=_ignore)
    except PermissionError as error:
        if not allow_sudo:
            raise FlakeError(
                f"Cannot read {error.filename or source} (permission denied). "
                "Re-run with --sudo to copy the flake using sudo."
            ) from error
        if on_sudo:
            on_sudo()
        _copy_with_sudo(source, destination)

    _make_writable(destination)
    return destination


def _copy_with_sudo(source: Path, destination: Path) -> None:
    try:
        # sudo itself prompts on the terminal; the password never passes through
        # this program or its logs.
        subprocess.run(["sudo", "-v"], check=True)
        subprocess.run(["sudo", "cp", "-a", f"{source}/.", str(destination)], check=True)
        subprocess.run(
            ["sudo", "chown", "-R", f"{os.getuid()}:{os.getgid()}", str(destination)],
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise FlakeError(
            f"Could not copy the flake with sudo: {error}. "
            "Try copying it manually to a writable directory and pass that path."
        ) from error


def _make_writable(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        try:
            if path.is_symlink():
                continue
            mode = path.stat().st_mode
            # Preserve the executable bit, add owner write.
            path.chmod(mode | 0o200)
        except OSError:
            continue


def read_lock_exists(flake_dir: Path) -> bool:
    return (flake_dir / "flake.lock").is_file()


def update_lock(
    flake_dir: Path,
    inputs: Sequence[str] = (),
    *,
    timeout: int | None = nix.DEFAULT_TIMEOUT,
) -> str:
    try:
        return nix.flake_update(flake_dir, inputs, timeout=timeout)
    except NixError as error:
        raise FlakeError(
            "nix flake update failed:\n"
            f"{error.stderr.strip() or error.stdout.strip()}\n"
            f"Hint: {error.hint}"
        ) from error


def use_existing_lock(flake_dir: Path, lock_file: Path) -> None:
    if not lock_file.is_file():
        raise FlakeError(f"Lock file does not exist: {lock_file}")
    try:
        shutil.copyfile(lock_file, flake_dir / "flake.lock")
    except OSError as error:
        raise FlakeError(f"Could not copy {lock_file}: {error}") from error


def resolve_host(flake_dir: Path, requested: str | None) -> tuple[str, list[str]]:
    hosts = nix.detect_hosts(flake_dir)
    if requested:
        if hosts and requested not in hosts:
            raise FlakeError(
                f"Host '{requested}' not found. Available: {', '.join(sorted(hosts))}"
            )
        return requested, hosts
    if not hosts:
        raise FlakeError(
            "This flake does not expose any nixosConfigurations. "
            "Pass --host if the configuration uses a different attribute layout."
        )
    return hosts[0], hosts


def evaluate(flake_dir: Path, host: str) -> dict:
    try:
        return nix.evaluate_configuration(flake_dir, host)
    except NixError as error:
        raise FlakeError(
            f"Could not evaluate nixosConfigurations.{host}:\n"
            f"{error.stderr.strip() or error.stdout.strip()}\n"
            f"Hint: {error.hint}"
        ) from error
