import subprocess
from pathlib import Path

import pytest

import app.versioning as versioning


@pytest.fixture(autouse=True)
def clear_version_cache() -> None:
    versioning.get_display_version.cache_clear()
    yield
    versioning.get_display_version.cache_clear()


def test_derive_display_version_formats_commits_ahead_as_fourth_segment() -> None:
    assert versioning.derive_display_version_from_describe("v0.1.1-2-gaaed0c6") == "0.1.1.2"


def test_derive_display_version_keeps_exact_tag() -> None:
    assert versioning.derive_display_version_from_describe("v0.1.1") == "0.1.1"


def test_derive_display_version_keeps_zero_ahead_long_tag() -> None:
    assert versioning.derive_display_version_from_describe("v0.1.1-0-gaaed0c6") == "0.1.1"


def test_derive_display_version_returns_none_for_invalid_value() -> None:
    assert versioning.derive_display_version_from_describe("not-a-version") is None


def test_get_display_version_uses_git_describe_even_when_env_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_EMOJI_TAILOR_VERSION", "v9.8.7")
    monkeypatch.setattr(versioning, "_git_describe", lambda repo_root: "v0.1.1-2-gaaed0c6")
    monkeypatch.setattr(versioning, "_baked_git_tag_version", lambda: "0.1.0")

    assert versioning.get_display_version() == "0.1.1.2"


def test_get_display_version_reads_baked_git_tag_version_when_git_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_EMOJI_TAILOR_VERSION", "v9.8.7")
    monkeypatch.setattr(versioning, "_git_describe", lambda repo_root: None)
    monkeypatch.setattr(versioning, "_baked_git_tag_version", lambda: "0.1.1.4")

    assert versioning.get_display_version() == "0.1.1.4"


def test_get_display_version_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def git_describe(repo_root: Path) -> str | None:
        nonlocal calls
        calls += 1
        return "v0.1.1-1-gaaed0c6"

    monkeypatch.setattr(versioning, "_git_describe", git_describe)
    monkeypatch.setattr(versioning, "_baked_git_tag_version", lambda: None)

    assert versioning.get_display_version() == "0.1.1.1"
    assert versioning.get_display_version() == "0.1.1.1"
    assert calls == 1


def test_get_display_version_raises_when_git_tag_version_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_EMOJI_TAILOR_VERSION", "v9.8.7")
    monkeypatch.setattr(versioning, "_git_describe", lambda repo_root: None)
    monkeypatch.setattr(versioning, "_baked_git_tag_version", lambda: None)

    with pytest.raises(versioning.VersionResolutionError, match="git tag"):
        versioning.get_display_version()


def test_git_describe_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="git describe", timeout=1)

    monkeypatch.setattr(versioning.subprocess, "run", raise_timeout)

    assert versioning._git_describe(Path.cwd()) is None
