#!/usr/bin/env bash
# Scan the release image of one platform with GORA's pinned scanner (ADR-011, amendment
# 2026-11-07). Gate-defining file (ADR-0016 rule 7).
#
#   image-scan.sh --platform PLATFORM --scanner IMAGE --stage-image IMAGE --run "PREFIX"
#                 (--layout DIR | --image REFERENCE) [--exceptions FILE]
#
# The Makefile target `image-scan` runs it under bounded.sh with SCAN_TIMEOUT: one limit for the
# whole scan (`docker save`, the volume, both containers). PREFIX is the run-container.sh call
# the gate_run macro makes (TARGET NAME SECONDS); both containers take --outer-bound, since the
# scanner image has no timeout(1) and that bounded.sh run is their limit. The scan is a function
# of the commit: the image's bytes, the scanner's digest and the exceptions file.
#
# The rule: the scan fails (exit 2) on a vulnerability that has a fix and is of high or critical
# severity (`--only-fixed --fail-on high`, the filter applied before the threshold); one
# without a fix, or below high, is printed and does not fail. Grype reads the exceptions file
# as its own configuration (`-c`); its other keys are ignored by Grype and held by the test.
#
# Offline, and never through a host path or the Docker socket. The scanner container runs with
# no network, a read-only root file system and a tmpfs at /tmp, and reads a Docker volume
# mounted read-only. The volume is filled through standard input, by a container of the
# pinned stage image (offline, read-only): a tar stream of the image as an OCI layout
# (--layout DIR: `tar -c` of the directory the build action made; --image REFERENCE: `docker
# save` of an image in the local daemon), then the exceptions file. A bind mount depends on
# what the daemon's machine shares with the host (colima shares $HOME, red-first.sh exports
# under $TMPDIR) and mounts empty, silently, what it cannot see.
#
# The volume has one fixed name. If it exists when the scan starts the scan exits 2 and names
# the command that removes it; it is removed when the scan ends, green or red.
#
# An exception that has expired stops the scan (exit 2, naming it) before anything runs: the
# scanner image has no shell, so this check is the host's, in UTC; an entry holds through its
# own date. It fails closed: a line that is none of the file's forms is refused with its
# number, never skipped. A saved image with no OCI layout (`docker save` writes one from Docker
# Engine 25) is named by the staging container and ends the scan with exit 2.
#
# Exit: 0 passed; 2 refused (an expired exception, a leftover volume, an image with no layout)
# or the scanner's own 2 for a finding at the threshold; 64 a wrong call; else a failure as it
# ended (124 or 137: the limit, from bounded.sh).
#
# Written for bash 3.2 (macOS) and later.
set -euo pipefail

VOLUME=gora-scan
root=$(cd "$(dirname "$0")/../.." && pwd)

usage() {
  echo "usage: image-scan.sh --platform PLATFORM --scanner IMAGE --stage-image IMAGE --run PREFIX (--layout DIR | --image REFERENCE) [--exceptions FILE]" >&2
  exit 64
}

platform=
scanner=
stage=
prefix=
layout=
image=
exceptions=$root/scripts/gate/scan-exceptions.yaml
while [ $# -gt 0 ]; do
  if [ $# -lt 2 ]; then usage; fi
  case "$1" in
    --platform) platform=$2 ;;
    --scanner) scanner=$2 ;;
    --stage-image) stage=$2 ;;
    --run) prefix=$2 ;;
    --layout) layout=$2 ;;
    --image) image=$2 ;;
    --exceptions) exceptions=$2 ;;
    *) usage ;;
  esac
  shift 2
done
if [ -z "$platform" ] || [ -z "$scanner" ] || [ -z "$stage" ] || [ -z "$prefix" ]; then usage; fi
if [ -n "$layout" ] && [ -n "$image" ]; then usage; fi
if [ -z "$layout" ] && [ -z "$image" ]; then usage; fi

