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
