from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from importlib import metadata
from pathlib import Path

PACKAGE_NAME = "slack-emoji-tailor"
FALLBACK_VERSION = "0.1.0"
VERSION_ENV_VAR = "SLACK_EMOJI_TAILOR_VERSION"
_GIT_DESCRIBE_TIMEOUT_SECONDS = 1.0
_LONG_DESCRIBE_RE = re.compile(
    r"^(?P<tag>.+)-(?P<count>\d+)-g(?P<sha>[0-9a-fA-F]+)(?P<dirty>-dirty)?$"
)
_FINAL_NUMERIC_SEGMENT_RE = re.compile(r"^(?P<prefix>.*?)(?P<number>\d+)(?P<suffix>\D*)$")


def derive_display_version_from_describe(describe: str) -> str | None:
    """Derive a human display version from ``git describe --long`` output.

    Exact tags remain unchanged. Commits ahead of a tag increment the final
    numeric segment of that tag by the ahead count, e.g. ``v0.1.1-2-gabc``
    becomes ``v0.1.3``.
    """

    value = describe.strip()
    if not value or any(char.isspace() for char in value):
        return None

    long_match = _LONG_DESCRIBE_RE.match(value)
    if long_match:
        tag = long_match.group("tag")
        ahead_count = int(long_match.group("count"))
        if ahead_count == 0:
            return tag
        return _increment_final_numeric_segment(tag, ahead_count)

    if any(char.isdigit() for char in value):
        return value
    return None


def _increment_final_numeric_segment(tag: str, increment: int) -> str | None:
    match = _FINAL_NUMERIC_SEGMENT_RE.match(tag)
    if not match:
        return None
    next_number = int(match.group("number")) + increment
    return f"{match.group('prefix')}{next_number}{match.group('suffix')}"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _git_describe(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--long", "--match", "v[0-9]*"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=_GIT_DESCRIBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None


def _installed_package_version() -> str | None:
    try:
        version = metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return None
    return version.strip() or None


@lru_cache(maxsize=1)
def get_display_version() -> str:
    """Return the app-visible version without failing request handling."""

    env_version = os.getenv(VERSION_ENV_VAR, "").strip()
    if env_version:
        return env_version

    describe = _git_describe(_repo_root())
    if describe:
        derived = derive_display_version_from_describe(describe)
        if derived:
            return derived

    package_version = _installed_package_version()
    if package_version:
        return package_version

    return FALLBACK_VERSION
