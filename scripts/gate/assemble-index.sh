#!/usr/bin/env bash
# Assemble one OCI image index from the per-platform images the build jobs pushed by digest,
# tag it, and prove what it holds (ADR-021 section 1). Gate-defining file (ADR-0016 rule 7).
# The same script assembles every pull request's rehearsal (CI, a registry on the runner)
# and every release candidate (GHCR).
#
#   assemble-index.sh IMAGE TAG REVISION RECORD...
#
# IMAGE is a repository without a tag. REVISION is the full SHA of the commit the build jobs
# built; the index is annotated with it as org.opencontainers.image.revision (L9). Each
# RECORD is the file a build job wrote:
#   platform=linux/amd64
#   digest=sha256:...     what it pushed: an index of the platform image and its provenance
#   manifest=sha256:...   the platform image manifest inside it, the one it smoke-tested
#
# Exit 0 only if the records name linux/amd64 and linux/arm64 once each, and the index
# tagged TAG holds exactly those two platform manifests, at exactly those digests, besides
# their attestation manifests, and the index and each of those manifests carry REVISION
# (revision.sh). Prints the index digest last; with GITHUB_OUTPUT set, also writes
# `index=<digest>` there.
#
# Written for bash 3.2 (macOS) and later.
set -euo pipefail

PLATFORMS='linux/amd64
linux/arm64'

if [ $# -lt 4 ]; then
  echo "usage: assemble-index.sh IMAGE TAG REVISION RECORD..." >&2
  exit 64
fi
image=$1
tag=$2
revision=$3
shift 3

is_digest() {
  printf '%s\n' "$1" | grep -Eq '^sha256:[0-9a-f]{64}$'
}

sources=()
want=
for record in "$@"; do
  platform=$(sed -n 's/^platform=//p' "$record")
  digest=$(sed -n 's/^digest=//p' "$record")
  manifest=$(sed -n 's/^manifest=//p' "$record")
  if ! is_digest "$digest" || ! is_digest "$manifest" || [ -z "$platform" ]; then
    echo "assemble-index.sh: $record is not a build record (platform, digest, manifest)" >&2
    exit 1
  fi
  sources+=("$image@$digest")
  want="$want$platform $manifest
"
done
want=$(printf '%s' "$want" | sort)
if [ "$(printf '%s\n' "$want" | cut -d' ' -f1)" != "$PLATFORMS" ]; then
  echo "assemble-index.sh: the records name these platforms, expected linux/amd64 and linux/arm64 once each:" >&2
  printf '%s\n' "$want" | cut -d' ' -f1 >&2
  exit 1
fi

docker buildx imagetools create --tag "$image:$tag" \
  --annotation "index:org.opencontainers.image.revision=$revision" "${sources[@]}"

raw=$(docker buildx imagetools inspect --raw "$image:$tag")
index="sha256:$(printf '%s' "$raw" | { sha256sum 2> /dev/null || shasum -a 256; } | cut -d' ' -f1)"
# The index by its digest is the same document: the digest is the release identity.
if [ "$(docker buildx imagetools inspect --raw "$image@$index")" != "$raw" ]; then
  echo "assemble-index.sh: $image@$index is not the index tagged $tag" >&2
  exit 1
fi
# Attestation manifests (BuildKit provenance) describe a platform manifest; they are not a
# platform a node can pull.
got=$(printf '%s' "$raw" | jq -r '.manifests[]
  | select(.annotations["vnd.docker.reference.type"] != "attestation-manifest")
  | "\(.platform.os)/\(.platform.architecture) \(.digest)"' | sort)

echo "assemble-index.sh: $image:$tag is $index"
printf '%s' "$raw" | jq -r '.manifests[] | "  \(.platform.os)/\(.platform.architecture)\(if .platform.variant then "/" + .platform.variant else "" end)  \(.digest)  \(.annotations["vnd.docker.reference.type"] // "image")"'
if [ "$got" != "$want" ]; then
  echo "assemble-index.sh: the index does not hold exactly the platform manifests the build jobs built" >&2
  echo "  expected:" >&2
  printf '%s\n' "$want" | sed 's/^/    /' >&2
  echo "  found:" >&2
  printf '%s\n' "$got" | sed 's/^/    /' >&2
  exit 1
fi
echo "assemble-index.sh: exactly linux/amd64 and linux/arm64, the digests the build jobs built"
"$(dirname "$0")/revision.sh" "$image" "$index" "$revision"
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "index=$index" >> "$GITHUB_OUTPUT"
fi
echo "$index"