# --- the exceptions: every line one the file may hold, every entry dated, none past ------------
# The file has these lines and no other (comments and empty lines aside): `ignore:` or
# `ignore: []`; `  - vulnerability: ID`; `    package:`; `      name: NAME`; `    reason: TEXT`;
# `    expires: YYYY-MM-DD`. Its whole form is the test's (tests/unit/test_scan_exceptions.py).
if [ ! -f "$exceptions" ]; then
  echo "image-scan: the exceptions file $exceptions does not exist" >&2
  exit 2
fi
today=$(date -u +%Y-%m-%d)
refusals=$(awk -v today="$today" -v file="$exceptions" '
  function value(line,   v) {
    v = line
    sub(/^[^:]*:[[:space:]]*/, "", v)
    sub(/[[:space:]]+$/, "", v)
    return v
  }
  function flush() {
    if (!open) return
    if (!has) {
      print "image-scan: the exception for " id " has no expires date"
    } else if (date !~ /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]$/) {
      print "image-scan: the exception for " id " has no ISO date in expires: " date
    } else if (date < today) {
      print "image-scan: the exception for " id " expired on " date " (today is " today "): remove it, or have the maintainer renew it"
    }
    open = 0
  }
  /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
  /^ignore:( \[\])?$/ { next }
  /^  - vulnerability: [^[:space:]]+$/ { flush(); open = 1; id = value($0); has = 0; date = ""; next }
  /^    package:$/ { next }
  /^      name: [^[:space:]]+$/ { next }
  /^    reason: [^[:space:]].*$/ { next }
  /^    expires: [^[:space:]].*$/ { has = 1; date = value($0); next }
  {
    print "image-scan: " file ":" NR ": a line that is none of the exceptions file'"'"'s forms, refused: " $0
  }
  END { flush() }
' "$exceptions")
if [ -n "$refusals" ]; then
  printf '%s\n' "$refusals" >&2
  exit 2
fi

# --- the volume ------------------------------------------------------------------------------
if docker volume inspect "$VOLUME" > /dev/null 2>&1; then
  echo "image-scan: the volume $VOLUME is left from another scan; remove it with: docker volume rm $VOLUME" >&2
  exit 2
fi
docker volume create "$VOLUME" > /dev/null
trap 'docker volume rm -f "$VOLUME" > /dev/null 2>&1 || true' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# --- stage: the image as an OCI layout, then the exceptions file, through standard input -------
# tar -i reads past the end of the first archive to the second. --no-overwrite-dir keeps the
# volume's own directory as it is; the scanner runs as root and reads whatever the owners are.
echo "image-scan: staging ${layout:-$image} for $platform in the volume $VOLUME"
{
  if [ -n "$layout" ]; then
    tar --no-xattrs -c -C "$layout" .
  else
    docker save "$image"
  fi
  tar --no-xattrs -c -C "$(dirname "$exceptions")" "$(basename "$exceptions")"
} | $prefix --stdin --outer-bound --platform "$platform" --network none --read-only -i \
  -v "$VOLUME:/scan" -- "$stage" sh -euc '
  tar -x -i --no-overwrite-dir -C /scan
  test -f /scan/index.json || {
    echo "image-scan: the image stream holds no OCI layout (index.json): docker save writes one from Docker Engine 25" >&2
    exit 2
  }'

# --- scan --------------------------------------------------------------------------------------
echo "image-scan: scanning for $platform: a vulnerability with a fix, high or critical, fails"
rc=0
$prefix --outer-bound --platform "$platform" --network none --read-only --tmpfs /tmp \
  -v "$VOLUME:/scan:ro" -- "$scanner" --platform "$platform" -c "/scan/$(basename "$exceptions")" \
  --only-fixed --fail-on high oci-dir:/scan || rc=$?
if [ "$rc" -eq 0 ]; then
  echo "image-scan: passed: no vulnerability with a fix, of high or critical severity"
fi
exit "$rc"
