import argparse
import os
import socket
import sys
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Callable, Iterable, Sequence
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
import uvicorn

from app.schemas import ConvertParams
from app.services.converter_adapter import (
    ConversionPayload,
    MAX_UPLOAD_BYTES,
    InputTooLargeError,
    convert_uploaded_image,
    inspect_source_metadata,
)
from app.versioning import get_display_version

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
DEFAULT_HOST = "0.0.0.0"
LOCALHOST = "127.0.0.1"
DEFAULT_PORT = 8000
FALLBACK_PORT_MAX = 8099
UPLOAD_READ_CHUNK_BYTES = 64 * 1024

CanBind = Callable[[str, int], tuple[bool, OSError | None]]


@dataclass(frozen=True)
class ServerOptions:
    host: str
    port: int
    port_was_auto_selected: bool = False
    default_port_error: OSError | None = None


class PortUnavailableError(RuntimeError):
    def __init__(self, host: str, port: int, original_error: OSError | None) -> None:
        self.host = host
        self.port = port
        self.original_error = original_error
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        if self.original_error is None:
            return f"{self.host}:{self.port} is unavailable."
        return f"{self.host}:{self.port} is unavailable: {self.original_error}"


app = FastAPI(
    title="Slack Emoji Tailor",
    description="Convert images into Slack emoji-friendly assets.",
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "title": "Slack Emoji Tailor",
            "default_max_kb": 128,
            "default_size": "auto",
            "default_fit": "stretch",
            "default_max_frames": 50,
            "app_version": get_display_version(),
        },
    )


@app.get("/healthz", response_class=JSONResponse)
async def healthz() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})


@app.post("/api/inspect", response_class=JSONResponse)
async def inspect_image(file: UploadFile = File(...)) -> JSONResponse:
    try:
        payload = await _read_upload_bounded(file, limit=MAX_UPLOAD_BYTES)
        metadata = inspect_source_metadata(payload)
    except InputTooLargeError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return JSONResponse(content=metadata.model_dump())


def _input_too_large_message(limit: int) -> str:
    return f"Input file is too large. Max allowed size is {limit // (1024 * 1024)}MB."


async def _read_upload_bounded(file: UploadFile, *, limit: int) -> bytes:
    payload = bytearray()
    total = 0

    while True:
        read_size = min(UPLOAD_READ_CHUNK_BYTES, limit + 1 - total)
        if read_size <= 0:
            raise InputTooLargeError(_input_too_large_message(limit))

        chunk = await file.read(read_size)
        if not chunk:
            break

        payload.extend(chunk)
        total += len(chunk)
        if total > limit:
            raise InputTooLargeError(_input_too_large_message(limit))

    return bytes(payload)


def _ascii_filename_fallback(filename: str) -> str:
    fallback = "".join(
        char
        if char.isascii()
        and 32 <= ord(char) < 127
        and char not in {'"', "\\", "/", ";"}
        else "_"
        for char in filename
    )
    return fallback.strip(" ._") or "emoji"


def _content_disposition_attachment(filename: str) -> str:
    fallback = _ascii_filename_fallback(filename)
    encoded_filename = quote(filename, safe="")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded_filename}"


def _frame_cap_headers(converted: ConversionPayload, params: ConvertParams) -> dict[str, str]:
    source_metadata = converted.source_metadata
    frame_cap_metadata = converted.frame_cap_metadata
    requested_max_frames = frame_cap_metadata.requested_max_frames or params.max_frames
    effective_max_frames = (
        frame_cap_metadata.effective_max_frames
        or (source_metadata.frame_count if not source_metadata.is_animated else params.max_frames)
    )
    frame_cap_mode = frame_cap_metadata.frame_cap_mode or (
        "none" if not source_metadata.is_animated else "user"
    )
    frame_reduction_reason = frame_cap_metadata.frame_reduction_reason or "none"

    return {
        "X-Requested-Max-Frames": str(requested_max_frames),
        "X-Effective-Max-Frames": str(effective_max_frames),
        "X-Frame-Cap-Mode": frame_cap_mode,
        "X-Frame-Reduction-Reason": frame_reduction_reason,
    }


