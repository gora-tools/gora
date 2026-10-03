# Gate-defining file (ADR-0016 rule 7). The container images the gates run, pinned by the
# digest of their multi-platform index. Never built: scripts/gate/image.sh prints one by its
# stage name, and the Makefile and CI take it from there. The file exists in this form so
# that Dependabot (docker), which reads every *Dockerfile, proposes their updates.
#
# linux-gate: make verify-linux and docker-platform-check. Its uv is the exact uv of the
#   Dockerfile's uv stage (tests/unit/test_build_pins.py): a uv bump changes both together.
# buildkit:   the builder of the release image, in CI and in the release workflow.
# registry:   the registry CI assembles the image index in, on every pull request.

FROM ghcr.io/astral-sh/uv:0.12.22-python3.12-trixie@sha256:76a7c405d6254b8625699bb6c5688eaa333bf6947cfddc8da7de952c63f1c156 AS linux-gate

FROM moby/buildkit:v0.33.1@sha256:cec9f139f45e93c5c69c60f8b07cfad9f43f4ef6b6a6cd917527fea5ff2e3dea AS buildkit

FROM registry:3.1.2@sha256:ddf754342cfc8acc51a56d5d0ab6af06826461864460636d8bd5c546dab2a7b8 AS registry
