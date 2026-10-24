#!/usr/bin/env bash
# Make a gate image present for a platform, pulling it under a time limit (TESTING rule 8).
# Gate-defining file (ADR-0016 rule 7).
#
#   pull.sh IMAGE PLATFORM SECONDS
#
# Check, then act: an image already present for PLATFORM is not pulled, so a local build
# (gora:smoke) needs no registry. Otherwise `docker pull` runs under bounded.sh, and a pull
# that does not finish in SECONDS ends the gate (exit 124) before any container starts. The
# run is named PULL_TIMEOUT, and its report has no sentence about the 10x of TESTING rule 8: a
# pull is bounded by the network, not by a run.
# Pull progress goes to standard error: a caller's standard output stays the container's.
#
# Written for bash 3.2 (macOS) and later.
set -euo pipefail

if [ $# -ne 3 ]; then
  echo "usage: pull.sh IMAGE PLATFORM SECONDS" >&2
  exit 64
fi
image=$1
platform=$2
seconds=$3
format='{{.Os}}/{{.Architecture}}'

# --platform picks one platform of a multi-platform image; a Docker CLI without the option
# answers for the image as stored, which is right for a single-platform image.
have=$(docker image inspect --platform "$platform" --format "$format" "$image" 2> /dev/null ||
  docker image inspect --format "$format" "$image" 2> /dev/null || true)
if [ "$have" = "$platform" ]; then
  exit 0
fi

echo "pull.sh: pulling $image for $platform, limit $seconds s" >&2
rc=0
"$(dirname "$0")/bounded.sh" PULL_TIMEOUT "$seconds" docker pull --platform "$platform" "$image" >&2 || rc=$?
if [ "$rc" -ne 0 ]; then
  echo "pull.sh: $image for $platform could not be pulled (exit $rc); no container started" >&2
  exit "$rc"
fi
