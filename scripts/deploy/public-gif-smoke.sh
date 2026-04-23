#!/usr/bin/env bash
set -Eeuo pipefail

SMOKE_SCOPE="${SMOKE_SCOPE:-external}"
EXTERNAL_PROOF="${EXTERNAL_PROOF:-true}"
SMOKE_EVIDENCE_DIR="${SMOKE_EVIDENCE_DIR:-deploy-evidence}"
PUBLIC_HEALTHCHECK_TIMEOUT_SECONDS="${PUBLIC_HEALTHCHECK_TIMEOUT_SECONDS:-15}"
EXPECTED_SOURCE_FRAMES=159

if [ "$SMOKE_EVIDENCE_DIR" = "deploy-evidence" ]; then
  mkdir -p deploy-evidence
else
  mkdir -p "$SMOKE_EVIDENCE_DIR"
fi

BASE_URL="${BASE_URL:-}"
PUBLIC_HEALTHCHECK_URL="${PUBLIC_HEALTHCHECK_URL:-}"
if [ -z "$BASE_URL" ]; then
  PUBLIC_HEALTHCHECK_URL="${PUBLIC_HEALTHCHECK_URL:-https://dev.emoji.enmsoftware.com/healthz}"
  BASE_URL="${PUBLIC_HEALTHCHECK_URL%/healthz}"
  if [ "$BASE_URL" = "$PUBLIC_HEALTHCHECK_URL" ]; then
    BASE_URL="${PUBLIC_HEALTHCHECK_URL%/}"
  fi
fi
BASE_URL="${BASE_URL%/}"
PUBLIC_HEALTHCHECK_URL="${PUBLIC_HEALTHCHECK_URL:-${BASE_URL}/healthz}"

cat > "$SMOKE_EVIDENCE_DIR/public-smoke-scope.txt" <<EOF
scope=${SMOKE_SCOPE}
external-proof=${EXTERNAL_PROOF}
base_url=${BASE_URL}
public_healthcheck_url=${PUBLIC_HEALTHCHECK_URL}
EOF
cat > "$SMOKE_EVIDENCE_DIR/public-smoke-summary.txt" <<EOF
scope=${SMOKE_SCOPE}
external-proof=${EXTERNAL_PROOF}
route=${PUBLIC_HEALTHCHECK_URL}
EOF

if command -v uv >/dev/null 2>&1; then
  PYTHON_CMD=(uv run python)
else
  PYTHON_CMD=(python3)
fi

GIF_FIXTURE="$SMOKE_EVIDENCE_DIR/frame-priority-smoke.gif"
GIF_FRAME_PRIORITY_SCAN_LIMIT="$("${PYTHON_CMD[@]}" - <<'PY'
from app.services.converter_core import GIF_FRAME_PRIORITY_SCAN_LIMIT
print(GIF_FRAME_PRIORITY_SCAN_LIMIT)
PY
)"
EXPECTED_EFFECTIVE_FRAMES="$EXPECTED_SOURCE_FRAMES"
if [ "$EXPECTED_SOURCE_FRAMES" -gt "$GIF_FRAME_PRIORITY_SCAN_LIMIT" ]; then
  EXPECTED_EFFECTIVE_FRAMES="$GIF_FRAME_PRIORITY_SCAN_LIMIT"
fi
export SMOKE_EVIDENCE_DIR EXPECTED_SOURCE_FRAMES GIF_FRAME_PRIORITY_SCAN_LIMIT EXPECTED_EFFECTIVE_FRAMES

"${PYTHON_CMD[@]}" - <<'PY'
import os
from pathlib import Path

from PIL import Image, ImageDraw

evidence_dir = Path(os.environ["SMOKE_EVIDENCE_DIR"])
frame_count = int(os.environ["EXPECTED_SOURCE_FRAMES"])
frames = []
for index in range(frame_count):
    image = Image.new(
        "RGBA",
        (64, 64),
        ((index * 3) % 256, (index * 5) % 256, (index * 7) % 256, 255),
    )
    draw = ImageDraw.Draw(image)
    draw.rectangle((index % 32, index % 32, 63, 63), outline=(255, 255, 255, 255))
    draw.text((4, 4), str(index % 100), fill=(255, 255, 255, 255))
    frames.append(image.convert("P", palette=Image.ADAPTIVE, colors=64))

