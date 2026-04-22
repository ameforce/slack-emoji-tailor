FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev

COPY . .
RUN uv sync --frozen --no-dev
ARG APP_GIT_TAG_VERSION=""
RUN test -n "$APP_GIT_TAG_VERSION" || (echo "APP_GIT_TAG_VERSION is required" >&2; exit 1)
RUN printf '%s\n' "$APP_GIT_TAG_VERSION" > app/_git_version

FROM python:3.12-slim AS runtime

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY --from=builder /app/.venv /app/.venv
COPY . .
COPY --from=builder /app/app/_git_version /app/app/_git_version

EXPOSE 8000

CMD ["slack-emoji-tailor", "--host", "0.0.0.0", "--port", "8000"]
