import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.main as main_module
from app.main import app
from app.services.converter_adapter import inspect_source_metadata

client = TestClient(app)


def _make_png_bytes() -> bytes:
    image = Image.new("RGBA", (24, 18), (20, 160, 80, 255))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def _make_animated_gif_bytes(frame_count: int = 3) -> bytes:
    frames = [Image.new("RGBA", (16, 16), (index * 50, 20, 180, 255)) for index in range(frame_count)]
    stream = io.BytesIO()
    frames[0].save(
        stream,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=80,
        loop=0,
    )
    return stream.getvalue()


def test_inspect_source_metadata_reads_png_details() -> None:
    image_bytes = _make_png_bytes()

    metadata = inspect_source_metadata(image_bytes)

    assert metadata.format_name == "PNG"
    assert metadata.width == 24
    assert metadata.height == 18
    assert metadata.frame_count == 1
    assert metadata.byte_size == len(image_bytes)
    assert metadata.is_animated is False


def test_inspect_source_metadata_reads_animated_gif_frame_count() -> None:
    image_bytes = _make_animated_gif_bytes(frame_count=4)

    metadata = inspect_source_metadata(image_bytes)

    assert metadata.format_name == "GIF"
    assert metadata.width == 16
    assert metadata.height == 16
    assert metadata.frame_count == 4
    assert metadata.byte_size == len(image_bytes)
    assert metadata.is_animated is True


def test_inspect_source_metadata_rejects_invalid_image() -> None:
    with pytest.raises(ValueError, match="Unsupported or invalid image file"):
        inspect_source_metadata(b"not image data")


def test_api_inspect_png_success() -> None:
    image_bytes = _make_png_bytes()

    response = client.post(
        "/api/inspect",
        files={"file": ("avatar.png", image_bytes, "image/png")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "format_name": "PNG",
        "width": 24,
        "height": 18,
        "frame_count": 1,
        "byte_size": len(image_bytes),
        "is_animated": False,
    }


def test_api_inspect_animated_gif_success() -> None:
    image_bytes = _make_animated_gif_bytes(frame_count=3)

    response = client.post(
        "/api/inspect",
        files={"file": ("emoji.gif", image_bytes, "image/gif")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["format_name"] == "GIF"
    assert payload["width"] == 16
    assert payload["height"] == 16
    assert payload["frame_count"] == 3
    assert payload["byte_size"] == len(image_bytes)
    assert payload["is_animated"] is True


def test_api_inspect_invalid_image_returns_400() -> None:
    response = client.post(
        "/api/inspect",
        files={"file": ("bad.txt", b"not image data", "text/plain")},
    )

    assert response.status_code == 400
    assert "Unsupported or invalid image file" in response.json()["detail"]


def test_api_inspect_rejects_oversized_input_before_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "MAX_UPLOAD_BYTES", 8)

    def fail_if_called(*args: object, **kwargs: object) -> object:
        raise AssertionError("image inspection should not run after route-level size rejection")

    monkeypatch.setattr(main_module, "inspect_source_metadata", fail_if_called)

    response = client.post(
        "/api/inspect",
        files={"file": ("too_large.png", b"x" * 9, "image/png")},
    )

    assert response.status_code == 413


def test_index_displays_version_without_extra_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "get_display_version", lambda: "v9.8.7")

    response = client.get("/")

    assert response.status_code == 200
    assert "v9.8.7" in response.text
    assert "vv9.8.7" not in response.text
