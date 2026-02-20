import io

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services.converter_adapter import MAX_UPLOAD_BYTES

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


def test_convert_rejects_oversized_input() -> None:
    oversized = b"x" * (MAX_UPLOAD_BYTES + 1)
    response = client.post(
        "/api/convert",
        files={"file": ("too_large.png", oversized, "image/png")},
        data={
            "max_kb": "128",
            "size": "auto",
            "fit": "stretch",
            "max_frames": "50",
        },
    )

    assert response.status_code == 413


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
