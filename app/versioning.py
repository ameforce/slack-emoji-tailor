from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path

BAKED_GIT_VERSION_FILE = "_git_version"
_GIT_DESCRIBE_TIMEOUT_SECONDS = 1.0
_LONG_DESCRIBE_RE = re.compile(
    r"^(?P<tag>.+)-(?P<count>\d+)-g(?P<sha>[0-9a-fA-F]+)(?P<dirty>-dirty)?$"
)
_TAG_VERSION_RE = re.compile(r"^v[0-9][0-9A-Za-z._-]*$")


class VersionResolutionError(RuntimeError):
    """Raised when no git tag-derived app version is available."""


def derive_display_version_from_describe(describe: str) -> str | None:
    """Validate and normalize a human display version from ``git describe`` output."""

    value = describe.strip()
    if not value or any(char.isspace() for char in value):
        return None

    long_match = _LONG_DESCRIBE_RE.match(value)
    if long_match:
        tag = long_match.group("tag")
        if not _TAG_VERSION_RE.match(tag):
            return None
        ahead_count = int(long_match.group("count"))
        if ahead_count == 0:
            return tag
        return value

    if _TAG_VERSION_RE.match(value):
        return value
    return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _git_describe(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--dirty", "--match", "v[0-9]*"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=_GIT_DESCRIBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None


def _baked_git_tag_version() -> str | None:
    path = Path(__file__).with_name(BAKED_GIT_VERSION_FILE)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return derive_display_version_from_describe(value)


@lru_cache(maxsize=1)
def get_display_version() -> str:
    """Return the app-visible version from git tag metadata only."""

    describe = _git_describe(_repo_root())
    if describe:
        derived = derive_display_version_from_describe(describe)
        if derived:
            return derived

    baked = _baked_git_tag_version()
    if baked:
        return baked

    raise VersionResolutionError(
        "Unable to resolve app version from git tag metadata. "
        "Fetch git tags before building or bake app/_git_version from git describe."
    )
