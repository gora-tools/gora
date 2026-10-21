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

FROM ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 AS uv

FROM python:3.12-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS build
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
# pyproject.toml fails the build instead of being re-resolved. The one dependency group is
# `build`, the build backend, locked with hashes like every other package: the image carries
# no test or check tool.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-default-groups --group build --no-install-project

# The project, installed into the virtual environment rather than linked to the source, and
# built by the locked backend there: an isolated build would resolve the backend from the
# index instead (TESTING rule 3).
COPY src/ src/
# What pyproject.toml's license-files names: the installed distribution carries both.
COPY LICENSE NOTICE ./
RUN uv sync --locked --no-default-groups --group build --no-editable \
        --no-build-isolation-package gora
# The backend leaves the image: an exact sync without the group removes it.
RUN uv sync --locked --no-default-groups --no-editable

FROM python:3.12-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

# git, which the Git adapter runs at /usr/bin/git (GORA_GIT, ADR-006), from the Debian
# snapshot deployments/docker/debian.sources names, which replaces the base's sources: the live
# archive is never read, and the build refuses any other apt source. apt-get update fails on any
# error. snapshot.debian.org serves every file by a redirect to its content address, so apt
# cannot tell an exact import from a time; debian.lock can: the Release files apt fetched must
# be exactly the ones it pins, and they hold the hashes of the indices, which hold the hashes of
# the packages apt checks. GIT_VERSION asserts the version the snapshot holds. No security
# update is taken until the snapshot is moved (ADR-011, TESTING rule 3).
ARG GIT_VERSION=1:2.47.3-0+deb13u1
COPY deployments/docker/debian.sources /etc/apt/sources.list.d/debian.sources
COPY deployments/docker/debian.lock /etc/apt/debian.lock
RUN test "$(ls -A /etc/apt/sources.list.d)" = debian.sources && test ! -e /etc/apt/sources.list \
    && apt-get update --error-on=any -o Acquire::Retries=5 \
    && python -c 'import glob, hashlib, sys; \
        want = sorted(line.split()[0] for line in open(sys.argv[1], encoding="utf-8") if line.strip() and not line.startswith("#")); \
        got = sorted(hashlib.sha256(open(path, "rb").read()).hexdigest() for path in glob.glob("/var/lib/apt/lists/*_InRelease")); \
        sys.exit(None if got == want else "apt fetched other Release files than debian.lock pins: " + " ".join(got))' \
        /etc/apt/debian.lock \
    && apt-get install --yes --no-install-recommends -o Acquire::Retries=5 "git=$GIT_VERSION" \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 gora \
    && useradd --system --uid 10001 --gid gora --no-create-home \
       --home-dir /nonexistent --shell /usr/sbin/nologin gora

COPY --from=build /app/.venv /app/.venv

ARG GORA_REVISION
ENV GORA_REVISION=$GORA_REVISION
LABEL org.opencontainers.image.revision=$GORA_REVISION
# GORA's own license; the base image and the dependencies carry their own.
LABEL org.opencontainers.image.licenses="Apache-2.0"

USER 10001:10001

ENTRYPOINT ["/app/.venv/bin/gora"]
