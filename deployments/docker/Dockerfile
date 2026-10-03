# Gate-defining file (ADR-0016 rule 7). The release image is immutable at run time
# (ADR-011, amendment 2026-10-02): it installs uv.lock exactly at build time, and at run
# time it runs the installed CLI, with no uv, no sync, no network and no write.
#
# Every base is pinned by the digest of its multi-platform index (ADR-021): one Dockerfile
# builds linux/amd64 and linux/arm64, each on its own runner. Dependabot (docker) proposes
# the updates; uv's version is the one pyproject.toml requires (tests/unit/test_build_pins.py).

FROM ghcr.io/astral-sh/uv:0.12.22@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc AS uv

FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016 AS build

COPY --from=uv /uv /usr/local/bin/uv

# Compile bytecode now: a read-only root filesystem cannot take it later. Use the image's
# own interpreter, which the runtime stage has at the same path; .python-version names its
# patch version, so a base image with another one fails here.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON_PREFERENCE=only-system

WORKDIR /app

# Dependencies first, for the layer cache. --locked: a lockfile that does not match
# pyproject.toml fails the build instead of being re-resolved. No dependency group: the
# image carries no test or check tool.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-default-groups --no-install-project

# The project, installed into the virtual environment rather than linked to the source.
COPY src/ src/
COPY README.md ./
RUN uv sync --locked --no-default-groups --no-editable

FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016

RUN groupadd --system --gid 10001 gora \
    && useradd --system --uid 10001 --gid gora --no-create-home \
       --home-dir /nonexistent --shell /usr/sbin/nologin gora

COPY --from=build /app/.venv /app/.venv

USER 10001:10001

ENTRYPOINT ["/app/.venv/bin/gora"]
