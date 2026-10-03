#!/usr/bin/env bash
# Install crane, pinned by version and checksum, for moving a pull request's per-platform
# images between CI jobs as OCI layouts (ADR-021 section 4). Gate-defining file (ADR-0016
# rule 7).
#
#   crane.sh DIR
#
# Downloads the go-containerregistry release archive for this machine, checks its SHA-256
# against the value below, and puts `crane` in DIR. A checksum that does not match, or a
# machine with no checksum here, exits 1 and installs nothing. To update: take the new
# release's checksums.txt from github.com/google/go-containerregistry/releases, and change
# VERSION and every checksum in one commit.
#
# Written for bash 3.2 (macOS) and later.
set -euo pipefail

VERSION=v0.22.1

if [ $# -ne 1 ]; then
  echo "usage: crane.sh DIR" >&2
  exit 64
fi
dir=$1

case "$(uname -s)/$(uname -m)" in
  Linux/x86_64) asset=go-containerregistry_Linux_x86_64.tar.gz
    sum=0ab7a1d6932a213aed964ce97666c3077fe691c8606413674a8b3e0b9ec4cda0 ;;
  Linux/aarch64) asset=go-containerregistry_Linux_arm64.tar.gz
    sum=898c0cff975f898a33e8c4580bdafb0e7c02c7faa33374e946762f97c4ab7110 ;;
  Darwin/arm64) asset=go-containerregistry_Darwin_arm64.tar.gz
    sum=2231fc8df8806d20d680ff1225db44e095a55dd6ac1ae8eced4faf4b278b78fb ;;
  *)
    echo "crane.sh: no checksum for $(uname -s)/$(uname -m)" >&2
    exit 1
    ;;
esac

work=$(mktemp -d "${TMPDIR:-/tmp}/crane.XXXXXX")
trap 'rm -rf "$work"' EXIT
curl -fsSL --retry 3 --max-time 120 -o "$work/$asset" \
  "https://github.com/google/go-containerregistry/releases/download/$VERSION/$asset"
if command -v sha256sum > /dev/null 2>&1; then
  got=$(sha256sum "$work/$asset" | cut -d' ' -f1)
else
  got=$(shasum -a 256 "$work/$asset" | cut -d' ' -f1)
fi
if [ "$got" != "$sum" ]; then
  echo "crane.sh: $asset has SHA-256 $got, expected $sum" >&2
  exit 1
fi
tar -xzf "$work/$asset" -C "$work" crane
mkdir -p "$dir"
install -m 0755 "$work/crane" "$dir/crane"
echo "crane.sh: crane $VERSION ($asset, sha256 $sum) in $dir"
