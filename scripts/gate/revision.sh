#!/usr/bin/env bash
# Check that an image index, and every platform manifest in it, carries its source commit as
# the annotation org.opencontainers.image.revision (L9, ADR-022 section 3): the artifact
# carries its source commit, never its version. Gate-defining file (ADR-0016 rule 7).
#
#   revision.sh IMAGE DIGEST REVISION
#
# IMAGE is a repository without a tag, DIGEST the digest of the index, REVISION the full SHA
# of the commit built. Prints the revision each document carries. Exit 0 only if the index
# and each platform manifest it lists carry exactly REVISION; attestation manifests describe
# a platform manifest and are not checked. An index that lists no platform manifest fails.
#
# Written for bash 3.2 (macOS) and later.
set -euo pipefail

KEY=org.opencontainers.image.revision

if [ $# -ne 3 ]; then
  echo "usage: revision.sh IMAGE DIGEST REVISION" >&2
  exit 64
fi
image=$1
digest=$2
revision=$3

if ! [[ $revision =~ ^[0-9a-f]{40}$ ]]; then
  echo "revision.sh: REVISION must be the full SHA of a commit, 40 lowercase hex digits" >&2
  exit 64
fi

# The annotation of one document, or (none).
annotation() {
  printf '%s' "$1" | jq -r --arg key "$KEY" '.annotations[$key] // "(none)"'
}

raw=$(docker buildx imagetools inspect --raw "$image@$digest")
manifests=$(printf '%s' "$raw" | jq -r '.manifests[]?
  | select(.annotations["vnd.docker.reference.type"] != "attestation-manifest") | .digest')
if [ -z "$manifests" ]; then
  echo "revision.sh: $image@$digest lists no platform manifest" >&2
  exit 1
fi

wrong=0
got=$(annotation "$raw")
echo "revision.sh: index $digest: $KEY=$got"
if [ "$got" != "$revision" ]; then
  wrong=1
fi
for manifest in $manifests; do
  got=$(annotation "$(docker buildx imagetools inspect --raw "$image@$manifest")")
  echo "revision.sh: manifest $manifest: $KEY=$got"
  if [ "$got" != "$revision" ]; then
    wrong=1
  fi
done
if [ "$wrong" -ne 0 ]; then
  echo "revision.sh: the index and every platform manifest must carry $KEY=$revision" >&2
  exit 1
fi
echo "revision.sh: the index and every platform manifest carry $KEY=$revision"
