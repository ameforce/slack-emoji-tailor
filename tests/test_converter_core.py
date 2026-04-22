import io

import pytest
from PIL import Image

import app.services.converter_core as converter_core
from app.services.converter_core import (
    GIF_FRAME_PRIORITY_SCAN_LIMIT,
    EncodeResult,
    build_step_candidates,
    collapse_identical_frames,
    convert_gif_frames,
    convert_static,
    get_gif_strategy_weights,
    load_gif_frames_from_image,
    parse_size_option,
    resolve_effective_max_frames,
    score_gif_candidate,
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


def _make_paletted_pixel(red: int, green: int, blue: int) -> Image.Image:
    image = Image.new("P", (1, 1), 0)
    image.putpalette([red, green, blue] + [0, 0, 0] * 255)
    return image


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


def test_resolve_effective_max_frames_uses_strategy_cap_for_frames() -> None:
    cap = resolve_effective_max_frames(
        source_frame_count=159,
        requested_max_frames=50,
        optimization_strategy="frames",
    )

    assert cap.requested_max_frames == 50
    assert cap.effective_max_frames == 159
    assert cap.frame_cap_mode == "strategy"


def test_resolve_effective_max_frames_applies_safety_limit() -> None:
    cap = resolve_effective_max_frames(
        source_frame_count=1000,
        requested_max_frames=50,
        optimization_strategy="frames",
    )

    assert cap.requested_max_frames == 50
    assert cap.effective_max_frames == GIF_FRAME_PRIORITY_SCAN_LIMIT == 300
    assert cap.frame_cap_mode == "safety"


@pytest.mark.parametrize("strategy", ["quality", "balanced"])
def test_resolve_effective_max_frames_keeps_user_cap_for_non_frame_strategies(
    strategy: str,
) -> None:
    cap = resolve_effective_max_frames(
        source_frame_count=159,
        requested_max_frames=50,
        optimization_strategy=strategy,
    )

    assert cap.requested_max_frames == 50
    assert cap.effective_max_frames == 50
    assert cap.frame_cap_mode == "user"


def test_step_candidates_document_old_and_effective_frame_caps() -> None:
    assert build_step_candidates(frame_count=159, max_frames=159)[0] == 1
    assert build_step_candidates(frame_count=159, max_frames=50)[0] == 4


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


def test_gif_strategy_weights_preserve_frames_default_priority() -> None:
    frames = get_gif_strategy_weights("frames")
    quality = get_gif_strategy_weights("quality")
    balanced = get_gif_strategy_weights("balanced")

    assert frames.frame == pytest.approx(0.70)
    assert frames.frame > frames.side > frames.color
    assert quality.side + quality.color > quality.frame
    assert quality.side > frames.side
    assert balanced.imbalance > frames.imbalance


def test_gif_strategy_scoring_changes_tradeoff_priority() -> None:
    frame_preserving_side_loss = {
        "side_loss": 0.5,
        "color_loss": 0.0,
        "frame_loss": 0.0,
    }
    side_preserving_frame_loss = {
        "side_loss": 0.0,
        "color_loss": 0.0,
        "frame_loss": 1.0,
    }

    frames = get_gif_strategy_weights("frames")
    quality = get_gif_strategy_weights("quality")

    assert score_gif_candidate(
        **frame_preserving_side_loss,
        weights=frames,
    ) < score_gif_candidate(
        **side_preserving_frame_loss,
        weights=frames,
    )
    assert score_gif_candidate(
        **side_preserving_frame_loss,
        weights=quality,
    ) < score_gif_candidate(
        **frame_preserving_side_loss,
        weights=quality,
    )


def test_frame_priority_candidate_generation_starts_with_full_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_frames = [
        Image.new("RGBA", (96, 96), (index % 256, index // 2, 255 - (index % 256), 255))
        for index in range(159)
    ]
    source_durations = [80] * len(source_frames)
    encoded_steps: list[int] = []

    monkeypatch.setattr(converter_core, "build_side_candidates", lambda target_side: [96])
    monkeypatch.setattr(converter_core, "GIF_COLOR_CANDIDATES", (128,))

    def fake_encode_gif(
        source_frames,
        source_durations,
        side,
        fit_mode,
        frame_step,
        colors,
    ) -> EncodeResult:
        encoded_steps.append(frame_step)
        return EncodeResult(
            data=b"x" * 80,
            format_name="GIF",
            side=side,
            colors=colors,
            frame_step=frame_step,
            frame_count=len(source_frames[::frame_step]),
        )

    monkeypatch.setattr(converter_core, "encode_gif", fake_encode_gif)

    result = convert_gif_frames(
        source_frames=source_frames,
        source_durations=source_durations,
        fit_mode="contain",
        target_side=96,
        max_bytes=100,
        max_frames=50,
        optimization_strategy="frames",
    )

    assert encoded_steps[:1] == [1]
    assert result.frame_step == 1
    assert result.frame_count == 159
    assert result.requested_max_frames == 50
    assert result.effective_max_frames == 159
    assert result.frame_cap_mode == "strategy"
    assert result.frame_reduction_reason == "none"


def test_non_frame_strategy_candidate_generation_keeps_user_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_frames = [
        Image.new("RGBA", (96, 96), (index % 256, index // 2, 255 - (index % 256), 255))
        for index in range(159)
    ]
    source_durations = [80] * len(source_frames)
    encoded_steps: list[int] = []

    monkeypatch.setattr(converter_core, "build_side_candidates", lambda target_side: [96])
    monkeypatch.setattr(converter_core, "GIF_COLOR_CANDIDATES", (128,))

    def fake_encode_gif(
        source_frames,
        source_durations,
        side,
        fit_mode,
        frame_step,
        colors,
    ) -> EncodeResult:
        encoded_steps.append(frame_step)
        return EncodeResult(
            data=b"x" * 80,
            format_name="GIF",
            side=side,
            colors=colors,
            frame_step=frame_step,
            frame_count=len(source_frames[::frame_step]),
        )

    monkeypatch.setattr(converter_core, "encode_gif", fake_encode_gif)

    result = convert_gif_frames(
        source_frames=source_frames,
        source_durations=source_durations,
        fit_mode="contain",
        target_side=96,
        max_bytes=100,
        max_frames=50,
        optimization_strategy="quality",
    )

    assert encoded_steps[:1] == [4]
    assert result.frame_step == 4
    assert result.requested_max_frames == 50
    assert result.effective_max_frames == 50
    assert result.frame_cap_mode == "user"
    assert result.frame_reduction_reason == "user-cap"


def test_frame_priority_reduction_reason_reports_slack_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_frames = [
        Image.new("RGBA", (96, 96), (index, 0, 255 - index, 255))
        for index in range(12)
    ]
    source_durations = [80] * len(source_frames)

    monkeypatch.setattr(converter_core, "build_side_candidates", lambda target_side: [96])
    monkeypatch.setattr(converter_core, "GIF_COLOR_CANDIDATES", (128,))

    def fake_encode_gif(
        source_frames,
        source_durations,
        side,
        fit_mode,
        frame_step,
        colors,
    ) -> EncodeResult:
        byte_size = 120 if frame_step == 1 else 80
        return EncodeResult(
            data=b"x" * byte_size,
            format_name="GIF",
            side=side,
            colors=colors,
            frame_step=frame_step,
            frame_count=len(source_frames[::frame_step]),
        )

    monkeypatch.setattr(converter_core, "encode_gif", fake_encode_gif)

    result = convert_gif_frames(
        source_frames=source_frames,
        source_durations=source_durations,
        fit_mode="contain",
        target_side=96,
        max_bytes=100,
        max_frames=50,
        optimization_strategy="frames",
    )

    assert result.frame_step == 2
    assert result.frame_cap_mode == "strategy"
    assert result.frame_reduction_reason == "slack-size"


def test_convert_gif_frames_strategy_reaches_candidate_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_frames = [
        Image.new("RGBA", (96, 96), (index * 30, 0, 255 - index * 30, 255))
        for index in range(4)
    ]
    source_durations = [80, 80, 80, 80]

    monkeypatch.setattr(converter_core, "build_side_candidates", lambda target_side: [96, 48])
    monkeypatch.setattr(converter_core, "build_step_candidates", lambda frame_count, max_frames: [2, 4])
    monkeypatch.setattr(converter_core, "GIF_COLOR_CANDIDATES", (128,))

    def fake_encode_gif(
        source_frames,
        source_durations,
        side,
        fit_mode,
        frame_step,
        colors,
    ) -> EncodeResult:
        byte_size = 80 if (side, frame_step, colors) in {(48, 2, 128), (96, 4, 128)} else 200
        return EncodeResult(
            data=b"x" * byte_size,
            format_name="GIF",
            side=side,
            colors=colors,
            frame_step=frame_step,
            frame_count=1,
        )

    monkeypatch.setattr(converter_core, "encode_gif", fake_encode_gif)

    frames_result = convert_gif_frames(
        source_frames=source_frames,
        source_durations=source_durations,
        fit_mode="contain",
        target_side=96,
        max_bytes=100,
        max_frames=50,
        optimization_strategy="frames",
    )
    quality_result = convert_gif_frames(
        source_frames=source_frames,
        source_durations=source_durations,
        fit_mode="contain",
        target_side=96,
        max_bytes=100,
        max_frames=50,
        optimization_strategy="quality",
    )

    assert (frames_result.side, frames_result.frame_step, frames_result.colors) == (48, 2, 128)
    assert (quality_result.side, quality_result.frame_step, quality_result.colors) == (96, 4, 128)


def test_collapse_identical_frames_keeps_palette_distinct_pixels() -> None:
    red_frame = _make_paletted_pixel(255, 0, 0)
    blue_frame = _make_paletted_pixel(0, 0, 255)

    frames, durations = collapse_identical_frames(
        [red_frame, blue_frame],
        [100, 120],
    )

    assert frames == [red_frame, blue_frame]
    assert durations == [100, 120]


def test_collapse_identical_frames_merges_rendered_duplicates() -> None:
    first_frame = _make_paletted_pixel(255, 0, 0)
    second_frame = _make_paletted_pixel(255, 0, 0)

    frames, durations = collapse_identical_frames(
        [first_frame, second_frame],
        [100, 120],
    )

    assert frames == [first_frame]
    assert durations == [220]
