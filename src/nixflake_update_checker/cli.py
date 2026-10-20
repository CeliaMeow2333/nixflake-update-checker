"""Command line entry point."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

from rich.console import Console
from rich.markup import escape

from . import (
    __version__,
    download as download_mod,
    flake,
    nix,
    packages as packages_mod,
    report as report_mod,
    upstream,
)
from .lockfile import (
    LockFileError,
    collect_inputs,
    diff_inputs,
    input_origins,
    read_lock,
)
from .models import Report
from .nix import NixError

DEFAULT_FLAKE = Path("/etc/nixos")


def _default_path() -> Path:
    if (DEFAULT_FLAKE / "flake.nix").is_file():
        return DEFAULT_FLAKE
    return Path.cwd()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nixflake-check",
        description=(
            "Read a local Nix flake, fetch the latest upstream inputs in a temporary "
            "copy, and summarize the software/version changes that an update would bring."
        ),
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help=f"flake directory to inspect (default: {DEFAULT_FLAKE} when present, else the current directory)",
    )
    parser.add_argument("--host", help="NixOS configuration name (default: auto-detect)")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="INPUT",
        help="only update this flake input (repeatable)",
    )
    parser.add_argument(
        "--new-lock",
        metavar="FILE",
        help="compare against this existing flake.lock instead of running nix flake update",
    )
    parser.add_argument(
        "--no-packages",
        action="store_true",
        help="skip the package/version comparison (much faster)",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="skip the download/build size estimate (nix build --dry-run)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="show every changed closure component instead of the first --limit",
    )
    parser.add_argument("--limit", type=int, default=40, help="rows to show for non-explicit packages")
    parser.add_argument("--json", metavar="FILE", help="write a machine readable report")
    parser.add_argument("--markdown", metavar="FILE", help="write a Markdown report")
    parser.add_argument(
        "--sudo",
        action="store_true",
        help="use sudo (which prompts on the terminal) if the flake directory is unreadable",
    )
    parser.add_argument("--no-github", action="store_true", help="skip GitHub commit-count lookups")
    parser.add_argument(
        "--timeout",
        type=int,
        default=nix.DEFAULT_TIMEOUT,
        help="timeout in seconds for each Nix command (default: %(default)s)",
    )
    parser.add_argument(
        "--exit-code",
        action="store_true",
        help="exit with code 10 when updates are available",
    )
    parser.add_argument("--keep-temp", action="store_true", help="keep the temporary flake copies")
    parser.add_argument("--verbose", "-v", action="store_true", help="show extra Nix output")
    parser.add_argument("--quiet", "-q", action="store_true", help="disable progress spinners")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


@contextmanager
def _spin(console: Console, message: str, enabled: bool) -> Iterator[None]:
    if enabled:
        with console.status(message):
            yield
    else:
        yield


def _shorten(text: str, limit: int = 1200) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return "…" + text[-limit:]


def _fail(console: Console, message: str) -> int:
    console.print(f"[red]error:[/red] {escape(message)}")
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.limit = max(1, args.limit)
    args.timeout = max(30, args.timeout)

    console = Console(highlight=False)
    started = time.monotonic()
    show_progress = not args.quiet

    source = Path(args.path).expanduser() if args.path else _default_path()
    source = source.resolve()

    try:
        flake.validate_flake(source)
    except flake.FlakeError as error:
        return _fail(console, str(error))

    temp_root = Path(tempfile.mkdtemp(prefix="nixflake-check-"))
    old_dir = temp_root / "old"
    new_dir = temp_root / "new"
    warnings: list[str] = []

    try:
        if args.sudo:
            console.print(
                "[dim]--sudo enabled: sudo may ask for your password in this terminal.[/dim]"
            )

        try:
            with _spin(console, "Copying flake to a temporary directory…", show_progress):
                flake.snapshot_flake(source, old_dir, allow_sudo=args.sudo)
                flake.snapshot_flake(source, new_dir, allow_sudo=args.sudo)
        except flake.FlakeError as error:
            return _fail(console, str(error))

        lock_path = old_dir / "flake.lock"
        if not lock_path.is_file():
            return _fail(
                console,
                f"{source / 'flake.lock'} does not exist. Run `nix flake lock` in the flake "
                "first, or pass --new-lock with an existing lock file to compare against.",
            )

        try:
            old_lock = read_lock(lock_path)
            old_inputs = collect_inputs(old_lock)
        except LockFileError as error:
            return _fail(console, str(error))

        try:
            host, hosts = flake.resolve_host(old_dir, args.host)
        except flake.FlakeError as error:
            return _fail(console, str(error))

        if args.host is None and len(hosts) > 1:
            warnings.append(
                f"Multiple NixOS configurations found ({', '.join(sorted(hosts))}); "
                f"using '{host}'. Pass --host to choose one."
            )

        if args.new_lock:
            try:
                with _spin(console, "Using the supplied lock file…", show_progress):
                    flake.use_existing_lock(new_dir, Path(args.new_lock).expanduser())
            except flake.FlakeError as error:
                return _fail(console, str(error))
        else:
            try:
                with _spin(console, "Fetching the latest upstream inputs (nix flake update)…", show_progress):
                    update_output = flake.update_lock(new_dir, args.only, timeout=args.timeout)
                if args.verbose and update_output.strip():
                    console.print(escape(update_output.strip()))
            except flake.FlakeError as error:
                return _fail(console, str(error))

        try:
            new_lock = read_lock(new_dir / "flake.lock")
            new_inputs = collect_inputs(new_lock)
        except LockFileError as error:
            return _fail(console, str(error))

        input_changes = diff_inputs(old_inputs, new_inputs)

        # Label nested inputs as "parent › alias" (e.g. "sfd-nix › nixpkgs").
        origins = input_origins(new_lock)
        old_origins = input_origins(old_lock)
        root_name = old_lock.get("root") or new_lock.get("root") or "root"
        for change in input_changes:
            origin = origins.get(change.name) or old_origins.get(change.name)
            if origin and origin[0] != root_name:
                change.parent, change.alias = origin
        order = {"updated": 0, "added": 1, "removed": 2, "up-to-date": 3}
        input_changes.sort(
            key=lambda change: (change.parent is not None, order.get(change.status, 4), change.name)
        )

        if not args.no_github:
            with _spin(console, "Measuring upstream commit distance…", show_progress):
                upstream.attach_commit_counts(input_changes)

        package_diff = None
        old_label: str | None = None
        new_label: str | None = None

        if args.no_packages:
            pass
        elif not any(change.changed for change in input_changes):
            warnings.append("Flake inputs are already up to date; no package comparison needed.")
        else:
            old_info = new_info = None
            try:
                with _spin(console, "Evaluating the current system configuration…", show_progress):
                    old_info = flake.evaluate(old_dir, host)
                old_label = old_info.get("label") if old_info else None
            except flake.FlakeError as error:
                warnings.append(f"Current configuration could not be evaluated: {_shorten(str(error))}")

            try:
                with _spin(console, "Evaluating the updated system configuration…", show_progress):
                    new_info = flake.evaluate(new_dir, host)
                new_label = new_info.get("label") if new_info else None
            except flake.FlakeError as error:
                warnings.append(f"Updated configuration could not be evaluated: {_shorten(str(error))}")

            if old_info and new_info:
                old_json = temp_root / "old-derivations.json"
                new_json = temp_root / "new-derivations.json"
                try:
                    with _spin(console, "Reading derivation closures…", show_progress):
                        with ThreadPoolExecutor(max_workers=2) as pool:
                            old_future = pool.submit(
                                nix.derivation_show, old_info["drvPath"], old_json, timeout=args.timeout
                            )
                            new_future = pool.submit(
                                nix.derivation_show, new_info["drvPath"], new_json, timeout=args.timeout
                            )
                            old_future.result()
                            new_future.result()

                    with _spin(console, "Comparing package versions…", show_progress):
                        old_map = packages_mod.load_package_map(old_json)
                        new_map = packages_mod.load_package_map(new_json)
                        explicit = packages_mod.explicit_pnames(
                            old_info.get("packages") or []
                        ) | packages_mod.explicit_pnames(new_info.get("packages") or [])
                        package_diff = packages_mod.diff_package_maps(old_map, new_map, explicit)
                except (NixError, OSError, ValueError) as error:
                    warnings.append(f"Package comparison failed: {_shorten(str(error))}")

        download_estimate = None
        if not args.no_download:
            with _spin(
                console,
                "Asking Nix how much the update would download (binary-cache lookup)…",
                show_progress,
            ):
                download_estimate = download_mod.estimate_download(
                    new_dir, host, timeout=args.timeout
                )
            if download_estimate.error:
                warnings.append(
                    f"Download estimate unavailable: {_shorten(download_estimate.error)}"
                )

        report = Report(
            flake_path=source,
            host=host,
            old_label=old_label,
            new_label=new_label,
            inputs=input_changes,
            packages=package_diff,
            download=download_estimate,
            warnings=warnings,
            duration=time.monotonic() - started,
        )

        report_mod.print_report(
            report,
            console,
            show_all=args.all,
            limit=args.limit,
        )

        if args.json:
            report_mod.write_json(report, Path(args.json).expanduser())
            if not args.quiet:
                console.print(f"[dim]JSON report written to {escape(args.json)}[/dim]")
        if args.markdown:
            report_mod.write_markdown(
                report,
                Path(args.markdown).expanduser(),
                show_all=True,
                limit=args.limit,
            )
            if not args.quiet:
                console.print(f"[dim]Markdown report written to {escape(args.markdown)}[/dim]")

        if args.keep_temp:
            console.print(f"[dim]Temporary copies kept at {temp_root}[/dim]")

        return 10 if args.exit_code and report.has_updates else 0

    except KeyboardInterrupt:  # pragma: no cover - interactive convenience
        console.print("[red]interrupted[/red]")
        return 130
    except (NixError, OSError) as error:
        return _fail(console, str(error))
    finally:
        if not args.keep_temp:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
