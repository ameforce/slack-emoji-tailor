import io

import pytest
from PIL import Image
from pydantic import ValidationError

import app.services.converter_adapter as converter_adapter
from app.schemas import ConvertParams
from app.services.converter_core import EncodeResult


def _make_gif_bytes() -> bytes:
    frames = [
        Image.new("RGBA", (32, 32), (255, 0, 0, 255)),
        Image.new("RGBA", (32, 32), (0, 255, 0, 255)),
    ]
    stream = io.BytesIO()
    frames[0].save(
        stream,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=[80, 120],
        loop=0,
    )
    return stream.getvalue()


def _make_png_bytes() -> bytes:
    image = Image.new("RGBA", (32, 32), (20, 160, 80, 255))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def test_convert_params_default_strategy_preserves_frames_mode() -> None:
    assert ConvertParams().optimization_strategy == "frames"


def test_convert_params_rejects_unknown_strategy() -> None:
    with pytest.raises(ValidationError):
        ConvertParams(optimization_strategy="tiny")


def test_convert_uploaded_gif_passes_optimization_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_convert_gif_frames(**kwargs: object) -> EncodeResult:
        captured.update(kwargs)
        return EncodeResult(
            data=b"gif-result",
            format_name="GIF",
            side=32,
            colors=128,
            frame_step=1,
            frame_count=2,
            requested_max_frames=50,
            effective_max_frames=50,
            frame_cap_mode="user",
            frame_reduction_reason="none",
            candidate_budget=240,
            candidate_attempts=7,
            gif_search_exhausted=False,
        )

    monkeypatch.setattr(converter_adapter, "convert_gif_frames", fake_convert_gif_frames)

    payload = converter_adapter.convert_uploaded_image(
        file_bytes=_make_gif_bytes(),
        original_filename="animated.gif",
        params=ConvertParams(optimization_strategy="quality"),
    )

    assert captured["optimization_strategy"] == "quality"
    assert payload.metadata.format_name == "GIF"
    assert payload.metadata.frame_count == 2
    assert payload.frame_cap_metadata.requested_max_frames == 50
    assert payload.frame_cap_metadata.effective_max_frames == 50
    assert payload.frame_cap_metadata.frame_cap_mode == "user"
    assert payload.frame_cap_metadata.frame_reduction_reason == "none"
    assert payload.frame_cap_metadata.candidate_budget == 240
    assert payload.frame_cap_metadata.candidate_attempts == 7
    assert payload.frame_cap_metadata.gif_search_exhausted is False


def test_convert_uploaded_static_uses_neutral_frame_cap_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_convert_static(**kwargs: object) -> EncodeResult:
        return EncodeResult(
            data=b"png-result",
            format_name="PNG",
            side=32,
            colors=128,
            frame_step=1,
            frame_count=1,
        )

    monkeypatch.setattr(converter_adapter, "convert_static", fake_convert_static)

    payload = converter_adapter.convert_uploaded_image(
        file_bytes=_make_png_bytes(),
        original_filename="avatar.png",
        params=ConvertParams(max_frames=37),
    )

    assert payload.metadata.format_name == "PNG"
    assert payload.frame_cap_metadata.requested_max_frames == 37
    assert payload.frame_cap_metadata.effective_max_frames == 1
    assert payload.frame_cap_metadata.frame_cap_mode == "none"
    assert payload.frame_cap_metadata.frame_reduction_reason == "none"
    assert payload.frame_cap_metadata.candidate_budget == 0
    assert payload.frame_cap_metadata.candidate_attempts == 0
    assert payload.frame_cap_metadata.gif_search_exhausted is False
