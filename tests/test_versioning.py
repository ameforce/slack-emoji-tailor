import subprocess
from pathlib import Path

import pytest

import app.versioning as versioning


@pytest.fixture(autouse=True)
def clear_version_cache() -> None:
    versioning.get_display_version.cache_clear()
    yield
    versioning.get_display_version.cache_clear()


def test_derive_display_version_increments_commits_ahead() -> None:
    assert versioning.derive_display_version_from_describe("v0.1.1-2-gaaed0c6") == "v0.1.3"


def test_derive_display_version_keeps_exact_tag() -> None:
    assert versioning.derive_display_version_from_describe("v0.1.1") == "v0.1.1"


def test_derive_display_version_keeps_zero_ahead_long_tag() -> None:
    assert versioning.derive_display_version_from_describe("v0.1.1-0-gaaed0c6") == "v0.1.1"


def test_derive_display_version_returns_none_for_invalid_value() -> None:
    assert versioning.derive_display_version_from_describe("not-a-version") is None


def test_get_display_version_prefers_env_without_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(versioning.VERSION_ENV_VAR, "v9.8.7")

    def fail_git(repo_root: Path) -> str | None:
        raise AssertionError("git should not be called when env override is present")

    monkeypatch.setattr(versioning, "_git_describe", fail_git)

    assert versioning.get_display_version() == "v9.8.7"


def test_get_display_version_derives_from_git_describe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(versioning.VERSION_ENV_VAR, raising=False)
    monkeypatch.setattr(versioning, "_git_describe", lambda repo_root: "v0.1.1-2-gaaed0c6")
    monkeypatch.setattr(versioning, "_installed_package_version", lambda: "0.1.0")

    assert versioning.get_display_version() == "v0.1.3"


def test_get_display_version_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(versioning.VERSION_ENV_VAR, raising=False)
    calls = 0

    def git_describe(repo_root: Path) -> str | None:
        nonlocal calls
        calls += 1
        return "v0.1.1-1-gaaed0c6"

    monkeypatch.setattr(versioning, "_git_describe", git_describe)

    assert versioning.get_display_version() == "v0.1.2"
    assert versioning.get_display_version() == "v0.1.2"
    assert calls == 1


def test_get_display_version_falls_back_after_git_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(versioning.VERSION_ENV_VAR, raising=False)
    monkeypatch.setattr(versioning, "_git_describe", lambda repo_root: None)
    monkeypatch.setattr(versioning, "_installed_package_version", lambda: "0.1.0")

    assert versioning.get_display_version() == "0.1.0"


def test_git_describe_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="git describe", timeout=1)

    monkeypatch.setattr(versioning.subprocess, "run", raise_timeout)

    assert versioning._git_describe(Path.cwd()) is None
