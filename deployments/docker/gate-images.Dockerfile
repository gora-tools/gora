# Gate-defining file (ADR-0016 rule 7). The container images the gates run, pinned by the
# digest of their multi-platform index. Never built: scripts/gate/image.sh prints one by its
# stage name, and the Makefile and CI take it from there. The file exists in this form so
# that Dependabot (docker), which reads every *Dockerfile, proposes their updates.
#
# linux-gate: make verify-linux and docker-platform-check. Its uv is the exact uv of the
#   Dockerfile's uv stage (tests/unit/test_build_pins.py): a uv bump changes both together.
# buildkit:   the builder of the release image, in CI and in the release workflow.
# registry:   the registry CI assembles the image index in, on every pull request.

FROM ghcr.io/astral-sh/uv:0.13.5-python3.12-trixie@sha256:28d8c73fcb8352d619ab3fa5d6b6ee040a3c03da47a77d688c33f2d1beb01396 AS linux-gate

FROM moby/buildkit:v0.34.0@sha256:b059f8d7226d0b326bc871af489a5dddf65e36af4d20d14251b072974d9ee87c AS buildkit

FROM registry:3.1.2@sha256:ddf754342cfc8acc51a56d5d0ab6af06826461864460636d8bd5c546dab2a7b8 AS registry
