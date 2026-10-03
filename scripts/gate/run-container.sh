#!/usr/bin/env bash
# Run one gate container (TESTING rule 8). Gate-defining file (ADR-0016 rule 7).
#
#   run-container.sh TARGET SECONDS [--archive REV] [docker run option...] -- IMAGE COMMAND...
#
# - Refuses to start (exit 2) while any container labelled gora.gate exists, and prints
#   the command that removes it: a gate never runs next to the leftover of another.
# - The container is labelled gora.gate=TARGET and named for this run.
# - It is bounded from inside: timeout(1) is its entrypoint and runs COMMAND, so a hang
#   is killed from outside the hung process even if this script is killed with -9.
#   Exit 124 means the limit was reached.
# - It is removed when this script exits, and on INT and TERM. The recipe that calls this
#   script execs it: make sends TERM to its recipe's process, not to that process's children.
# - --archive REV feeds `git archive REV` to the container's standard input.
#
# Written for bash 3.2 (macOS) and later.
set -euo pipefail

if [ $# -lt 2 ]; then
  echo "usage: run-container.sh TARGET SECONDS [--archive REV] [option...] -- IMAGE COMMAND..." >&2
  exit 64
fi
target=$1
seconds=$2
shift 2
archive=
if [ "${1:-}" = --archive ]; then
  archive=$2
  shift 2
fi
opts=()
while [ $# -gt 0 ] && [ "$1" != -- ]; do
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

run=(docker run --rm --label "gora.gate=$target" --name "$name"
  ${opts[@]+"${opts[@]}"} --entrypoint timeout "$image" -k 10 "$seconds" "$@")
# In the background, so that a trapped signal interrupts `wait` at once instead of
# waiting for docker to return.
if [ -n "$archive" ]; then
  git archive --format=tar "$archive" | "${run[@]}" &
else
  "${run[@]}" < /dev/null &
fi
pid=$!
wait "$pid"
