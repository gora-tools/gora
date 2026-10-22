#!/usr/bin/env bash
# Print the exact uv and Python the release image is built with, as CI installs them for
# its checks (ADR-021, amendment 2026-10-03). Gate-defining file (ADR-0016 rule 7).
#
#   versions.sh [DOCKERFILE]
#
# Reads DOCKERFILE (default deployments/docker/Dockerfile), where each is declared once:
#   uv      the tag of `FROM ghcr.io/astral-sh/uv:<version>@sha256:<digest> AS uv`
#   python  `ARG PYTHON_VERSION=<version>`
# and prints, in the form GITHUB_OUTPUT takes:
#   python=3.12.15
#   uv=0.13.4
# Exit 2, and nothing on standard output, unless each is declared exactly once as an exact
# X.Y.Z version (the uv stage by digest): a caller never gets half an answer.
#
# Written for bash 3.2 (macOS) and later.
set -euo pipefail

if [ $# -gt 1 ]; then
  echo "usage: versions.sh [DOCKERFILE]" >&2
  exit 64
fi
file=${1:-"$(cd "$(dirname "$0")/../.." && pwd)/deployments/docker/Dockerfile"}
if [ ! -r "$file" ]; then
  echo "versions.sh: cannot read $file" >&2
  exit 2
fi

uv=$(awk '$1 == "FROM" && $3 == "AS" && $4 == "uv" && NF == 4 { print $2 }' "$file")
python=$(awk -F= '$1 == "ARG PYTHON_VERSION" { print $2 }' "$file")

one_line() {
  [ "$(printf '%s' "$1" | grep -c .)" -eq 1 ]
}
if ! one_line "$uv" ||
  [ "$(printf '%s\n' "$uv" | grep -Ec '^ghcr\.io/astral-sh/uv:[0-9]+\.[0-9]+\.[0-9]+@sha256:[0-9a-f]{64}$')" -ne 1 ]; then
  echo "versions.sh: $file needs one FROM ghcr.io/astral-sh/uv:X.Y.Z@sha256:<digest> AS uv; found: ${uv:-none}" >&2
  exit 2
fi
if ! one_line "$python" || [ "$(printf '%s\n' "$python" | grep -Ec '^[0-9]+\.[0-9]+\.[0-9]+$')" -ne 1 ]; then
  echo "versions.sh: $file needs one ARG PYTHON_VERSION=X.Y.Z; found: ${python:-none}" >&2
  exit 2
fi
uv=${uv#ghcr.io/astral-sh/uv:}
printf 'python=%s\nuv=%s\n' "$python" "${uv%@*}"
