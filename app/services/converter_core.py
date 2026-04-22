from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

from PIL import Image, ImageOps, ImageSequence

RESAMPLE_LANCZOS = getattr(Image, "Resampling", Image).LANCZOS
QUANTIZE_FAST = getattr(getattr(Image, "Quantize", object), "FASTOCTREE", 2)
DITHER_NONE = getattr(getattr(Image, "Dither", object), "NONE", 0)


@dataclass
class EncodeResult:
    data: bytes
    format_name: str
    side: int
    colors: int
    frame_step: int = 1
    frame_count: int = 1
    quality: int = 0


def parse_size_option(raw_value: str) -> int | None:
    value = str(raw_value).strip().lower()
    if value == "auto":
        return None

    try:
        size = int(value)
    except ValueError as error:
        raise ValueError('--size must be an integer or "auto".') from error

    if size < 16:
        raise ValueError("--size must be >= 16.")
    return size


def resolve_auto_side(width: int, height: int, is_animated: bool) -> int:
    auto_cap = 224 if is_animated else 256
    return max(16, min(max(width, height), auto_cap))


def unique_preserve_order(values: Sequence[int]) -> List[int]:
    seen = set()
    out: List[int] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def build_side_candidates(target_side: int) -> List[int]:
    target_side = max(16, target_side)
    scale_points = [
        1.00,
        0.95,
        0.90,
        0.85,
        0.80,
        0.75,
        0.70,
        0.65,
        0.60,
        0.55,
        0.50,
        0.45,
        0.40,
        0.35,
        0.30,
        0.25,
    ]
    anchor_points = [
        256,
        224,
        192,
        176,
        160,
        144,
        128,
        112,
        96,
        88,
        80,
        72,
        64,
        56,
        48,
        40,
        32,
        24,
        16,
    ]
    scaled_points = [int(target_side * scale) for scale in scale_points]
    merged = [value for value in (scaled_points + anchor_points) if 16 <= value <= target_side]
    return unique_preserve_order(merged)


def has_alpha(image: Image.Image) -> bool:
    if image.mode in ("RGBA", "LA"):
        return True
    if image.mode == "P":
        return "transparency" in image.info
    return False


