FROM ghcr.io/astral-sh/uv:0.11.29 AS uv

FROM python:3.13-slim

COPY --from=uv /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app/askdb-agent

COPY askdb-agent/pyproject.toml askdb-agent/uv.lock ./
COPY askdb-agent/src ./src

RUN uv sync --locked --no-dev \
    && mkdir -p /app/data

ENV PATH="/app/askdb-agent/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
