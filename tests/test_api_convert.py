import io
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.main as main_module
import app.services.converter_adapter as converter_adapter
from app.main import app
from app.schemas import ConvertMetadata, ConvertParams

client = TestClient(app)


def _make_png_bytes() -> bytes:
    image = Image.new("RGBA", (180, 180), (20, 160, 80, 255))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def test_convert_png_success() -> None:
    image_bytes = _make_png_bytes()
    response = client.post(
        "/api/convert",
        files={"file": ("avatar.png", image_bytes, "image/png")},
        data={
            "max_kb": "128",
            "size": "auto",
            "fit": "cover",
            "max_frames": "50",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")
    assert response.headers["x-source-format"] == "PNG"
    assert int(response.headers["x-source-width"]) == 180
    assert int(response.headers["x-source-height"]) == 180
    assert int(response.headers["x-source-frame-count"]) == 1
    assert int(response.headers["x-source-byte-size"]) == len(image_bytes)
    assert response.headers["x-source-animated"] == "false"
    assert response.headers["x-result-format"] in {"PNG", "JPEG", "GIF"}
    assert int(response.headers["x-result-byte-size"]) == len(response.content)
    assert len(response.content) <= 128 * 1024
    assert response.headers["x-optimization-strategy"] == "frames"


def test_convert_passes_optimization_strategy_form_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, ConvertParams] = {}

    def fake_convert_uploaded_image(
        file_bytes: bytes,
        original_filename: str | None,
        params: ConvertParams,
    ) -> converter_adapter.ConversionPayload:
        captured["params"] = params
        return converter_adapter.ConversionPayload(
            data=b"converted",
            media_type="image/gif",
            filename="emoji_slack.gif",
            source_metadata=converter_adapter.inspect_source_metadata(file_bytes),
            metadata=ConvertMetadata(
                format_name="GIF",
                side=128,
                colors=64,
                frame_step=2,
                frame_count=4,
                quality=85,
                byte_size=len(b"converted"),
                target_reached=True,
            ),
        )

    monkeypatch.setattr(main_module, "convert_uploaded_image", fake_convert_uploaded_image)

    response = client.post(
        "/api/convert",
        files={"file": ("avatar.png", _make_png_bytes(), "image/png")},
        data={
            "max_kb": "128",
            "size": "auto",
            "fit": "cover",
            "max_frames": "50",
            "optimization_strategy": "quality",
        },
    )

    assert response.status_code == 200
    assert captured["params"].optimization_strategy == "quality"
    assert response.headers["x-optimization-strategy"] == "quality"


def test_convert_rejects_invalid_optimization_strategy() -> None:
    response = client.post(
        "/api/convert",
        files={"file": ("avatar.png", _make_png_bytes(), "image/png")},
        data={
            "max_kb": "128",
            "size": "auto",
            "fit": "cover",
            "max_frames": "50",
            "optimization_strategy": "tiny",
        },
    )

    assert response.status_code == 422


def test_convert_with_non_ascii_filename_uses_safe_download_header() -> None:
    image_bytes = _make_png_bytes()
    response = client.post(
        "/api/convert",
        files={"file": ("테스트.png", image_bytes, "image/png")},
        data={
            "max_kb": "128",
            "size": "auto",
            "fit": "cover",
            "max_frames": "50",
        },
    )

    assert response.status_code == 200
    content_disposition = response.headers["content-disposition"]
    assert 'filename="' in content_disposition
    fallback = content_disposition.split('filename="', 1)[1].split('"', 1)[0]
    assert fallback
    assert fallback.isascii()
    assert f"filename*=UTF-8''{quote('테스트_slack.png', safe='')}" in content_disposition


def test_convert_rejects_oversized_input_before_conversion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "MAX_UPLOAD_BYTES", 8)

    def fail_if_called(*args: object, **kwargs: object) -> object:
        raise AssertionError("conversion should not run after route-level size rejection")

    monkeypatch.setattr(main_module, "convert_uploaded_image", fail_if_called)

    response = client.post(
        "/api/convert",
        files={"file": ("too_large.png", b"x" * 9, "image/png")},
        data={
            "max_kb": "128",
            "size": "auto",
            "fit": "stretch",
            "max_frames": "50",
        },
    )

    assert response.status_code == 413


def test_converter_adapter_rejects_oversized_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(converter_adapter, "MAX_UPLOAD_BYTES", 8)

    with pytest.raises(converter_adapter.InputTooLargeError):
        converter_adapter.convert_uploaded_image(
            file_bytes=b"x" * 9,
            original_filename="too_large.png",
            params=ConvertParams(),
        )


def test_convert_rejects_invalid_size_parameter() -> None:
    image_bytes = _make_png_bytes()
    response = client.post(
        "/api/convert",
        files={"file": ("avatar.png", image_bytes, "image/png")},
        data={
            "max_kb": "128",
            "size": "not-a-size",
            "fit": "cover",
            "max_frames": "50",
        },
    )

    assert response.status_code == 422
