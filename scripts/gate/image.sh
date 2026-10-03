#!/usr/bin/env bash
# Print the image a gate runs, by its stage name in deployments/docker/gate-images.Dockerfile.
# Gate-defining file (ADR-0016 rule 7).
#
#   image.sh NAME
#
# Exactly one `FROM <image>@sha256:<digest> AS NAME` line must exist; anything else exits 2
# and prints nothing on standard output, so a caller never runs an unpinned image.
#
# Written for bash 3.2 (macOS) and later.
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: image.sh NAME" >&2
  exit 64
fi
name=$1
file="$(cd "$(dirname "$0")/../.." && pwd)/deployments/docker/gate-images.Dockerfile"

refs=$(awk -v n="$name" '$1 == "FROM" && $3 == "AS" && $4 == n && NF == 4 { print $2 }' "$file")
count=$(printf '%s' "$refs" | grep -c . || true)
if [ "$count" -ne 1 ]; then
  echo "image.sh: $count FROM lines name stage $name in $file, expected 1" >&2
  exit 2
fi
pinned=$(printf '%s\n' "$refs" | grep -Ec '^[^@ ]+@sha256:[0-9a-f]{64}$' || true)
if [ "$pinned" -ne 1 ]; then
  echo "image.sh: stage $name is not pinned by digest: $refs" >&2
  exit 2
fi
printf '%s\n' "$refs"
