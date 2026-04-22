FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev

COPY . .
RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime

ARG SLACK_EMOJI_TAILOR_VERSION=""

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV SLACK_EMOJI_TAILOR_VERSION=${SLACK_EMOJI_TAILOR_VERSION}

COPY --from=builder /app/.venv /app/.venv
COPY . .

EXPOSE 8000

CMD ["slack-emoji-tailor", "--host", "0.0.0.0", "--port", "8000"]
