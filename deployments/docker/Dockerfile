# Gate-defining file (ADR-0016 rule 7). The release image is immutable at run time
# (ADR-011, amendment 2026-10-02): it installs uv.lock exactly at build time, and at run
# time it runs the installed CLI, with no uv, no sync, no network and no write.
#
# Every base is pinned by the digest of its multi-platform index (ADR-021): one Dockerfile
# builds linux/amd64 and linux/arm64, each on its own runner. Dependabot (docker) proposes
# the update of the uv stage.
#
# Both the build and the runtime stage start from GORA's own base image (ADR-011, amendment
# 2026-11-07): Debian, Python and git, built from deployments/docker/base.Dockerfile by a
# person running .github/workflows/base.yml on main. A build of this file reaches GHCR and
# PyPI and nothing else: no Docker Hub, no snapshot.debian.org, and no package install here.
# Moving the digest below is a change of an input of the artifact (L8), and a base is applied
# by running that workflow and moving the digest in a pull request.
#
# Four stages: `uv` (the pinned uv), `build` (installs uv.lock into a virtual environment),
# `runtime` (the image that is pushed: that environment on the base) and `test` (the product's
# git tests, run on the runtime stage's own git; never pushed, and no input of the artifact).
# A build of the pushed image names its target, `runtime` (.github/actions/build-image,
# make docker-build): the last stage of the file is not the image. The stage `test` is built
# and run before it (ADR-021 section 4).
#
# The exact Python and the exact git are declared in base.Dockerfile, and the exact uv in the
# uv stage below. CI installs the uv and the Python for its checks, read by
# scripts/gate/versions.sh; nothing types them a second time (tests/unit/test_build_pins.py).
# make image-smoke checks both in the image built from this file.
#
# The image carries its source commit, never its version (L9, ADR-022 section 3): one digest
# is first X.Y.Z-rc and then X.Y.Z (ADR-024). The build passes the commit it builds as the build
# argument GORA_REVISION; the image sets it as the environment variable GORA_REVISION, which
# `gora version` prints, and as the label org.opencontainers.image.revision. The version is
# set where the image is deployed, as GORA_VERSION.

FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

FROM ghcr.io/gora-tools/gora-base@sha256:42b5ad1d1ecc2f4aea154e89d3a63e9e536e324ab56cfca5436c054a240cd45a AS build

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

FROM ghcr.io/gora-tools/gora-base@sha256:42b5ad1d1ecc2f4aea154e89d3a63e9e536e324ab56cfca5436c054a240cd45a AS runtime

# git is the base's: the Debian package the Git adapter runs at /usr/bin/git (GORA_GIT,
# ADR-006), at the version base.Dockerfile declares as GIT_VERSION.

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

# The test stage: the tests that run the product against git, on this image's own git, as this
# image's user. It is built and run before every push and is never pushed: nothing of it reaches
# `runtime`, and the build of the pushed image names its target. It adds uv and the test group
# of the same uv.lock, and the test sources; the venv is copied over itself owned by the user,
# so that the sync needs no other. Its list of modules is held by tests/unit/test_build_pins.py
# against the modules that run git; the rest of the suite needs more than this image has (a
# git index, make, docker, the extras), and runs on the platform's runner (TESTING rule 1).
FROM runtime AS test

COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PYTHON_PREFERENCE=only-system \
    PYTHONDONTWRITEBYTECODE=1 HOME=/tmp/home
WORKDIR /app
COPY --from=runtime --chown=10001:10001 /app/.venv /app/.venv
COPY pyproject.toml uv.lock .python-version ./
RUN mkdir "$HOME" \
    && uv sync --locked --inexact --no-default-groups --group test --no-install-project
COPY tests/ tests/
RUN /app/.venv/bin/python -m pytest -p no:cacheprovider \
        tests/integration/test_git_adapter.py \
        tests/integration/test_identifiers_against_git.py \
        tests/integration/test_input_globs_against_git.py \
        tests/integration/test_tag_message_against_git.py
