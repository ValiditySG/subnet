ARG BASE_IMAGE=python@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6
FROM $BASE_IMAGE AS builder
COPY --from=ghcr.io/astral-sh/uv@sha256:e49fde5daf002023f0a2e2643861ce9ca8a8da5b73d0e6db83ef82ff99969baf /uv /bin/uv
WORKDIR /build/miner
ENV UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
COPY protocol/ /build/protocol/
COPY miner/pyproject.toml miner/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-editable
COPY miner/src/ ./src/
RUN uv sync --frozen --no-dev --no-editable
FROM $BASE_IMAGE AS runtime
ENV PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
COPY --from=builder /opt/venv /opt/venv
RUN groupadd --gid 10001 validity \
    && useradd --uid 10001 --gid 10001 --no-create-home validity \
    && mkdir -p /var/lib/validity-miner \
    && chown validity:validity /var/lib/validity-miner
USER 10001:10001
WORKDIR /var/lib/validity-miner
CMD ["miner"]
