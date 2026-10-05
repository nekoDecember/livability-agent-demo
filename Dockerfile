# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85ab0f263ef12f492d8476aa3a2e4e1e333f7d67fbdd923d00a506a516a AS uv

FROM python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217

COPY --from=uv /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    HOME=/tmp/livability-home \
    PATH="/app/.venv/bin:$PATH" \
    API_HOST=0.0.0.0 \
    API_PORT=8091 \
    OUTPUTS_DIR=/data/outputs \
    OPEN_DATA_DIR=/data/open-data

RUN apt-get update \
    && apt-get install --no-install-recommends -y tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev --no-editable \
    && useradd --create-home --home-dir /home/livability --uid 10001 livability \
    && mkdir -p /data/outputs /data/open-data "$HOME" \
    && chown -R livability:livability /data "$HOME"

USER livability
EXPOSE 8091
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["livability-api"]