@app.post("/api/convert")
async def convert_image(
    file: UploadFile = File(...),
    max_kb: int = Form(128),
    size: str = Form("auto"),
    fit: str = Form("stretch"),
    max_frames: int = Form(50),
    optimization_strategy: str = Form("frames"),
) -> Response:
    try:
        params = ConvertParams(
            max_kb=max_kb,
            size=size,
            fit=fit,
            max_frames=max_frames,
            optimization_strategy=optimization_strategy,
        )
    except ValidationError as error:
        raise HTTPException(
            status_code=422,
            detail=error.errors(include_context=False, include_url=False),
        ) from error

    try:
        payload = await _read_upload_bounded(file, limit=MAX_UPLOAD_BYTES)
        converted = convert_uploaded_image(
            file_bytes=payload,
            original_filename=file.filename,
            params=params,
        )
    except InputTooLargeError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    source_metadata = converted.source_metadata
    metadata = converted.metadata
    headers = {
        "Content-Disposition": _content_disposition_attachment(converted.filename),
        "X-Source-Format": source_metadata.format_name,
        "X-Source-Width": str(source_metadata.width),
        "X-Source-Height": str(source_metadata.height),
        "X-Source-Frame-Count": str(source_metadata.frame_count),
        "X-Source-Byte-Size": str(source_metadata.byte_size),
        "X-Source-Animated": str(source_metadata.is_animated).lower(),
        "X-Result-Format": metadata.format_name,
        "X-Result-Side": str(metadata.side),
        "X-Result-Colors": str(metadata.colors),
        "X-Result-Frame-Step": str(metadata.frame_step),
        "X-Result-Frame-Count": str(metadata.frame_count),
        **_frame_cap_headers(converted, params),
        "X-Result-Quality": str(metadata.quality),
        "X-Optimization-Strategy": params.optimization_strategy,
        "X-Result-Byte-Size": str(metadata.byte_size),
        "X-Target-Reached": str(metadata.target_reached).lower(),
    }
    return Response(
        content=converted.data,
        media_type=converted.media_type,
        headers=headers,
    )


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _parse_run_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="slack-emoji-tailor",
        description="Run Slack Emoji Tailor web server.",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("SLACK_EMOJI_TAILOR_HOST", DEFAULT_HOST),
        help="Bind host address.",
    )
    parser.add_argument(
        "--port",
        type=_port,
        default=None,
        help=(
            "Bind port number. If omitted, 8000 is tried first and the next "
            "available 8001-8099 port is selected automatically."
        ),
    )
    args = parser.parse_args(argv)
    args.port_source = "cli" if args.port is not None else "default"

    env_port = os.getenv("SLACK_EMOJI_TAILOR_PORT")
    if args.port is None and env_port:
        try:
            args.port = _port(env_port)
        except argparse.ArgumentTypeError as error:
            parser.error(f"invalid SLACK_EMOJI_TAILOR_PORT: {error}")
        args.port_source = "env"

    return args


def _socket_family(host: str) -> socket.AddressFamily:
    return socket.AF_INET6 if ":" in host else socket.AF_INET


