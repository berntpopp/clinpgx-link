# syntax=docker/dockerfile:1
# Multi-stage Dockerfile for clinpgx-link.

FROM python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV VIRTUAL_ENV="/opt/venv" \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /home/app/web
COPY uv.lock pyproject.toml README.md ./

# Reproducible build: pin uv to a digest-addressed image (fleet-shared anchor)
# instead of a floating pip-bootstrapped installer upgrade (F-19).
COPY --from=ghcr.io/astral-sh/uv:0.8.7@sha256:1e26f9a868360eeb32500a35e05787ffff3402f01a8dc8168ef6aee44aef0aab /uv /usr/local/bin/uv

RUN uv sync --frozen --no-dev --active --no-install-project


FROM python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5 AS prepared

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/home/app/web" \
    CLINPGX_MCP_HOST=0.0.0.0 \
    CLINPGX_MCP_PORT=8000 \
    CLINPGX_CACHE_ROOT=/cache/clinpgx-api \
    CLINPGX_DATA_ROOT=/data

RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

RUN groupadd --system --gid 999 app && \
    useradd --system --uid 999 --gid app --home /home/app --create-home app && \
    mkdir -p /cache/clinpgx-api /data && \
    chown -R app:app /cache /data

COPY --from=builder /opt/venv /opt/venv

WORKDIR /home/app/web

COPY --chown=app:app ./clinpgx_link ./clinpgx_link
COPY --chown=app:app ./pyproject.toml ./README.md ./

# Install the project itself (no deps -- already provided by the builder venv)
# so the `clinpgx-link` console script is on PATH.
RUN pip install --no-deps .

RUN rm -rf /root/.cache/pip \
    /usr/local/lib/python*/ensurepip \
    /usr/local/lib/python*/site-packages/pip \
    /usr/local/lib/python*/site-packages/pip-*.dist-info \
    /usr/local/lib/python*/site-packages/setuptools \
    /usr/local/lib/python*/site-packages/setuptools-*.dist-info \
    /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14 \
    /opt/venv/lib/python*/site-packages/pip \
    /opt/venv/lib/python*/site-packages/pip-*.dist-info \
    /opt/venv/lib/python*/site-packages/setuptools \
    /opt/venv/lib/python*/site-packages/setuptools-*.dist-info \
    /opt/venv/bin/pip /opt/venv/bin/pip3 /opt/venv/bin/pip3.14 \
    && find / -xdev -type d -name __pycache__ -prune -exec rm -rf '{}' + \
    && find / -xdev -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
RUN find / -xdev -perm /6000 -exec chmod a-s '{}' + \
    && test -z "$(find / -xdev -perm /6000 -print -quit)"
RUN actual="$(find / -xdev -type f -links +1 -printf '%i %p\n' | sort -k2,2)" \
    && expected="$(printf '%s %s\n%s %s\n' \
         "$(stat -c '%i' /usr/bin/perl)" /usr/bin/perl \
         "$(stat -c '%i' /usr/bin/perl5.40.1)" /usr/bin/perl5.40.1 | sort -k2,2)" \
    && { test "$actual" = "$expected" || { printf 'unexpected hardlinks:\n%s\n' "$actual"; exit 1; }; } \
    && rm -f /usr/bin/perl5.40.1

FROM scratch AS production
COPY --from=prepared / /

ARG APP_VERSION=0.0.0
ARG VCS_REF=unknown
ARG BUILD_DATE=1970-01-01T00:00:00Z
LABEL org.opencontainers.image.title="clinpgx-link" \
      org.opencontainers.image.description="GeneFoundry MCP server for public ClinPGx data and source evidence" \
      org.opencontainers.image.url="https://github.com/berntpopp/clinpgx-link" \
      org.opencontainers.image.source="https://github.com/berntpopp/clinpgx-link" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.vendor="GeneFoundry"
ENV VCS_REF="${VCS_REF}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/home/app/web" \
    CLINPGX_MCP_HOST=0.0.0.0 \
    CLINPGX_MCP_PORT=8000 \
    CLINPGX_CACHE_ROOT=/cache/clinpgx-api \
    CLINPGX_DATA_ROOT=/data

WORKDIR /home/app/web

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["clinpgx-link", "serve", "--transport", "unified", "--host", "0.0.0.0", "--port", "8000"]
