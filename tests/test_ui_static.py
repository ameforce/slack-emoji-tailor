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


def test_index_exposes_dynamic_frame_target_status() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert 'id="frame-target-insight"' in response.text
    assert "원본 프레임" in response.text
    assert "유효 목표" in response.text
    assert "비프레임 전략의 사용자 제한" in response.text


def test_static_js_updates_dynamic_source_and_effective_frame_copy() -> None:
    script = Path("app/static/app.js").read_text(encoding="utf-8")

    assert "latestInspectMetadata" in script
    assert "renderFrameTargetInsight" in script
    assert "buildFrameTargetMessage" in script
    assert "FRAME_PRIORITY_SCAN_LIMIT = 300" in script
    assert "Effective target" in script
    assert "원본 프레임" in script
    assert "유효 목표" in script
    assert "source/effective" in script
    assert "renderFrameTargetInsight();" in script


def test_static_js_invalidates_stale_inspect_metadata_and_recomputes_on_controls() -> None:
    script = Path("app/static/app.js").read_text(encoding="utf-8")

    assert "clearInspectMetadata" in script
    assert "latestInspectMetadata = null" in script
    assert "latestInspectMetadata = normalizeInspectMetadata(metadata)" in script
    assert "latestInspectMetadata = buildFallbackInspectMetadata" in script
    assert 'optimizationStrategy.addEventListener("change", renderFrameTargetInsight)' in script
    assert 'maxFramesInput.addEventListener("input", renderFrameTargetInsight)' in script


def test_static_js_preserves_integer_max_frames_submission() -> None:
    script = Path("app/static/app.js").read_text(encoding="utf-8")

    assert "normalizeIntegerFormValue" in script
    assert 'formData.append("max_frames", normalizeIntegerFormValue(maxFramesInput, "50"))' in script


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


def test_static_js_displays_gif_candidate_budget_headers() -> None:
    script = Path("app/static/app.js").read_text(encoding="utf-8")

    assert 'headers.get("x-gif-candidate-budget")' in script
    assert 'headers.get("x-gif-candidate-attempts")' in script
    assert 'headers.get("x-gif-search-exhausted")' in script
    assert "GIF Candidate Budget" in script
    assert "GIF Candidate Attempts" in script
    assert "GIF Search Exhausted" in script
    assert '"budget-limit": "후보 예산 제한"' in script
