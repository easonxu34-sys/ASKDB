FROM ghcr.io/astral-sh/uv:0.11.29 AS uv

FROM python:3.13-slim AS python-base

ENV PYTHONUNBUFFERED=1

# Some networks return intermittent 502/404 responses for Debian’s HTTP mirror.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources

FROM python-base AS builder

# mysqlclient is locked to a source distribution on Linux.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        pkg-config \
        default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=120 \
    UV_HTTP_RETRIES=5 \
    UV_CONCURRENT_DOWNLOADS=4

WORKDIR /app/askdb-agent

COPY askdb-agent/pyproject.toml askdb-agent/uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY askdb-agent/src ./src

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

FROM python-base AS runtime

# Keep the client shared library used by the compiled MySQL extension.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmariadb3 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app/data

WORKDIR /app/askdb-agent

COPY --from=builder /app/askdb-agent /app/askdb-agent

ENV PATH="/app/askdb-agent/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
