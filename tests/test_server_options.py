import argparse

import pytest

from app.main import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    PortUnavailableError,
    _parse_run_args,
    _resolve_server_options,
    _server_urls,
)


def test_parse_run_args_keeps_default_port_implicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLACK_EMOJI_TAILOR_HOST", raising=False)
    monkeypatch.delenv("SLACK_EMOJI_TAILOR_PORT", raising=False)

    args = _parse_run_args([])

    assert args.host == DEFAULT_HOST
    assert args.host == "0.0.0.0"
    assert args.port is None
    assert args.port_source == "default"


def test_parse_run_args_marks_env_port_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_EMOJI_TAILOR_PORT", "8123")

    args = _parse_run_args([])

    assert args.port == 8123
    assert args.port_source == "env"


def test_default_port_falls_back_to_next_available_port() -> None:
    attempts: list[tuple[str, int]] = []

    def can_bind(host: str, port: int) -> tuple[bool, OSError | None]:
        attempts.append((host, port))
        if port == DEFAULT_PORT:
            return False, PermissionError(13, "forbidden")
        return True, None

    options = _resolve_server_options(
        argparse.Namespace(host=DEFAULT_HOST, port=None, port_source="default"),
        can_bind=can_bind,
    )

    assert options.port == DEFAULT_PORT + 1
    assert options.port_was_auto_selected is True
    assert isinstance(options.default_port_error, PermissionError)
    assert attempts == [(DEFAULT_HOST, DEFAULT_PORT), (DEFAULT_HOST, DEFAULT_PORT + 1)]


def test_explicit_unavailable_port_does_not_fall_back() -> None:
    attempts: list[tuple[str, int]] = []

    def can_bind(host: str, port: int) -> tuple[bool, OSError | None]:
        attempts.append((host, port))
        return False, PermissionError(13, "forbidden")

    with pytest.raises(PortUnavailableError) as exc_info:
        _resolve_server_options(
            argparse.Namespace(host=DEFAULT_HOST, port=DEFAULT_PORT, port_source="cli"),
            can_bind=can_bind,
        )

    assert exc_info.value.host == DEFAULT_HOST
    assert exc_info.value.port == DEFAULT_PORT
    assert attempts == [(DEFAULT_HOST, DEFAULT_PORT)]


def test_server_urls_for_wildcard_host_include_lan_addresses() -> None:
    urls = _server_urls(
        "0.0.0.0",
        8001,
        local_ipv4_addresses=[
            "127.0.0.1",
            "0.0.0.0",
            "192.168.0.24",
            "192.168.0.24",
            "10.0.0.7",
        ],
    )

    assert urls == [
        "http://127.0.0.1:8001",
        "http://192.168.0.24:8001",
        "http://10.0.0.7:8001",
    ]


def test_server_urls_for_specific_host_use_that_host_only() -> None:
    urls = _server_urls(
        "192.168.0.24",
        8000,
        local_ipv4_addresses=["10.0.0.7"],
    )

    assert urls == ["http://192.168.0.24:8000"]
