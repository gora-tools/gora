# Gate-defining file (ADR-0016 rule 7). The container images the gates run, pinned by the
# digest of their multi-platform index. Never built: scripts/gate/image.sh prints one by its
# stage name, and the Makefile and CI take it from there. The file exists in this form so
# that Dependabot (docker), which reads every *Dockerfile, proposes their updates.
#
# linux-gate: make verify-linux and docker-platform-check. Its uv is the exact uv of the
#   Dockerfile's uv stage (tests/unit/test_build_pins.py): a uv bump changes both together.
# buildkit:   the builder of the release image, in CI and in the release workflow.
# registry:   the registry CI assembles the image index in, on every pull request.
# scanner:    GORA's scanner image (scanner.Dockerfile), which scans the release image of each
#   platform in every pull request and in the release (make image-scan). Only a person moves
#   it, by the digest .github/workflows/scanner.yml prints: Dependabot ignores it.

FROM ghcr.io/astral-sh/uv:0.13.9-python3.12-trixie@sha256:f41e4fb82558e309e208a59ff7f0ac1ee9e70f87df60c6f77a17ad8c028402a3 AS linux-gate

FROM moby/buildkit:v0.34.2@sha256:c5f8862f52499773af85bc07695b2379ef08e8002352b0df2c4864c91d78dabe AS buildkit

FROM registry:3.1.2@sha256:82f6ff4a7f982960c8aa51617fea3aabc485df6e55cce80d22deb1de36f1bc3c AS registry

FROM ghcr.io/gora-tools/gora-scanner@sha256:28c40a41d0c2418bcf40b28b4d2a2287a6fbc4550a2e932603f106180196a4e0 AS scanner