def to_square(image: Image.Image, side: int, fit_mode: str) -> Image.Image:
    rgba = image.convert("RGBA")
    if fit_mode == "stretch":
        return rgba.resize((side, side), RESAMPLE_LANCZOS)

    if fit_mode == "cover":
        return ImageOps.fit(
            rgba,
            (side, side),
            method=RESAMPLE_LANCZOS,
            centering=(0.5, 0.5),
        )

    contained = ImageOps.contain(rgba, (side, side), method=RESAMPLE_LANCZOS)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    offset = ((side - contained.width) // 2, (side - contained.height) // 2)
    canvas.alpha_composite(contained, dest=offset)
    return canvas


def encode_png(image: Image.Image, colors: int) -> bytes:
    paletted = image.quantize(colors=colors, method=QUANTIZE_FAST, dither=DITHER_NONE)
    stream = io.BytesIO()
    paletted.save(stream, format="PNG", optimize=True, compress_level=9)
    return stream.getvalue()


def encode_jpeg(image: Image.Image, quality: int) -> bytes:
    rgb = image.convert("RGB")
    stream = io.BytesIO()
    rgb.save(
        stream,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
        subsampling=1,
    )
    return stream.getvalue()


def sample_frames(
    frames: Sequence[Image.Image],
    durations: Sequence[int],
    step: int,
) -> Tuple[List[Image.Image], List[int]]:
    sampled_frames: List[Image.Image] = []
    sampled_durations: List[int] = []
    for index in range(0, len(frames), step):
        sampled_frames.append(frames[index])
        sampled_durations.append(sum(durations[index : index + step]))
    return sampled_frames, sampled_durations


def collapse_identical_frames(
    frames: Sequence[Image.Image],
    durations: Sequence[int],
) -> Tuple[List[Image.Image], List[int]]:
    if not frames:
        return [], []

    merged_frames: List[Image.Image] = [frames[0]]
    merged_durations: List[int] = [durations[0]]
    prev_signature = (frames[0].size, frames[0].convert("RGBA").tobytes())

    for frame, duration in zip(frames[1:], durations[1:]):
        current_signature = (frame.size, frame.convert("RGBA").tobytes())
        if current_signature == prev_signature:
            merged_durations[-1] += duration
            continue
        merged_frames.append(frame)
        merged_durations.append(duration)
        prev_signature = current_signature

    return merged_frames, merged_durations


def build_step_candidates(frame_count: int, max_frames: int) -> List[int]:
    if frame_count <= 1:
        return [1]

    min_step = max(1, math.ceil(frame_count / max(1, max_frames)))
    ladder = [1, 2, 3, 4, 5, 6, 8, 10, 12, 14, 16, 20]
    candidates = [value for value in ladder if value >= min_step]
    if not candidates:
        candidates = [min_step]
    if min_step not in candidates:
        candidates.insert(0, min_step)
    return unique_preserve_order(candidates)


def convert_static(
    image: Image.Image,
    fit_mode: str,
    target_side: int | None,
    max_bytes: int,
) -> EncodeResult:
    base_side = (
        resolve_auto_side(image.width, image.height, is_animated=False)
        if target_side is None
        else max(16, target_side)
    )
    side_candidates = build_side_candidates(base_side)
    color_candidates = [256, 224, 192, 160, 128, 112, 96, 80, 64, 48, 32, 24, 16]
    best: EncodeResult | None = None
    supports_jpeg = not has_alpha(image)
    squared_cache: dict[int, Image.Image] = {}
    candidate_plan: List[Tuple[float, str, int, int, int]] = []

    for side in side_candidates:
        side_loss = 1.0 - (side / base_side)
        for colors in color_candidates:
            color_loss = 1.0 - (colors / color_candidates[0])
            imbalance = abs(side_loss - color_loss)
            score = (0.68 * side_loss) + (0.32 * color_loss) + (0.10 * imbalance)
            candidate_plan.append((score, "PNG", side, colors, 0))

    if supports_jpeg:
        for side in side_candidates:
            side_loss = 1.0 - (side / base_side)
            for quality in (92, 86, 80, 74, 68, 62, 56, 50, 44, 38, 32):
                quality_loss = 1.0 - (quality / 95.0)
                imbalance = abs(side_loss - quality_loss)
                score = (0.72 * side_loss) + (0.28 * quality_loss) + (0.08 * imbalance) + 0.03
                candidate_plan.append((score, "JPEG", side, 0, quality))

    candidate_plan.sort(key=lambda item: (item[0], -item[2], -item[3], -item[4]))

    for _, format_name, side, colors, quality in candidate_plan:
        if side not in squared_cache:
            squared_cache[side] = to_square(image, side, fit_mode)
        squared = squared_cache[side]

        if format_name == "PNG":
            data = encode_png(squared, colors=colors)
            candidate = EncodeResult(
                data=data,
                format_name="PNG",
                side=side,
                colors=colors,
            )
        else:
            data = encode_jpeg(squared, quality=quality)
            candidate = EncodeResult(
                data=data,
                format_name="JPEG",
                side=side,
                colors=0,
                quality=quality,
            )

        if best is None or len(data) < len(best.data):
            best = candidate
        if len(data) <= max_bytes:
            return candidate

    if best is None:
        raise RuntimeError("Failed to encode static image.")
    return best


def load_gif_frames_from_image(image: Image.Image) -> Tuple[List[Image.Image], List[int]]:
    frames: List[Image.Image] = []
    durations: List[int] = []

    default_duration = int(image.info.get("duration", 80))
    for frame in ImageSequence.Iterator(image):
        frames.append(frame.convert("RGBA"))
        durations.append(max(20, int(frame.info.get("duration", default_duration))))

    if not frames:
        raise RuntimeError("No GIF frames found.")
    return frames, durations


def encode_gif(
    source_frames: Sequence[Image.Image],
    source_durations: Sequence[int],
    side: int,
    fit_mode: str,
    frame_step: int,
    colors: int,
) -> EncodeResult:
    frames, durations = sample_frames(source_frames, source_durations, frame_step)

    squared_frames = [to_square(frame, side, fit_mode) for frame in frames]
    paletted = [
        frame.quantize(colors=colors, method=QUANTIZE_FAST, dither=DITHER_NONE)
        for frame in squared_frames
    ]

    paletted, durations = collapse_identical_frames(paletted, durations)
    stream = io.BytesIO()
    paletted[0].save(
        stream,
        format="GIF",
        save_all=True,
        append_images=paletted[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    data = stream.getvalue()

    return EncodeResult(
        data=data,
        format_name="GIF",
        side=side,
        colors=colors,
        frame_step=frame_step,
        frame_count=len(paletted),
    )


def convert_gif_frames(
    source_frames: Sequence[Image.Image],
    source_durations: Sequence[int],
    fit_mode: str,
    target_side: int | None,
    max_bytes: int,
    max_frames: int,
) -> EncodeResult:
    if not source_frames:
        raise RuntimeError("No GIF frames found.")
    if len(source_frames) != len(source_durations):
        raise ValueError("Frame and duration counts must match.")
    base_side = (
        resolve_auto_side(source_frames[0].width, source_frames[0].height, is_animated=True)
        if target_side is None
        else max(16, target_side)
    )
    side_candidates = build_side_candidates(base_side)
    step_candidates = build_step_candidates(len(source_frames), max_frames=max_frames)
    color_candidates = [128, 112, 96, 80, 64, 56, 48, 40, 32, 24, 16]
    best: EncodeResult | None = None
    min_step = step_candidates[0]
    max_step = step_candidates[-1]
    candidate_plan: List[Tuple[float, int, int, int]] = []

    frame_weight = 0.70
    side_weight = 0.20
    color_weight = 0.10
    imbalance_weight = 0.06

    for side in side_candidates:
        side_loss = 1.0 - (side / base_side)
        for frame_step in step_candidates:
            if max_step == min_step:
                frame_loss = 0.0
            else:
                frame_loss = (frame_step - min_step) / (max_step - min_step)
            frame_penalty = math.sqrt(frame_loss)

            for colors in color_candidates:
                color_loss = 1.0 - (colors / color_candidates[0])
                losses = (side_loss, color_loss, frame_loss)
                imbalance = max(losses) - min(losses)
                score = (
                    (side_weight * side_loss)
                    + (color_weight * color_loss)
                    + (frame_weight * frame_penalty)
                    + (imbalance_weight * imbalance)
                )
                candidate_plan.append((score, side, frame_step, colors))

    candidate_plan.sort(key=lambda item: (item[0], item[2], -item[1], -item[3]))

    for _, side, frame_step, colors in candidate_plan:
        candidate = encode_gif(
            source_frames=source_frames,
            source_durations=source_durations,
            side=side,
            fit_mode=fit_mode,
            frame_step=frame_step,
            colors=colors,
        )
        if best is None or len(candidate.data) < len(best.data):
            best = candidate
        if len(candidate.data) <= max_bytes:
            return candidate

    if best is None:
        raise RuntimeError("Failed to encode GIF.")
    return best
