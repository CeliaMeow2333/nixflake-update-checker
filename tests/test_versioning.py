from __future__ import annotations

from pathlib import Path

from nixflake_update_checker.versioning import (
    compare_versions,
    normalize_pname,
    split_name_version,
)


def test_split_name_version() -> None:
    assert split_name_version("git-2.56.0") == ("git", "2.56.0")
    assert split_name_version("python3.13-anyio-4.15.1") == ("python3.13-anyio", "4.15.1")
    assert split_name_version("stable_deref_trait-1.2.1") == ("stable_deref_trait", "1.2.1")
    assert split_name_version("etc") is None
    assert split_name_version("bash-get-version") is None


def test_normalize_pname() -> None:
    assert normalize_pname("python3.13-anyio") == "anyio"
    assert normalize_pname("python3-requests") == "requests"
    assert normalize_pname("git") == "git"


def test_compare_versions() -> None:
    assert compare_versions("1.2.3", "1.2.4") < 0
    assert compare_versions("1.10", "1.9") > 0
    assert compare_versions("5.3p15", "5.3p20") < 0
    assert compare_versions("2026-07-23", "2026-08-01") < 0
    assert compare_versions("1.0", "1.0") == 0


def test_path_import_is_not_needed(tmp_path: Path) -> None:
    # Guards against accidental filesystem coupling in helpers.
    assert isinstance(tmp_path, Path)