def _can_bind(host: str, port: int) -> tuple[bool, OSError | None]:
    try:
        with socket.socket(_socket_family(host), socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
    except OSError as error:
        return False, error
    return True, None


def _resolve_server_options(
    args: argparse.Namespace,
    *,
    can_bind: CanBind = _can_bind,
) -> ServerOptions:
    host = args.host
    explicit_port = args.port

    if explicit_port is not None:
        is_available, error = can_bind(host, explicit_port)
        if not is_available:
            raise PortUnavailableError(host, explicit_port, error)
        return ServerOptions(host=host, port=explicit_port)

    is_default_available, default_error = can_bind(host, DEFAULT_PORT)
    if is_default_available:
        return ServerOptions(host=host, port=DEFAULT_PORT)

    for fallback_port in range(DEFAULT_PORT + 1, FALLBACK_PORT_MAX + 1):
        is_available, _ = can_bind(host, fallback_port)
        if is_available:
            return ServerOptions(
                host=host,
                port=fallback_port,
                port_was_auto_selected=True,
                default_port_error=default_error,
            )

    raise PortUnavailableError(host, DEFAULT_PORT, default_error)


def _is_wildcard_host(host: str) -> bool:
    return host in {"", "0.0.0.0", "::"}


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def _is_lan_ipv4_address(address: str) -> bool:
    try:
        parsed = ip_address(address)
    except ValueError:
        return False
    return parsed.version == 4 and not parsed.is_loopback and not parsed.is_unspecified


def _local_ipv4_addresses() -> list[str]:
    candidates: list[str] = []

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            candidates.append(sock.getsockname()[0])
    except OSError:
        pass

    try:
        hostname = socket.gethostname()
        candidates.extend(socket.gethostbyname_ex(hostname)[2])
    except OSError:
        pass

    try:
        for result in socket.getaddrinfo(
            socket.gethostname(),
            None,
            socket.AF_INET,
            socket.SOCK_DGRAM,
        ):
            candidates.append(result[4][0])
    except OSError:
        pass

    addresses: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not _is_lan_ipv4_address(candidate) or candidate in seen:
            continue
        seen.add(candidate)
        addresses.append(candidate)

    return addresses


def _server_urls(
    host: str,
    port: int,
    *,
    local_ipv4_addresses: Iterable[str] | None = None,
) -> list[str]:
    if not _is_wildcard_host(host):
        return [f"http://{_url_host(host)}:{port}"]

    urls = [f"http://{LOCALHOST}:{port}"]
    addresses = (
        list(local_ipv4_addresses)
        if local_ipv4_addresses is not None
        else _local_ipv4_addresses()
    )
    seen = {LOCALHOST}
    for address in addresses:
        if not _is_lan_ipv4_address(address) or address in seen:
            continue
        seen.add(address)
        urls.append(f"http://{address}:{port}")

    return urls


def _format_bind_error(error: OSError | None) -> str:
    if error is None:
        return "unknown bind error"
    winerror = getattr(error, "winerror", None)
    if winerror is None:
        return f"{error} (errno={error.errno})"
    return f"{error} (errno={error.errno}, winerror={winerror})"


def _print_auto_port_notice(options: ServerOptions) -> None:
    if not options.port_was_auto_selected:
        return

    print(
        (
            f"Port {DEFAULT_PORT} is unavailable on {options.host}; "
            f"using {options.port} instead."
        ),
        file=sys.stderr,
    )
    print(
        f"Bind error: {_format_bind_error(options.default_port_error)}",
        file=sys.stderr,
    )


def _print_server_urls(options: ServerOptions) -> None:
    urls = _server_urls(options.host, options.port)
    print(f"Local URL: {urls[0]}", file=sys.stderr)

    lan_urls = urls[1:] if _is_wildcard_host(options.host) else []
    for url in lan_urls:
        print(f"Same-network URL: {url}", file=sys.stderr)

    if _is_wildcard_host(options.host) and not lan_urls:
        print(
            "Same-network URL: no LAN IPv4 address detected. "
            "Check `ipconfig` and open http://<your-ip>:"
            f"{options.port} from another PC.",
            file=sys.stderr,
        )


def _print_port_error(error: PortUnavailableError) -> None:
    print(f"ERROR: {error}", file=sys.stderr)
    print(
        "Try another port, for example: uv run slack-emoji-tailor --port 8001",
        file=sys.stderr,
    )


def run_server() -> None:
    args = _parse_run_args()
    try:
        options = _resolve_server_options(args)
    except PortUnavailableError as error:
        _print_port_error(error)
        raise SystemExit(1) from error

    _print_auto_port_notice(options)
    _print_server_urls(options)
    uvicorn.run(app, host=options.host, port=options.port)


if __name__ == "__main__":
    run_server()
