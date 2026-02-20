import argparse
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
import uvicorn

from app.schemas import ConvertParams
from app.services.converter_adapter import InputTooLargeError, convert_uploaded_image

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

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
        },
    )


@app.get("/healthz", response_class=JSONResponse)
async def healthz() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})


@app.post("/api/convert")
async def convert_image(
    file: UploadFile = File(...),
    max_kb: int = Form(128),
    size: str = Form("auto"),
    fit: str = Form("stretch"),
    max_frames: int = Form(50),
) -> Response:
    try:
        params = ConvertParams(
            max_kb=max_kb,
            size=size,
            fit=fit,
            max_frames=max_frames,
        )
    except ValidationError as error:
        raise HTTPException(
            status_code=422,
            detail=error.errors(include_context=False, include_url=False),
        ) from error

    payload = await file.read()

    try:
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
        "Content-Disposition": f'attachment; filename="{converted.filename}"',
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
        "X-Result-Quality": str(metadata.quality),
        "X-Result-Byte-Size": str(metadata.byte_size),
        "X-Target-Reached": str(metadata.target_reached).lower(),
    }
    return Response(
        content=converted.data,
        media_type=converted.media_type,
        headers=headers,
    )


def _parse_run_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="slack-emoji-tailor",
        description="Run Slack Emoji Tailor web server.",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("SLACK_EMOJI_TAILOR_HOST", "127.0.0.1"),
        help="Bind host address.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("SLACK_EMOJI_TAILOR_PORT", "8000")),
        help="Bind port number.",
    )
    return parser.parse_args()


def run_server() -> None:
    args = _parse_run_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    run_server()
