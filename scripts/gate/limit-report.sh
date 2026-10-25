#!/usr/bin/env bash
# The report of a bounded run (TESTING rule 8). Gate-defining file (ADR-0016 rule 7).
#
#   limit-report.sh check NAME SECONDS
#   limit-report.sh sample
#   limit-report.sh line PREFIX NAME ELAPSED SECONDS LOAD CPUS CODE
#
# One implementation for bounded.sh and run-container.sh, run by them and never sourced.
#
# check   refuses a call that names no limit, or a limit that is not a whole number of
#         seconds above zero (exit 64): NAME is a Makefile variable such as VERIFY_TIMEOUT,
#         upper case, the key of the table's row in TESTING rule 8.
# sample  prints "LOAD CPUS" for the machine this runs on, to be taken when a run starts:
#         the 1-minute load average from the first field of /proc/loadavg (Linux), else from
#         `sysctl -n vm.loadavg` (macOS, "{ 3.96 3.36 3.22 }"), and the CPUs from
#         `getconf _NPROCESSORS_ONLN`. A source that cannot be read prints `unknown`, never
#         a guess, and never fails. LIMIT_REPORT_LOADAVG is the file's path, for the tests.
# line    prints, on standard error,
#           PREFIX: NAME: <ELAPSED> s of <SECONDS> s (<n>%), load <LOAD> on <CPUS> CPUs
#         where n is rounded up, and adds "; above a tenth of the limit, TESTING rule 8 does
#         not hold" when ELAPSED x 10 > SECONDS. PULL_TIMEOUT never adds it: a pull is bounded
#         by the network, not by a run. CODE is the run's exit code, a whole number.
#         Then it appends the run to this machine's log, outside the repository, as one
#         tab-separated record:
#           <finished> <NAME> <ELAPSED> <LOAD> <CPUS> <CODE>
#         finished is the UTC time the line is printed (date -u +%Y-%m-%dT%H:%M:%SZ), LOAD and
#         CPUS are as printed, `unknown` included, and a pull is recorded as PULL_TIMEOUT. The
#         log is $XDG_STATE_HOME/gora/limits.log, else $HOME/.local/state/gora/limits.log (a
#         relative or empty one is not used, as the XDG Base Directory spec says), its
#         directory made when it is missing. A record that cannot be written prints one line
#         that says so, with no path, and changes nothing else. It exits 0 whatever happens to
#         the print and the record: a report never changes the exit code of a gate.
#
# Written for bash 3.2 (macOS) and later.
set -euo pipefail

# [A-Z] is a range of the collation in most locales, so lower case would pass it.
export LC_ALL=C

# A decimal number, or a whole one.
is_load() {
  case $1 in
    '' | *[!0-9.]* | .* | *. | *.*.*) return 1 ;;
  esac
  return 0
}

# A whole number, zero included.
is_whole() {
  case $1 in
    '' | *[!0-9]*) return 1 ;;
  esac
  return 0
}

# A whole number above zero.
is_count() {
  case $1 in
    '' | *[!0-9]* | 0*) return 1 ;;
  esac
  return 0
}

load() {
  local value=
  local file=${LIMIT_REPORT_LOADAVG:-/proc/loadavg}
  if [ -r "$file" ]; then
    value=$(awk 'NR == 1 { print $1; exit }' "$file" 2> /dev/null || true)
  fi
  if ! is_load "$value"; then
    value=$(sysctl -n vm.loadavg 2> /dev/null | awk 'NR == 1 { print $2; exit }' || true)
  fi
  if is_load "$value"; then
    echo "$value"
  else
    echo unknown
  fi
}

cpus() {
  local value
  value=$(getconf _NPROCESSORS_ONLN 2> /dev/null || true)
  if is_count "$value"; then
    echo "$value"
  else
    echo unknown
  fi
}

# The log of bounded runs: an absolute XDG_STATE_HOME, else an absolute HOME. Nothing else.
log_file() {
  local state=${XDG_STATE_HOME:-}
  case $state in
    /*) ;;
    *)
      case ${HOME:-} in
        /*) state=$HOME/.local/state ;;
        *) return 1 ;;
      esac
      ;;
  esac
  echo "$state/gora/limits.log"
}

# One record of the run, appended to the log; a failure is said, with no path, and is all.
record() {
  local log finished
  if log=$(log_file) && finished=$(date -u +%Y-%m-%dT%H:%M:%SZ 2> /dev/null) &&
    { mkdir -p -- "$(dirname -- "$log")" &&
      printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$finished" "$@" >> "$log"; } 2> /dev/null; then
    return 0
  fi
  echo "limit-report.sh: this run was not recorded in the machine's log" >&2 || true
}

usage() {
  echo "usage: limit-report.sh check NAME SECONDS | sample | line PREFIX NAME ELAPSED SECONDS LOAD CPUS CODE" >&2
  exit 64
}

if [ $# -lt 1 ]; then
  usage
fi
command=$1
shift
case $command in
  check)
    if [ $# -ne 2 ]; then usage; fi
    case $1 in
      '' | [!A-Z]* | *[!A-Z0-9_]*)
        echo "limit-report.sh: a bounded run names its limit, a Makefile variable such as VERIFY_TIMEOUT: '$1'" >&2
        exit 64
        ;;
    esac
    if ! is_count "$2"; then
      echo "limit-report.sh: the limit of $1 is a whole number of seconds above zero: '$2'" >&2
      exit 64
    fi
    ;;
  sample)
    if [ $# -ne 0 ]; then usage; fi
    echo "$(load) $(cpus)"
    ;;
  line)
    if [ $# -ne 7 ]; then usage; fi
    prefix=$1 name=$2 elapsed=$3 seconds=$4 loadavg=$5 online=$6 code=$7
    if ! is_load "$loadavg"; then loadavg=unknown; fi
    if ! is_count "$online"; then online=unknown; fi
    if ! is_count "$seconds" || ! is_whole "$elapsed" || ! is_whole "$code"; then usage; fi
    share=$(((elapsed * 100 + seconds - 1) / seconds))
    sentence=
    if [ $((elapsed * 10)) -gt "$seconds" ] && [ "$name" != PULL_TIMEOUT ]; then
      sentence="; above a tenth of the limit, TESTING rule 8 does not hold"
    fi
    echo "$prefix: $name: $elapsed s of $seconds s ($share%), load $loadavg on $online CPUs$sentence" >&2 || true
    record "$name" "$elapsed" "$loadavg" "$online" "$code"
    ;;
  *) usage ;;
esac
