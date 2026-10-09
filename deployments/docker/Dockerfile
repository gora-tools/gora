# Gate-defining file (ADR-0016 rule 7). The release image is immutable at run time
# (ADR-011, amendment 2026-10-02): it installs uv.lock exactly at build time, and at run
# time it runs the installed CLI, with no uv, no sync, no network and no write.
#
# Every base is pinned by the digest of its multi-platform index (ADR-021): one Dockerfile
# builds linux/amd64 and linux/arm64, each on its own runner. Dependabot (docker) proposes
# the updates.
#
# This file declares the exact uv and the exact Python where a result is produced: the uv
# stage below, and PYTHON_VERSION in the build stage. CI installs exactly these two for its
# checks, read by scripts/gate/versions.sh; nothing types them a second time
# (tests/unit/test_build_pins.py).
#
# The image carries its source commit, never its version (L9, ADR-022 section 3): one digest
# is first X.Y.Z-rc and then X.Y.Z (ADR-024). The build passes the commit it builds as the build
# argument GORA_REVISION; the image sets it as the environment variable GORA_REVISION, which
# `gora version` prints, and as the label org.opencontainers.image.revision. The version is
# set where the image is deployed, as GORA_VERSION.

FROM ghcr.io/astral-sh/uv:0.12.24@sha256:3af4716e991d6956a41e573eab705d0ee08500cd829ed30293eb8472f372c65a AS uv

FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016 AS build
# The exact Python of the release image: the interpreter of the base above, which the
# runtime stage has too. A base with another one fails the build here.
ARG PYTHON_VERSION=3.12.15
RUN got=$(python -c 'import platform; print(platform.python_version())') \
    && if [ "$got" != "$PYTHON_VERSION" ]; then \
         echo "the base image's Python is $got, PYTHON_VERSION declares $PYTHON_VERSION" >&2; \
         exit 1; \
       fi

# The commit this image is built from: a full commit SHA, or the build fails here.
ARG GORA_REVISION
RUN python -c 'import re, sys; sys.exit(re.fullmatch("[0-9a-f]{40}", sys.argv[1]) is None)' \
        "$GORA_REVISION" \
    || { echo "GORA_REVISION must be the full SHA of the commit built, got '$GORA_REVISION'" >&2; \
         exit 1; }

COPY --from=uv /uv /usr/local/bin/uv

# Compile bytecode now: a read-only root filesystem cannot take it later. Use the image's
# own interpreter, which the runtime stage has at the same path.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON_PREFERENCE=only-system

WORKDIR /app

# Dependencies first, for the layer cache. --locked: a lockfile that does not match
# pyproject.toml fails the build instead of being re-resolved. No dependency group: the
# image carries no test or check tool.
COPY pyproject.toml uv.lock ./
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

ARG GORA_REVISION
ENV GORA_REVISION=$GORA_REVISION
LABEL org.opencontainers.image.revision=$GORA_REVISION

USER 10001:10001

ENTRYPOINT ["/app/.venv/bin/gora"]
