FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp/livability-home \
    API_HOST=0.0.0.0 \
    API_PORT=8091 \
    OUTPUTS_DIR=/data/outputs

RUN apt-get update \
    && apt-get install --no-install-recommends -y tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && useradd --create-home --home-dir /home/livability --uid 10001 livability \
    && mkdir -p /data/outputs "$HOME" \
    && chown -R livability:livability /data "$HOME"

USER livability
EXPOSE 8091
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["livability-api"]
