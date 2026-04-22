from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_index_explains_frame_priority_semantics() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "GIF 프레임 목표/제한" in response.text
    assert "프레임 우선 · 용량 내 최대 프레임 보존" in response.text
    assert "프레임 우선에서는 Slack 용량 내 원본 프레임을 우선 탐색" in response.text
    assert "프레임 우선은 Slack 용량 내에서 가능한 많은 프레임을 먼저 보존" in response.text


def test_static_js_displays_frame_cap_response_headers() -> None:
    script = Path("app/static/app.js").read_text(encoding="utf-8")

    assert 'headers.get("x-requested-max-frames")' in script
    assert 'headers.get("x-effective-max-frames")' in script
    assert 'headers.get("x-frame-cap-mode")' in script
    assert 'headers.get("x-frame-reduction-reason")' in script
    assert "Requested Max Frames" in script
    assert "Effective Max Frames" in script
    assert "Frame Cap Mode" in script
    assert "Frame Reduction Reason" in script
