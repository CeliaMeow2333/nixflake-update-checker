"""Estimate how much an update would download.

``nix build --dry-run`` resolves the new system, asks the configured
substituters what is available, and prints::

    these 408 derivations will be built:
      ...
    these 1821 paths will be fetched (9.0 GiB download, 23.5 GiB unpacked):
      ...

This is the closest thing to an authoritative answer, and it does not download
or build anything.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Final

from . import nix
from .models import DownloadEstimate

_C_LOCALE_ENV: Final = {**os.environ, "LC_ALL": "C", "LANG": "C"}

_SIZE_PATTERN = r"([\d.]+)\s*([KMGTP]?i?B)"
_FETCH_RE = re.compile(
    rf"these\s+(\d+)\s+paths will be fetched\s*\(\s*{_SIZE_PATTERN}\s+download,\s*"
    rf"{_SIZE_PATTERN}\s+unpacked\s*\)"
)
_BUILD_RE = re.compile(r"these\s+(\d+)\s+derivations will be built")

_UNITS: Final = {
    "B": 1,
    "KiB": 1024,
    "MiB": 1024**2,
    "GiB": 1024**3,
    "TiB": 1024**4,
    "PiB": 1024**5,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
}


def _to_bytes(value: str, unit: str) -> int:
    return int(float(value) * _UNITS[unit])


def parse_estimate(output: str) -> DownloadEstimate:
    """Parse the human readable output of ``nix build --dry-run``."""

    estimate = DownloadEstimate()

    fetch_match = _FETCH_RE.search(output)
    if fetch_match:
        estimate.fetch_paths = int(fetch_match.group(1))
        estimate.download_bytes = _to_bytes(fetch_match.group(2), fetch_match.group(3))
        estimate.unpacked_bytes = _to_bytes(fetch_match.group(4), fetch_match.group(5))

    build_match = _BUILD_RE.search(output)
    if build_match:
        estimate.build_derivations = int(build_match.group(1))

    return estimate


def _last_error_line(output: str) -> str:
    for line in reversed(output.strip().splitlines()):
        stripped = line.strip()
        if stripped and not stripped.startswith("warning:"):
            return stripped
    return "nix build --dry-run failed"


def estimate_download(
    flake_dir,
    host: str,
    *,
    timeout: int | None = 900,
) -> DownloadEstimate:
    """Ask Nix what the updated system would need to fetch and build."""

    reference = (
        f"{nix.flake_ref(flake_dir)}"
        f"#nixosConfigurations.{host}.config.system.build.toplevel"
    )
    try:
        process = nix.nix(
            "build",
            "--dry-run",
            "--no-link",
            reference,
            timeout=timeout,
            check=False,
            env=_C_LOCALE_ENV,
        )
    except subprocess.TimeoutExpired:
        return DownloadEstimate(
            error=f"cache lookup timed out after {timeout}s; pass --no-download to skip it"
        )
    except OSError as error:
        return DownloadEstimate(error=f"could not run nix build: {error}")

    output = (process.stderr or "") + "\n" + (process.stdout or "")
    if process.returncode != 0:
        return DownloadEstimate(error=_last_error_line(output))

    return parse_estimate(output)


def format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    amount = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    index = 0
    while amount >= 1024 and index < len(units) - 1:
        amount /= 1024
        index += 1
    if index == 0:
        return f"{int(amount)} B"
    return f"{amount:.1f} {units[index]}"
