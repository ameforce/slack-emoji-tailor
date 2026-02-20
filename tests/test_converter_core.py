import io

import pytest
from PIL import Image

from app.services.converter_core import (
    build_step_candidates,
    convert_gif_frames,
    convert_static,
    load_gif_frames_from_image,
    parse_size_option,
)


def _make_png_image() -> Image.Image:
    return Image.new("RGBA", (220, 140), (15, 120, 240, 255))


def _make_gif_bytes() -> bytes:
    frame_a = Image.new("RGBA", (96, 96), (255, 0, 0, 255))
    frame_b = Image.new("RGBA", (96, 96), (0, 0, 255, 255))
    stream = io.BytesIO()
    frame_a.save(
        stream,
        format="GIF",
        save_all=True,
        append_images=[frame_b],
        duration=[80, 120],
        loop=0,
        optimize=True,
    )
    return stream.getvalue()


def test_parse_size_option_valid_and_invalid() -> None:
    assert parse_size_option("auto") is None
    assert parse_size_option("96") == 96

    with pytest.raises(ValueError):
        parse_size_option("bad-input")

    with pytest.raises(ValueError):
        parse_size_option("15")


def test_build_step_candidates_respects_frame_limit() -> None:
    steps = build_step_candidates(frame_count=120, max_frames=50)
    assert steps[0] == 3
    assert all(step >= 3 for step in steps)


def test_convert_static_returns_image_data() -> None:
    image = _make_png_image()
    result = convert_static(
        image=image,
        fit_mode="contain",
        target_side=None,
        max_bytes=128 * 1024,
    )

    assert result.data
    assert result.format_name in {"PNG", "JPEG"}
    assert result.side >= 16


def test_convert_gif_frames_returns_gif() -> None:
    gif_bytes = _make_gif_bytes()
    with Image.open(io.BytesIO(gif_bytes)) as gif_image:
        source_frames, source_durations = load_gif_frames_from_image(gif_image)

    result = convert_gif_frames(
        source_frames=source_frames,
        source_durations=source_durations,
        fit_mode="contain",
        target_side=96,
        max_bytes=128 * 1024,
        max_frames=50,
    )

    assert result.format_name == "GIF"
    assert result.frame_count >= 1
    assert len(result.data) > 0
