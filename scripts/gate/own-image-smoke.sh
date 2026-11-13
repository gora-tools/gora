#!/usr/bin/env bash
# Smoke one of GORA's own images before it reaches GHCR (ADR-011, amendments 2026-11-05 and
# 2026-11-07). Gate-defining file (ADR-0016 rule 7).
#
#   own-image-smoke.sh KIND IMAGE PLATFORM
#
# KIND is `base` (deployments/docker/base.Dockerfile) or `scanner` (scanner.Dockerfile). IMAGE
# is the reference to run, by digest. Every container runs offline (--network none) and with a
# read-only root file system, bounded to 300 seconds. Exit 0 when every check holds, 1 on the
# first that does not (the line starting `smoke: FAIL` says which), 64 on a wrong call.
#
# base:    the interpreter is the PYTHON_VERSION the Dockerfile declares, /usr/bin/git belongs to
#          the package git at the GIT_VERSION it declares, and it prints that version's upstream;
#          the interpreter lists no installed distribution (no pip).
# scanner: Grype's version and the database's status are printed and the status is valid with
#          no network; a scan of a package with a known fixed vulnerability fails as the gate
#          will (the fix filter and --fail-on high: exit 2, the advisory reported), and the
#          fixed version of it passes (exit 0).
#
# SCANNER_SMOKE_VULNERABLE and SCANNER_SMOKE_FIXED replace the two package URLs: the red-first
# proof that the smoke fails on a database that cannot see the advisory swaps the first for the
# fixed version. The advisory stays the one named below.
#
# Written for bash 3.2 (macOS) and later.
set -euo pipefail

if [ $# -ne 3 ]; then
  echo "usage: own-image-smoke.sh base|scanner IMAGE PLATFORM" >&2
  exit 64
fi
kind=$1
image=$2
platform=$3
root=$(cd "$(dirname "$0")/../.." && pwd)

fail() {
  echo "smoke: FAIL: $1" >&2
  exit 1
}

smoke_base() {
  local dockerfile want_git want_python upstream
  dockerfile=$root/deployments/docker/base.Dockerfile
  want_git=$(sed -n 's/^ARG GIT_VERSION=//p' "$dockerfile")
  want_python=$(sed -n 's/^ARG PYTHON_VERSION=//p' "$dockerfile")
  if [ -z "$want_git" ] || [ -z "$want_python" ]; then
    fail "base.Dockerfile declares no GIT_VERSION or no PYTHON_VERSION"
  fi
  # The upstream version git prints: no epoch, no Debian revision.
  upstream=${want_git#*:}
  upstream=${upstream%-*}
  echo "smoke: expecting Python $want_python and the package git $want_git ($upstream)"
  timeout 300 docker run --rm -i --read-only --network none --platform "$platform" \
    -e WANT_GIT="$want_git" -e WANT_PYTHON="$want_python" -e WANT_UPSTREAM="$upstream" \
    "$image" sh -eu <<'SMOKE'
python=$(python -c 'import platform; print(platform.python_version())')
echo "smoke: Python $python"
test "$python" = "$WANT_PYTHON"
package=$(dpkg-query --show --showformat='${Version}' git)
echo "smoke: package git $package"
test "$package" = "$WANT_GIT"
owner=$(dpkg-query --search /usr/bin/git)
echo "smoke: $owner"
test "$owner" = "git: /usr/bin/git"
version=$(/usr/bin/git --version)
echo "smoke: $version"
test "$version" = "git version $WANT_UPSTREAM"
distributions=$(python -c 'import importlib.metadata as m; print(", ".join(sorted("%s %s" % (d.metadata["Name"], d.version) for d in m.distributions())) or "none")')
echo "smoke: installed distributions: $distributions"
test "$distributions" = none
SMOKE
}

smoke_scanner() {
  local vulnerable fixed advisory status out rc
  vulnerable=${SCANNER_SMOKE_VULNERABLE:-pkg:pypi/certifi@2026.6.17}
  fixed=${SCANNER_SMOKE_FIXED:-pkg:pypi/certifi@2026.10.23}
  advisory=GHSA-6q48-6c7w-9gw2
  # The image's entrypoint is Grype: the arguments are Grype's.
  grype=(timeout 300 docker run --rm --read-only --network none --platform "$platform" "$image")

  "${grype[@]}" version || fail "grype version did not run"
  status=$("${grype[@]}" db status) || fail "grype db status failed with no network"
  echo "$status"
  if [ "$(printf '%s\n' "$status" | grep -Ec '^Status:[[:space:]]+valid$' || true)" -ne 1 ]; then
    fail "the database status is not valid with no network"
  fi

  # The gate's flags: the fix filter, then the threshold. The filter is applied before the
  # threshold (measured at the pinned Grype), so an unfixed finding does not fail the scan.
  rc=0
  out=$("${grype[@]}" --only-fixed --fail-on high "$vulnerable" 2>&1) || rc=$?
  echo "$out"
  echo "smoke: $vulnerable exited $rc"
  if [ "$rc" -ne 2 ]; then
    fail "$vulnerable should fail the scan (exit 2) and exited $rc: the database cannot see $advisory"
  fi
  case $out in
    *"$advisory"*) ;;
    *) fail "$vulnerable failed the scan without reporting $advisory" ;;
  esac

  rc=0
  out=$("${grype[@]}" --only-fixed --fail-on high "$fixed" 2>&1) || rc=$?
  echo "$out"
  echo "smoke: $fixed exited $rc"
  if [ "$rc" -ne 0 ]; then
    fail "$fixed should pass the scan (exit 0) and exited $rc"
  fi
}

case $kind in
  base) smoke_base ;;
  scanner) smoke_scanner ;;
  *)
    echo "usage: own-image-smoke.sh base|scanner IMAGE PLATFORM" >&2
    exit 64
    ;;
esac
echo "smoke: $kind image holds"
