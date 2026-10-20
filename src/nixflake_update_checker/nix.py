"""Thin wrappers around the Nix CLI.

Only read-only / lock-file operations are used; nothing is ever built or
substituted. The user's real flake directory is never modified because every
command runs against a temporary copy.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Sequence

EXPERIMENTAL_ARGS = ("--extra-experimental-features", "nix-command flakes")
DEFAULT_TIMEOUT = 1800

#: Everything we need from one evaluation of a NixOS configuration.
CONFIG_EXPRESSION = """
cfg: {
  label = cfg.config.system.nixos.label or null;
  drvPath = cfg.config.system.build.toplevel.drvPath;
  packages = map (p: {
    name = p.name or "";
    pname = p.pname or null;
    version = p.version or null;
  }) cfg.config.environment.systemPackages;
}
""".strip()


class NixError(RuntimeError):
    def __init__(
        self,
        command: Sequence[str],
        returncode: int,
        stderr: str = "",
        stdout: str = "",
    ) -> None:
        self.command = list(command)
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout
        message = stderr.strip() or stdout.strip() or f"exit status {returncode}"
        super().__init__(f"`{' '.join(self.command)}` failed: {message}")

    @property
    def hint(self) -> str:
        last = self.last_line
        if "trusted" in last or "not a trusted user" in last:
            return "Add yourself to nix.settings.trusted-users or use a different substituter."
        if any(word in last for word in ("Could not resolve", "network", "unable to download", "TLS")):
            return "Check network access, or pass --new-lock FILE to compare without fetching upstream."
        if "does not provide attribute" in last or ("attribute" in last and "missing" in last):
            return "Check the flake exposes nixosConfigurations.<host> and pass --host explicitly if needed."
        return "Re-run with --verbose to see the full Nix output."

    @property
    def last_line(self) -> str:
        for line in reversed(self.stderr.strip().splitlines()):
            stripped = line.strip()
            if stripped:
                return stripped
        return "unknown error"


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
    stdout_file: Path | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if stdout_file is not None:
        with open(stdout_file, "w", encoding="utf-8") as handle:
            process = subprocess.run(
                list(command),
                cwd=str(cwd) if cwd else None,
                stdout=handle,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                env=env,
            )
        stdout = ""
    else:
        process = subprocess.run(
            list(command),
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        stdout = process.stdout or ""

    if check and process.returncode != 0:
        raise NixError(command, process.returncode, process.stderr or "", stdout)
    return process


def nix(*args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return _run(["nix", *EXPERIMENTAL_ARGS, *args], **kwargs)


def flake_ref(flake_dir: Path) -> str:
    """Build a ``path:`` flake reference for a local directory."""

    return "path:" + str(Path(flake_dir).resolve())


def eval_json(
    flake_dir: Path,
    attr: str | None = None,
    *,
    apply: str | None = None,
    timeout: int | None = DEFAULT_TIMEOUT,
) -> Any:
    reference = flake_ref(flake_dir)
    if attr:
        reference = f"{reference}#{attr}"
    args = ["eval", "--json", reference]
    if apply:
        args.extend(["--apply", apply])
    process = nix(*args, timeout=timeout)
    return json.loads(process.stdout)


def eval_raw(
    flake_dir: Path,
    attr: str,
    *,
    timeout: int | None = DEFAULT_TIMEOUT,
) -> str:
    process = nix("eval", "--raw", f"{flake_ref(flake_dir)}#{attr}", timeout=timeout)
    return process.stdout.strip()


def detect_hosts(flake_dir: Path, *, timeout: int | None = 300) -> list[str]:
    try:
        names = eval_json(
            flake_dir,
            "nixosConfigurations",
            apply="builtins.attrNames",
            timeout=timeout,
        )
    except (NixError, json.JSONDecodeError):
        return []
    return [str(name) for name in names] if isinstance(names, list) else []


def evaluate_configuration(flake_dir: Path, host: str) -> dict[str, Any]:
    """Return label, toplevel derivation and explicit packages for ``host``."""

    data = eval_json(
        flake_dir,
        f"nixosConfigurations.{host}",
        apply=CONFIG_EXPRESSION,
    )
    if not isinstance(data, dict):
        raise NixError(["nix", "eval"], 0, "unexpected evaluation result")
    return data


def flake_update(
    flake_dir: Path,
    inputs: Sequence[str] = (),
    *,
    timeout: int | None = DEFAULT_TIMEOUT,
) -> str:
    """Run ``nix flake update`` inside ``flake_dir`` (a temporary copy)."""

    process = nix(
        "flake",
        "update",
        *inputs,
        cwd=flake_dir,
        timeout=timeout,
        check=False,
    )
    if process.returncode != 0:
        raise NixError(
            ["nix", *EXPERIMENTAL_ARGS, "flake", "update", *inputs],
            process.returncode,
            process.stderr or "",
            process.stdout or "",
        )
    return (process.stderr or "") + (process.stdout or "")


def derivation_show(
    drv_path: str,
    output_file: Path,
    *,
    timeout: int | None = DEFAULT_TIMEOUT,
) -> None:
    """Write the full derivation closure of ``drv_path`` as JSON."""

    process = nix(
        "derivation",
        "show",
        "-r",
        drv_path,
        stdout_file=output_file,
        timeout=timeout,
        check=False,
    )
    if process.returncode != 0:
        raise NixError(
            ["nix", *EXPERIMENTAL_ARGS, "derivation", "show", "-r", drv_path],
            process.returncode,
            process.stderr or "",
        )


def version(*, timeout: int | None = 60) -> str:
    try:
        process = _run(["nix", "--version"], timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (process.stdout or "").strip()
