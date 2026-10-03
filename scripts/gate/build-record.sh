#!/usr/bin/env bash
# Write the record of one platform's build (ADR-021 section 2). Gate-defining file
# (ADR-0016 rule 7).
#
#   build-record.sh IMAGE DIGEST PLATFORM
#
# DIGEST is what the build pushed by digest: with BuildKit provenance, an index of the
# platform image and its attestation manifest. Prints the record assemble-index.sh reads:
#   platform=PLATFORM
#   digest=DIGEST
#   manifest=<the one image manifest for PLATFORM inside it>
# Exits 1 unless DIGEST holds exactly one image manifest, and it is for PLATFORM.
#
# Written for bash 3.2 (macOS) and later.
set -euo pipefail

if [ $# -ne 3 ]; then
  echo "usage: build-record.sh IMAGE DIGEST PLATFORM" >&2
  exit 64
fi
image=$1
digest=$2
platform=$3

raw=$(docker buildx imagetools inspect --raw "$image@$digest")
images=$(printf '%s' "$raw" | jq -r 'if .manifests then .manifests[] else empty end
  | select(.annotations["vnd.docker.reference.type"] != "attestation-manifest")
  | "\(.platform.os)/\(.platform.architecture) \(.digest)"')
if [ "$(printf '%s\n' "$images" | grep -c .)" -ne 1 ] || [ "${images%% *}" != "$platform" ]; then
  echo "build-record.sh: $image@$digest should hold one image manifest, for $platform; it holds:" >&2
  printf '%s\n' "$images" | sed 's/^/  /' >&2
  exit 1
fi
printf 'platform=%s\ndigest=%s\nmanifest=%s\n' "$platform" "$digest" "${images#* }"
