# Multi-stage Dockerfile for self-hosting Omakase as a web service.
# Build:  docker build -t omakase:latest .
# Run the base guest-only stack with Compose. Add compose.production.yaml and
# its protected Lite keyring for encrypted member-saved provider keys.

FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip build && \
    python -m build --wheel --outdir /wheels

FROM python:3.12-slim AS runtime
ARG OMAKASE_SOURCE_COMMIT=development
LABEL org.opencontainers.image.source="https://github.com/HeyiTzSenpai/omakase"
LABEL org.opencontainers.image.description="An LLM-powered sommelier for anime"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.revision="${OMAKASE_SOURCE_COMMIT}"

RUN useradd --create-home --uid 1000 omakase && \
    mkdir -p /home/omakase/data/lite && \
    chown -R omakase:omakase /home/omakase/data
WORKDIR /home/omakase

COPY --from=builder /wheels/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -rf /tmp/*.whl

USER omakase
ENV OMAKASE_PORT=8765
ENV OMAKASE_SOURCE_COMMIT=${OMAKASE_SOURCE_COMMIT}
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/').read()" || exit 1

CMD ["omakase", "web", "--host", "0.0.0.0"]
