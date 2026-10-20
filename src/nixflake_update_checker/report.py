"""Human readable (rich), JSON and Markdown rendering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .download import format_bytes
from .models import DownloadEstimate, InputChange, PackageChange, Report

KIND_STYLE = {
    "upgraded": "green",
    "downgraded": "red",
    "added": "cyan",
    "removed": "red",
    "changed": "yellow",
}

KIND_LABEL = {
    "upgraded": "↑ upgraded",
    "downgraded": "↓ downgraded",
    "added": "＋ added",
    "removed": "－ removed",
    "changed": "~ changed",
}

STATUS_STYLE = {
    "updated": "green",
    "added": "cyan",
    "removed": "red",
    "up-to-date": "dim",
}


def _input_table(inputs: Iterable[InputChange]) -> Table:
    table = Table(
        title="Flake inputs",
        title_justify="left",
        header_style="bold",
        expand=False,
    )
    table.add_column("Input", style="bold")
    table.add_column("Repository")
    table.add_column("Old rev", no_wrap=True)
    table.add_column("New rev", no_wrap=True)
    table.add_column("Commits", justify="right")
    table.add_column("Status")

    for change in inputs:
        source = change.new or change.old
        old_rev = change.old.short_rev if change.old else "—"
        new_rev = Text(change.new.short_rev if change.new else "—")
        if change.new and change.new.date and change.status == "updated":
            new_rev.append(f"\n{change.new.date}", style="dim")
        commits = "—" if change.commits is None else str(change.commits)
        style = STATUS_STYLE.get(change.status, "")
        status = "up to date" if change.status == "up-to-date" else change.status

        table.add_row(
            escape(change.display_name),
            escape(source.label if source else ""),
            old_rev,
            new_rev,
            commits,
            Text(status, style=style),
        )
    return table


def _package_table(title: str, changes: Iterable[PackageChange]) -> Table:
    table = Table(title=title, title_justify="left", header_style="bold", expand=False)
    table.add_column("Package", style="bold")
    table.add_column("Old version")
    table.add_column("New version")
    table.add_column("Change")

    for change in changes:
        table.add_row(
            escape(change.pname),
            escape(change.old_display),
            escape(change.new_display),
            Text(KIND_LABEL.get(change.kind, change.kind), style=KIND_STYLE.get(change.kind, "")),
        )
    return table


def _summary(report: Report) -> Text:
    lines: list[Text] = []

    if report.old_label or report.new_label:
        line = Text()
        line.append("NixOS  ", style="bold")
        line.append(f"{report.old_label or '?'} → {report.new_label or '?'}")
        lines.append(line)

    updated = sum(1 for item in report.inputs if item.status == "updated")
    unchanged = sum(1 for item in report.inputs if item.status == "up-to-date")
    line = Text()
    line.append("Inputs  ", style="bold")
    line.append(f"{updated} updated, {unchanged} up to date")
    lines.append(line)

    if report.packages is not None:
        packages = report.packages
        line = Text()
        line.append("Packages  ", style="bold")
        line.append(f"{len(packages.changes)} changed")
        if packages.explicit_changes:
            line.append(f" ({len(packages.explicit_changes)} explicitly installed)")
        line.append("\n  ")

        parts = [
            ("upgraded", "green"),
            ("downgraded", "red"),
            ("added", "cyan"),
            ("removed", "red"),
            ("changed", "yellow"),
        ]
        rendered = False
        for kind, style in parts:
            count = packages.count(kind)
            if count:
                if rendered:
                    line.append(" · ")
                line.append(f"{count} {kind}", style=style)
                rendered = True
        if not rendered:
            line.append("no version changes")
        lines.append(line)

    return Text("\n").join(lines)


def _download_panel(estimate: DownloadEstimate) -> Panel:
    text = Text()
    if estimate.error:
        text.append("Not available: ", style="bold")
        text.append(estimate.error)
        return Panel(
            text,
            title="Update download estimate",
            border_style="yellow",
            expand=False,
        )

    if estimate.fetch_paths:
        text.append("Download  ", style="bold")
        text.append(
            f"{format_bytes(estimate.download_bytes)} across {estimate.fetch_paths:,} paths\n"
        )
        text.append("Unpacked  ", style="bold")
        text.append(f"{format_bytes(estimate.unpacked_bytes)}\n")
    else:
        text.append("Download  ", style="bold")
        text.append("nothing to fetch\n")

    text.append("Build     ", style="bold")
    if estimate.build_derivations:
        text.append(f"{estimate.build_derivations:,} derivations without a cache hit")
    else:
        text.append("nothing to build")

    return Panel(
        text,
        title="Update download estimate",
        border_style="blue",
        expand=False,
    )


def print_report(
    report: Report,
    console: Console,
    *,
    show_all: bool = False,
    limit: int = 40,
) -> None:
    subtitle = f"{report.flake_path} · host [bold]{escape(report.host)}[/bold]"
    console.print(
        Panel(
            subtitle,
            title="Nix flake update check",
            border_style="blue",
            expand=False,
        )
    )

    if report.inputs:
        console.print(_input_table(report.inputs))

    if not report.has_updates:
        console.print("[green]Everything is already up to date.[/green]")

    packages = report.packages
    if packages is not None:
        if packages.explicit_changes:
            console.print(_package_table("Software — explicitly installed", packages.explicit_changes))
        else:
            console.print("[dim]No explicitly installed package changed version.[/dim]")

        other = packages.other_changes
        if other:
            if show_all or len(other) <= limit:
                console.print(_package_table("Software — other closure components", other))
            else:
                console.print(
                    _package_table(f"Software — other closure components (first {limit})", other[:limit])
                )
                console.print(
                    f"[dim]… and {len(other) - limit} more. Use --all to show everything.[/dim]"
                )
    elif report.changed_inputs:
        console.print("[dim]Package comparison skipped.[/dim]")

    console.print()
    console.print(Panel(_summary(report), border_style="blue", expand=False))
    if packages is not None:
        console.print(
            "[dim]Versions are read from the build-time derivation closure of the system; "
            "nothing is built or downloaded. Build-time dependencies are included.[/dim]"
        )

    if report.download is not None:
        console.print(_download_panel(report.download))

    for warning in report.warnings:
        console.print(f"[yellow]warning:[/yellow] {escape(warning)}")


def write_json(report: Report, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report.to_dict(), handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def _markdown_package_table(changes: Iterable[PackageChange]) -> list[str]:
    lines = ["| Package | Old version | New version | Change |", "| --- | --- | --- | --- |"]
    for change in changes:
        lines.append(
            f"| `{change.pname}` | {change.old_display} | {change.new_display} | "
            f"{KIND_LABEL.get(change.kind, change.kind)} |"
        )
    return lines


def to_markdown(report: Report, *, show_all: bool = True, limit: int = 40) -> str:
    lines: list[str] = [f"# Nix flake update check — {report.flake_path}", ""]
    lines.append(f"- **Host:** `{report.host}`")
    if report.old_label or report.new_label:
        lines.append(f"- **NixOS label:** `{report.old_label}` → `{report.new_label}`")
    lines.append(f"- **Generated:** {report.generated_at.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    lines.append("## Flake inputs")
    lines.append("")
    lines.append("| Input | Repository | Old rev | New rev | Commits | Status |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for change in report.inputs:
        source = change.new or change.old
        commits = "" if change.commits is None else str(change.commits)
        status = "up to date" if change.status == "up-to-date" else change.status
        lines.append(
            f"| `{change.display_name}` | {source.label if source else ''} | "
            f"`{change.old.short_rev if change.old else '—'}` | "
            f"`{change.new.short_rev if change.new else '—'}` | {commits} | {status} |"
        )
    lines.append("")

    packages = report.packages
    if packages is not None:
        lines.append("## Summary")
        lines.append("")
        lines.append(f"- {len(packages.changes)} packages changed")
        for kind in ("upgraded", "downgraded", "added", "removed", "changed"):
            count = packages.count(kind)
            if count:
                lines.append(f"  - {count} {kind}")
        lines.append("")

        if packages.explicit_changes:
            lines.append("## Software — explicitly installed")
            lines.append("")
            lines.extend(_markdown_package_table(packages.explicit_changes))
            lines.append("")

        other = packages.other_changes
        if other:
            shown = other if show_all else other[:limit]
            suffix = "" if show_all or len(other) <= limit else f" (first {limit} of {len(other)})"
            lines.append(f"## Software — other closure components{suffix}")
            lines.append("")
            lines.extend(_markdown_package_table(shown))
            lines.append("")

    if report.download is not None:
        download = report.download
        lines.append("## Update download estimate")
        lines.append("")
        if download.error:
            lines.append(f"- Not available: {download.error}")
        elif download.fetch_paths:
            lines.append(
                f"- **Download:** {format_bytes(download.download_bytes)} across "
                f"{download.fetch_paths:,} paths"
            )
            lines.append(f"- **Unpacked:** {format_bytes(download.unpacked_bytes)}")
            if download.build_derivations:
                lines.append(
                    f"- **Built locally:** {download.build_derivations:,} derivations "
                    "without a cache hit"
                )
            else:
                lines.append("- **Built locally:** nothing")
        else:
            lines.append("- **Download:** nothing to fetch")
            lines.append("- **Built locally:** nothing")
        lines.append("")

    if report.warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in report.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_markdown(report: Report, path: Path, *, show_all: bool = True, limit: int = 40) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_markdown(report, show_all=show_all, limit=limit), encoding="utf-8")
