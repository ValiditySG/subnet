ARG BASE_IMAGE=python@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

FROM $BASE_IMAGE AS builder

COPY --from=ghcr.io/astral-sh/uv@sha256:e49fde5daf002023f0a2e2643861ce9ca8a8da5b73d0e6db83ef82ff99969baf /uv /uvx /bin/

WORKDIR /build/validator

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY protocol/ /build/protocol/
COPY validator/pyproject.toml validator/uv.lock ./

RUN uv sync --frozen --no-install-project --no-dev --no-editable

COPY validator/src/ ./src/

RUN uv sync --frozen --no-dev --no-editable


FROM $BASE_IMAGE AS runtime

ENV PYTHONUNBUFFERED=1
ENV PATH="/opt/venv/bin:$PATH"

COPY --from=builder /opt/venv /opt/venv

RUN groupadd --gid 10001 validity \
    && useradd --uid 10001 --gid 10001 --no-create-home validity \
    && mkdir -p /var/lib/validity \
    && chown validity:validity /var/lib/validity
USER 10001:10001
WORKDIR /var/lib/validity

CMD ["validator"]
