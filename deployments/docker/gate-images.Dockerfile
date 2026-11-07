# Gate-defining file (ADR-0016 rule 7). The container images the gates run, pinned by the
# digest of their multi-platform index. Never built: scripts/gate/image.sh prints one by its
# stage name, and the Makefile and CI take it from there. The file exists in this form so
# that Dependabot (docker), which reads every *Dockerfile, proposes their updates.
#
# linux-gate: make verify-linux and docker-platform-check. Its uv is the exact uv of the
#   Dockerfile's uv stage (tests/unit/test_build_pins.py): a uv bump changes both together.
# buildkit:   the builder of the release image, in CI and in the release workflow.
# registry:   the registry CI assembles the image index in, on every pull request.

FROM ghcr.io/astral-sh/uv:0.13.7-python3.12-trixie@sha256:2cd2db9a96f7036629ff5427e2769030dd1314bb6522a2e0999bad3f45e1dc9e AS linux-gate

FROM moby/buildkit:v0.34.2@sha256:c5f8862f52499773af85bc07695b2379ef08e8002352b0df2c4864c91d78dabe AS buildkit

FROM registry:3.1.2@sha256:d4032d4cc323c1731c707931ecada07a53076eac38e3b61ae57533f170f2c6c3 AS registry
