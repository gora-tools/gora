FROM python:3.12-slim AS base

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source
COPY src/ src/
COPY README.md ./

# Install project
RUN uv sync --frozen --no-dev

# Default: run CLI
ENTRYPOINT ["uv", "run", "gora"]
