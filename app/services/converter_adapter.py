from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.schemas import ConvertMetadata, ConvertParams, SourceMetadata
from app.services.converter_core import (
    convert_gif_frames,
    convert_static,
    load_gif_frames_from_image,
    parse_size_option,
)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class InputTooLargeError(ValueError):
    pass


@dataclass(frozen=True)
class FrameCapMetadata:
    requested_max_frames: int = 0
    effective_max_frames: int = 0
    frame_cap_mode: str = "none"
    frame_reduction_reason: str = "none"
    candidate_budget: int = 0
    candidate_attempts: int = 0
    gif_search_exhausted: bool = False


@dataclass
class ConversionPayload:
    data: bytes
    media_type: str
    filename: str
    source_metadata: SourceMetadata
    metadata: ConvertMetadata
    frame_cap_metadata: FrameCapMetadata = field(default_factory=FrameCapMetadata)


def _resolve_output_info(format_name: str, original_filename: str | None) -> tuple[str, str]:
    extension = ".png"
    media_type = "image/png"
    if format_name == "GIF":
        extension = ".gif"
        media_type = "image/gif"
    elif format_name == "JPEG":
        extension = ".jpg"
        media_type = "image/jpeg"

    base = Path(original_filename or "emoji").stem or "emoji"
    filename = f"{base}_slack{extension}"
    return filename, media_type


def inspect_source_metadata(file_bytes: bytes) -> SourceMetadata:
    if not file_bytes:
        raise ValueError("Uploaded file is empty.")

    try:
        with Image.open(io.BytesIO(file_bytes)) as probe:
            return SourceMetadata(
                format_name=(probe.format or "UNKNOWN").upper(),
                width=probe.width,
                height=probe.height,
                frame_count=max(1, int(getattr(probe, "n_frames", 1) or 1)),
                byte_size=len(file_bytes),
                is_animated=bool(getattr(probe, "is_animated", False)),
            )
    except UnidentifiedImageError as error:
        raise ValueError("Unsupported or invalid image file.") from error
    except OSError as error:
        raise ValueError(f"Failed to read image data: {error}") from error


def _frame_cap_metadata_from_result(
    *,
    result: object,
    params: ConvertParams,
    source_metadata: SourceMetadata,
) -> FrameCapMetadata:
    if not source_metadata.is_animated:
        return FrameCapMetadata(
            requested_max_frames=params.max_frames,
            effective_max_frames=source_metadata.frame_count,
            frame_cap_mode="none",
            frame_reduction_reason="none",
            candidate_budget=0,
            candidate_attempts=0,
            gif_search_exhausted=False,
        )

    requested_max_frames = getattr(result, "requested_max_frames", None)
    effective_max_frames = getattr(result, "effective_max_frames", None)
    frame_cap_mode = getattr(result, "frame_cap_mode", None)
    frame_reduction_reason = getattr(result, "frame_reduction_reason", None)
    candidate_budget = getattr(result, "candidate_budget", None)
    candidate_attempts = getattr(result, "candidate_attempts", None)
    gif_search_exhausted = getattr(result, "gif_search_exhausted", None)

    return FrameCapMetadata(
        requested_max_frames=int(requested_max_frames or params.max_frames),
        effective_max_frames=int(effective_max_frames or params.max_frames),
        frame_cap_mode=str(frame_cap_mode or "user"),
        frame_reduction_reason=str(frame_reduction_reason or "none"),
        candidate_budget=int(candidate_budget or 0),
        candidate_attempts=int(candidate_attempts or 0),
        gif_search_exhausted=bool(gif_search_exhausted),
    )


def convert_uploaded_image(
    file_bytes: bytes,
    original_filename: str | None,
    params: ConvertParams,
) -> ConversionPayload:
    if not file_bytes:
        raise ValueError("Uploaded file is empty.")
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise InputTooLargeError(
            f"Input file is too large. Max allowed size is {MAX_UPLOAD_BYTES // (1024 * 1024)}MB."
        )

    source_metadata = inspect_source_metadata(file_bytes)
    max_bytes = params.max_kb * 1024
    target_side = parse_size_option(params.size)

    try:
        with Image.open(io.BytesIO(file_bytes)) as probe:
            if source_metadata.is_animated:
                source_frames, source_durations = load_gif_frames_from_image(probe)
                result = convert_gif_frames(
                    source_frames=source_frames,
                    source_durations=source_durations,
                    fit_mode=params.fit,
                    target_side=target_side,
                    max_bytes=max_bytes,
                    max_frames=params.max_frames,
                    optimization_strategy=params.optimization_strategy,
                )
            else:
                result = convert_static(
                    image=probe.copy(),
                    fit_mode=params.fit,
                    target_side=target_side,
                    max_bytes=max_bytes,
                )
    except UnidentifiedImageError as error:
        raise ValueError("Unsupported or invalid image file.") from error
    except OSError as error:
        raise ValueError(f"Failed to read image data: {error}") from error

    output_filename, media_type = _resolve_output_info(result.format_name, original_filename)
    metadata = ConvertMetadata(
        format_name=result.format_name,
        side=result.side,
        colors=result.colors,
        frame_step=result.frame_step,
        frame_count=result.frame_count,
        quality=result.quality,
        byte_size=len(result.data),
        target_reached=(len(result.data) <= max_bytes),
    )
    return ConversionPayload(
        data=result.data,
        media_type=media_type,
        filename=output_filename,
        source_metadata=source_metadata,
        metadata=metadata,
        frame_cap_metadata=_frame_cap_metadata_from_result(
            result=result,
            params=params,
            source_metadata=source_metadata,
        ),
    )
