#!/usr/bin/env bash
# Run a gate under an outer time limit (TESTING rule 8). Gate-defining file (ADR-0016 rule 7).
#
#   bounded.sh NAME SECONDS COMMAND...
#
# NAME is the limit's Makefile variable (VERIFY_TIMEOUT), the limit of its records in
# scripts/gate/limits.tsv; a call without one is refused (exit 64). The Makefile passes both
# from one macro.
#
# The limit is GNU timeout(1): `timeout` on Linux, `gtimeout` on macOS (Homebrew coreutils).
# Without it the gate does not start (exit 2): it never runs unbounded. At the limit COMMAND
# and every process it started get TERM, then KILL 10 s later; exit 124 (or 137 after KILL)
# means the limit was reached, and a line on standard error says so.
#
# Every run ends with a report on standard error, green, red or at its limit (limit-report.sh):
# NAME, the seconds it took, the limit, the share, the load average when it started and the
# CPUs, and a sentence when the run took more than a tenth of its limit, where TESTING rule 8
# does not hold. The run is also recorded, with its exit code, in this machine's log, outside
# the repository (limit-report.sh says where). The report never changes the exit code: it is
# COMMAND's, as the limit leaves it.
#
# timeout(1) runs COMMAND in a process group of its own, which Ctrl-C does not reach. So it
# runs in the background here, and INT or TERM to this script is passed on as TERM: the
# whole group ends with the caller, and nothing is left running.
#
# Written for bash 3.2 (macOS) and later.
set -euo pipefail

if [ $# -lt 3 ]; then
  echo "usage: bounded.sh NAME SECONDS COMMAND..." >&2
  exit 64
fi
report=$(dirname "$0")/limit-report.sh
"$report" check "$1" "$2" || exit 64
name=$1
seconds=$2
shift 2

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

sample=$("$report" sample)
load=${sample%% *}
cpus=${sample##* }

SECONDS=0
"$tool" -k 10 "$seconds" "$@" &
pid=$!
rc=0
wait "$pid" || rc=$?
elapsed=$SECONDS
pid=
if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
  echo "bounded.sh: the limit of $seconds s was reached (exit $rc): $*" >&2 || true
fi
"$report" line bounded.sh "$name" "$elapsed" "$seconds" "$load" "$cpus" "$rc" || true
if [ "$rc" -ne 0 ]; then
  exit "$rc"
fi