evidence_dir.mkdir(exist_ok=True)
frames[0].save(
    evidence_dir / "frame-priority-smoke.gif",
    save_all=True,
    append_images=frames[1:],
    duration=20,
    loop=0,
    disposal=2,
    optimize=False,
)
PY

cat > "$SMOKE_EVIDENCE_DIR/public-frame-priority-expected.txt" <<EOF
expected_source_frames=${EXPECTED_SOURCE_FRAMES}
gif_frame_priority_scan_limit=${GIF_FRAME_PRIORITY_SCAN_LIMIT}
expected_effective_frames=${EXPECTED_EFFECTIVE_FRAMES}
EOF

run_curl_capture_status() {
  local status_file="$1"
  shift
  local status exit_code
  set +e
  status="$(curl "$@" 2>> "$SMOKE_EVIDENCE_DIR/deploy-script.log")"
  exit_code=$?
  set -e
  printf '%s\n' "${status:-000}" > "$status_file"
  if [ "$exit_code" -ne 0 ]; then
    echo "curl failed exit_code=${exit_code} status=${status:-000} status_file=${status_file}" >> "$SMOKE_EVIDENCE_DIR/deploy-script.log"
    return "$exit_code"
  fi
  printf '%s' "$status"
}

health_status="$(run_curl_capture_status "$SMOKE_EVIDENCE_DIR/public-health-status.txt" \
  --show-error --silent --location --max-time "$PUBLIC_HEALTHCHECK_TIMEOUT_SECONDS" \
  --output "$SMOKE_EVIDENCE_DIR/public-health-response.txt" \
  --write-out '%{http_code}' \
  "$PUBLIC_HEALTHCHECK_URL")"
test "$health_status" = "200"

index_status="$(run_curl_capture_status "$SMOKE_EVIDENCE_DIR/public-index-status.txt" \
  --show-error --silent --location --max-time "$PUBLIC_HEALTHCHECK_TIMEOUT_SECONDS" \
  --output "$SMOKE_EVIDENCE_DIR/public-index-response.html" \
  --write-out '%{http_code}' \
  "$BASE_URL/")"
test "$index_status" = "200"
grep -q 'id="max-frames"' "$SMOKE_EVIDENCE_DIR/public-index-response.html"
grep -q 'max="50"' "$SMOKE_EVIDENCE_DIR/public-index-response.html"

inspect_status="$(run_curl_capture_status "$SMOKE_EVIDENCE_DIR/public-inspect-status.txt" \
  --show-error --silent --location --max-time 30 \
  --output "$SMOKE_EVIDENCE_DIR/public-inspect-response.json" \
  --write-out '%{http_code}' \
  -F "file=@${GIF_FIXTURE};type=image/gif" \
  "$BASE_URL/api/inspect")"
test "$inspect_status" = "200"

"${PYTHON_CMD[@]}" - <<'PY' > "$SMOKE_EVIDENCE_DIR/public-inspect-summary.json"
import json
import os
from pathlib import Path

evidence_dir = Path(os.environ["SMOKE_EVIDENCE_DIR"])
payload = json.loads((evidence_dir / "public-inspect-response.json").read_text())
expected = int(os.environ["EXPECTED_SOURCE_FRAMES"])
summary = {
    "http_status": (evidence_dir / "public-inspect-status.txt").read_text().strip(),
    "format_name": payload.get("format_name"),
    "frame_count": payload.get("frame_count"),
    "is_animated": payload.get("is_animated"),
    "expected_source_frames": expected,
}
print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
if payload.get("frame_count") != expected or payload.get("is_animated") is not True:
    raise SystemExit(f"Unexpected inspect metadata: {summary}")
PY

frames_status="$(run_curl_capture_status "$SMOKE_EVIDENCE_DIR/public-convert-frames-status.txt" \
  --show-error --silent --location --max-time 60 \
  --dump-header "$SMOKE_EVIDENCE_DIR/public-convert-frames-headers.txt" \
  --output "$SMOKE_EVIDENCE_DIR/public-convert-frames.gif" \
  --write-out '%{http_code}' \
  -F "file=@${GIF_FIXTURE};type=image/gif" \
  -F "max_kb=128" \
  -F "size=auto" \
  -F "fit=cover" \
  -F "max_frames=${EXPECTED_EFFECTIVE_FRAMES}" \
  -F "optimization_strategy=frames" \
  "$BASE_URL/api/convert")"
