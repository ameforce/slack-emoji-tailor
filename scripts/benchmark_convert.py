from __future__ import annotations

import argparse
import io
import statistics
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.schemas import ConvertParams
from app.services.converter_adapter import convert_uploaded_image

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark Slack emoji conversion.")
    parser.add_argument("--input", type=Path, default=Path("test_set"))
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--max-kb", type=int, default=128)
    parser.add_argument("--size", type=str, default="auto")
    parser.add_argument("--fit", type=str, default="stretch")
    parser.add_argument("--max-frames", type=int, default=50)
    return parser.parse_args()


def load_files(input_dir: Path) -> list[tuple[str, bytes]]:
    if not input_dir.exists():
        return []

    samples: list[tuple[str, bytes]] = []
    for path in sorted(input_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        samples.append((path.name, path.read_bytes()))
    return samples


def generate_synthetic_samples() -> list[tuple[str, bytes]]:
    static = Image.new("RGBA", (280, 180), (255, 255, 255, 0))
    draw = ImageDraw.Draw(static)
    draw.rounded_rectangle((16, 16, 264, 164), radius=30, fill=(79, 70, 229, 255))
    draw.text((50, 72), "emoji", fill=(255, 255, 255, 255))

    static_stream = io.BytesIO()
    static.save(static_stream, format="PNG")

    frame_a = Image.new("RGBA", (128, 128), (255, 99, 132, 255))
    frame_b = Image.new("RGBA", (128, 128), (54, 162, 235, 255))
    gif_stream = io.BytesIO()
    frame_a.save(
        gif_stream,
        format="GIF",
        save_all=True,
        append_images=[frame_b],
        duration=[80, 80],
        loop=0,
        optimize=True,
    )

    return [
        ("synthetic_static.png", static_stream.getvalue()),
        ("synthetic_anim.gif", gif_stream.getvalue()),
    ]


def run_benchmark(samples: list[tuple[str, bytes]], params: ConvertParams, repeat: int) -> None:
    print(f"Samples: {len(samples)}")
    print(f"Repeat per sample: {repeat}")
    print("-" * 68)
    print(f"{'sample':30} {'mean(ms)':>10} {'p95(ms)':>10} {'size(KB)':>10}")
    print("-" * 68)

    total_times: list[float] = []
    for name, payload in samples:
        elapsed_ms: list[float] = []
        last_size = 0
        for _ in range(max(1, repeat)):
            start = time.perf_counter()
            result = convert_uploaded_image(payload, name, params)
            elapsed_ms.append((time.perf_counter() - start) * 1000.0)
            last_size = result.metadata.byte_size

        total_times.extend(elapsed_ms)
        mean_ms = statistics.fmean(elapsed_ms)
        p95_ms = statistics.quantiles(elapsed_ms, n=20)[18] if len(elapsed_ms) >= 2 else elapsed_ms[0]
        print(f"{name:30} {mean_ms:10.2f} {p95_ms:10.2f} {last_size / 1024:10.2f}")

    print("-" * 68)
    print(
        "overall: "
        f"mean={statistics.fmean(total_times):.2f}ms, "
        f"min={min(total_times):.2f}ms, max={max(total_times):.2f}ms"
    )


def main() -> int:
    args = parse_args()
    params = ConvertParams(
        max_kb=args.max_kb,
        size=args.size,
        fit=args.fit,
        max_frames=args.max_frames,
    )

    samples = load_files(args.input)
    if not samples:
        print(
            f'No image files found in "{args.input}". '
            "Running benchmark with synthetic samples instead."
        )
        samples = generate_synthetic_samples()

    run_benchmark(samples=samples, params=params, repeat=args.repeat)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
