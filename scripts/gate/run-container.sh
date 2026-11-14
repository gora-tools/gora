#!/usr/bin/env bash
# Run one gate container (TESTING rule 8). Gate-defining file (ADR-0016 rule 7).
#
#   run-container.sh TARGET NAME SECONDS [--archive REV | --stdin] [--outer-bound]
#     [docker run option...] -- IMAGE COMMAND...
#
# NAME is the limit's Makefile variable (SMOKE_TIMEOUT); a call without one is refused
# (exit 64). The Makefile passes both from one macro.
#
# - Refuses to start (exit 2) while any container labelled gora.gate exists, and prints
#   the command that removes it: a gate never runs next to the leftover of another.
# - The container is labelled gora.gate=TARGET and named for this run.
# - It is bounded from inside: timeout(1) is its entrypoint and runs COMMAND, so a hang
#   is killed from outside the hung process even if this script is killed with -9.
#   Exit 124 means the limit was reached.
# - It ends with the report of limit-report.sh on standard error, for the `docker run` and not
#   the pull before it, with the load and CPUs of the machine this script runs on (the Docker
#   host: the one whose load slows the run, a Docker VM included). The report never changes
#   the exit code, which is the container's. Its seconds are two readings of the real clock
#   (`date +%s`), the first after the pull and the second when the container has ended:
#   `unknown` when the clock went back or a reading is no number.
# - It is removed when this script exits, and on INT and TERM. The recipe that calls this
#   script execs it: make sends TERM to its recipe's process, not to that process's children.
# - --archive REV feeds `git archive REV` to the container's standard input. --stdin gives it
#   this script's own standard input instead (the caller passes -i, as for --archive); the two
#   exclude each other.
# - --outer-bound is for an image without timeout(1) (GORA's scanner is built from scratch):
#   the image's own entrypoint runs COMMAND as its arguments, with no limit inside the
#   container. The limit is the bounded.sh run this script is called in, which ends every
#   process of that run at its limit (TERM, then KILL 10 s later), and this script removes
#   the container on TERM. SECONDS is that outer limit, for the report's share. A run killed
#   with -9 leaves the container until the next gate run refuses on it (above).
# - The image is made present for the --platform option first, by pull.sh: a pull that does
#   not finish in PULL_TIMEOUT seconds (default 300) ends the run before the container starts.
#
# Written for bash 3.2 (macOS) and later.
set -euo pipefail

if [ $# -lt 3 ]; then
  echo "usage: run-container.sh TARGET NAME SECONDS [--archive REV | --stdin] [--outer-bound] [option...] -- IMAGE COMMAND..." >&2
  exit 64
fi
report=$(dirname "$0")/limit-report.sh
"$report" check "$2" "$3" || exit 64
target=$1
limit=$2
seconds=$3
shift 3
archive=
stdin=
outer_bound=
while [ $# -gt 0 ]; do
  case "$1" in
    --archive)
      if [ $# -lt 2 ] || [ -n "$stdin" ] || [ -n "$archive" ]; then
        echo "run-container.sh: --archive takes a revision, once, and not with --stdin" >&2
        exit 64
      fi
      case "$2" in
        -*)
          echo "run-container.sh: --archive takes a revision, not $2" >&2
          exit 64
          ;;
      esac
      archive=$2
      shift 2
      ;;
    --stdin)
      if [ -n "$archive" ] || [ -n "$stdin" ]; then
        echo "run-container.sh: --stdin is given once, and not with --archive" >&2
        exit 64
      fi
      stdin=1
      shift
      ;;
    --outer-bound)
      outer_bound=1
      shift
      ;;
    *) break ;;
  esac
done
opts=()
platform=
while [ $# -gt 0 ] && [ "$1" != -- ]; do
  if [ "$1" = --platform ] && [ $# -gt 1 ]; then
    platform=$2
  fi
  opts+=("$1")
  shift
done
if [ $# -lt 3 ]; then
  echo "run-container.sh: expected -- IMAGE COMMAND..." >&2
  exit 64
fi
shift
image=$1
shift

left=$(docker ps -a --filter label=gora.gate --format '{{.Names}}')
if [ -n "$left" ]; then
  names=${left//$'\n'/ }
  echo "$target: refusing to start, a gate container is still present: $names" >&2
  echo "  remove it with: docker rm -f $names" >&2
  exit 2
fi

if [ -z "$platform" ]; then
  echo "$target: run-container.sh needs --platform: the image is pulled for one platform" >&2
  exit 64
fi
"$(dirname "$0")/pull.sh" "$image" "$platform" "${PULL_TIMEOUT:-300}"

name="gora-gate-$target-$$-$(date +%s)"
pid=
cleanup() {
  docker rm -f "$name" > /dev/null 2>&1 || true
  if [ -n "$pid" ]; then
    wait "$pid" 2> /dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

sample=$("$report" sample)
load=${sample%% *}
cpus=${sample##* }

if [ -n "$outer_bound" ]; then
  # The image has no timeout(1): its own entrypoint runs COMMAND, and the caller's bounded.sh
  # bounds the run.
  run=(docker run --rm --label "gora.gate=$target" --name "$name"
    ${opts[@]+"${opts[@]}"} "$image" "$@")
else
  run=(docker run --rm --label "gora.gate=$target" --name "$name"
    ${opts[@]+"${opts[@]}"} --entrypoint timeout "$image" -k 10 "$seconds" "$@")
fi
# The run's length is two readings of the real clock, the first after the pull; one that fails
# reads as nothing, and limit-report.sh says `unknown` for it.
start=$(date +%s 2> /dev/null || true)
# In the background, so that a trapped signal interrupts `wait` at once instead of
# waiting for docker to return. A background command reads /dev/null unless it is told
# otherwise: --stdin says so (<&0).
if [ -n "$archive" ]; then
  git archive --format=tar "$archive" | "${run[@]}" &
elif [ -n "$stdin" ]; then
  "${run[@]}" <&0 &
else
  "${run[@]}" < /dev/null &
fi
pid=$!
rc=0
wait "$pid" || rc=$?
end=$(date +%s 2> /dev/null || true)
elapsed=$("$report" elapsed "$start" "$end" 2> /dev/null || true)
pid=
"$report" line run-container.sh "$limit" "$elapsed" "$seconds" "$load" "$cpus" || true
if [ "$rc" -ne 0 ]; then
  exit "$rc"
fi
