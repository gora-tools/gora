#!/usr/bin/env bash
# Run a gate under an outer time limit (TESTING rule 8). Gate-defining file (ADR-0016 rule 7).
#
#   bounded.sh SECONDS COMMAND...
#
# The limit is GNU timeout(1): `timeout` on Linux, `gtimeout` on macOS (Homebrew coreutils).
# Without it the gate does not start (exit 2): it never runs unbounded. At the limit COMMAND
# and every process it started get TERM, then KILL 10 s later; exit 124 (or 137 after KILL)
# means the limit was reached, and a line on standard error says so.
#
# timeout(1) runs COMMAND in a process group of its own, which Ctrl-C does not reach. So it
# runs in the background here, and INT or TERM to this script is passed on as TERM: the
# whole group ends with the caller, and nothing is left running.
#
# Written for bash 3.2 (macOS) and later.
set -euo pipefail

if [ $# -lt 2 ]; then
  echo "usage: bounded.sh SECONDS COMMAND..." >&2
  exit 64
fi
seconds=$1
shift

case "$(uname -s)" in
  Darwin) tool=gtimeout hint="brew install coreutils" ;;
  *) tool=timeout hint="install GNU coreutils" ;;
esac
if ! command -v "$tool" > /dev/null 2>&1; then
  echo "bounded.sh: $tool not found, and a gate never runs unbounded (TESTING rule 8): $hint" >&2
  exit 2
fi

pid=
cleanup() {
  if [ -n "$pid" ]; then
    kill -TERM "$pid" 2> /dev/null || true
    wait "$pid" 2> /dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"$tool" -k 10 "$seconds" "$@" &
pid=$!
rc=0
wait "$pid" || rc=$?
pid=
if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
  echo "bounded.sh: the limit of $seconds s was reached (exit $rc): $*" >&2
fi
if [ "$rc" -ne 0 ]; then
  exit "$rc"
fi