test "$frames_status" = "200"
grep -Eiq '^X-Optimization-Strategy:[[:space:]]*frames([[:space:]]|$)' "$SMOKE_EVIDENCE_DIR/public-convert-frames-headers.txt"
grep -Eiq '^X-Requested-Max-Frames:[[:space:]]*'"$EXPECTED_EFFECTIVE_FRAMES"'([[:space:]]|$)' "$SMOKE_EVIDENCE_DIR/public-convert-frames-headers.txt"
grep -Eiq '^X-Effective-Max-Frames:[[:space:]]*'"$EXPECTED_EFFECTIVE_FRAMES"'([[:space:]]|$)' "$SMOKE_EVIDENCE_DIR/public-convert-frames-headers.txt"
grep -Eiq '^X-Frame-Cap-Mode:[[:space:]]*' "$SMOKE_EVIDENCE_DIR/public-convert-frames-headers.txt"
grep -Eiq '^X-Frame-Reduction-Reason:[[:space:]]*' "$SMOKE_EVIDENCE_DIR/public-convert-frames-headers.txt"
frames_bytes="$(wc -c < "$SMOKE_EVIDENCE_DIR/public-convert-frames.gif" | tr -d ' ')"
printf 'http_status=%s\nresult_bytes=%s\nmax_bytes=%s\nexpected_effective_frames=%s\n' \
  "$frames_status" "$frames_bytes" "$((128 * 1024))" "$EXPECTED_EFFECTIVE_FRAMES" \
  > "$SMOKE_EVIDENCE_DIR/public-convert-frames-summary.txt"
test "$frames_bytes" -le $((128 * 1024))

tight_status="$(run_curl_capture_status "$SMOKE_EVIDENCE_DIR/public-convert-tight-status.txt" \
  --show-error --silent --location --max-time 60 \
  --dump-header "$SMOKE_EVIDENCE_DIR/public-convert-tight-headers.txt" \
  --output "$SMOKE_EVIDENCE_DIR/public-convert-tight.gif" \
  --write-out '%{http_code}' \
  -F "file=@${GIF_FIXTURE};type=image/gif" \
  -F "max_kb=1" \
  -F "size=auto" \
  -F "fit=cover" \
  -F "max_frames=${EXPECTED_EFFECTIVE_FRAMES}" \
  -F "optimization_strategy=frames" \
  "$BASE_URL/api/convert")"
test "$tight_status" = "200"
grep -Eiq '^X-Frame-Cap-Mode:[[:space:]]*' "$SMOKE_EVIDENCE_DIR/public-convert-tight-headers.txt"
grep -Eiq '^X-Frame-Reduction-Reason:[[:space:]]*budget-limit([[:space:]]|$)' "$SMOKE_EVIDENCE_DIR/public-convert-tight-headers.txt"
grep -Eiq '^X-Gif-Search-Exhausted:[[:space:]]*true([[:space:]]|$)' "$SMOKE_EVIDENCE_DIR/public-convert-tight-headers.txt"
grep -Eiq '^X-Target-Reached:[[:space:]]*false([[:space:]]|$)' "$SMOKE_EVIDENCE_DIR/public-convert-tight-headers.txt"
tight_bytes="$(wc -c < "$SMOKE_EVIDENCE_DIR/public-convert-tight.gif" | tr -d ' ')"
printf 'http_status=%s\nresult_bytes=%s\nmax_bytes=%s\nexpected_effective_frames=%s\n' \
  "$tight_status" "$tight_bytes" "$((1 * 1024))" "$EXPECTED_EFFECTIVE_FRAMES" \
  > "$SMOKE_EVIDENCE_DIR/public-convert-tight-summary.txt"

echo "Public URL/API GIF smoke passed for ${BASE_URL} (scope=${SMOKE_SCOPE}, external-proof=${EXTERNAL_PROOF})."
