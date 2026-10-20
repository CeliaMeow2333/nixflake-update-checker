from __future__ import annotations

from nixflake_update_checker.download import format_bytes, parse_estimate

SAMPLE = """\
warning: ignoring the client-specified setting 'trusted-public-keys'
these 408 derivations will be built:
  /nix/store/02bqghprm37qhclpqfklialbjn8x2v0d-etc-os-release.drv
  /nix/store/518wpj9jija9zpxsj0k9f5ch4rhb5zia-glibc-locales-2.44-57.drv
these 1821 paths will be fetched (9.0 GiB download, 23.5 GiB unpacked):
  /nix/store/0vxpqd33z57265069xgxkd2a6vacvsr1-gdm
"""


def test_parse_full_output() -> None:
    estimate = parse_estimate(SAMPLE)
    assert estimate.fetch_paths == 1821
    assert estimate.download_bytes == int(9.0 * 1024**3)
    assert estimate.unpacked_bytes == int(23.5 * 1024**3)
    assert estimate.build_derivations == 408
    assert estimate.available
    assert estimate.has_work


def test_parse_nothing_to_do() -> None:
    estimate = parse_estimate("warning: something harmless\n")
    assert estimate.fetch_paths == 0
    assert estimate.download_bytes == 0
    assert estimate.build_derivations == 0
    assert not estimate.has_work


def test_parse_build_only() -> None:
    estimate = parse_estimate("these 2 derivations will be built:\n  /nix/store/a.drv\n")
    assert estimate.build_derivations == 2
    assert estimate.fetch_paths == 0


def test_parse_small_units() -> None:
    estimate = parse_estimate(
        "these 3 paths will be fetched (1.5 MiB download, 4 KiB unpacked):\n"
    )
    assert estimate.download_bytes == int(1.5 * 1024**2)
    assert estimate.unpacked_bytes == 4 * 1024


def test_format_bytes() -> None:
    assert format_bytes(0) == "0 B"
    assert format_bytes(512) == "512 B"
    assert format_bytes(1536) == "1.5 KiB"
    assert format_bytes(int(9.0 * 1024**3)) == "9.0 GiB"
    assert format_bytes(None) == "unknown"
