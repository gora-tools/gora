#!/usr/bin/env bash
# The red-first proof of ADR-016 rule 1, run by the repository: named tests are run on
# committed HEAD, exported, with named files taken from a base commit or a defect patch
# applied, and the proof holds only when they fail. Gate-defining file (ADR-0016 rule 7).
#
#   red-first.sh --tests "IDS" [--base REV --files "PATHS"] [--patch FILE]
#
# IDS      pytest node ids or paths, separated by spaces.
# --base   with --files: each path takes its content at REV, and is removed when REV lacks
#          it. A path that neither REV nor HEAD holds is a wrong call. --base with no file
#          changes nothing.
# --patch  a diff applied with git apply after the files, paths relative to the repository
#          root (a/ and b/ prefixes). Both forms may be combined; one of them is required.
#
# HEAD is exported with git archive into a temporary directory that every exit removes, the
# test group is synced there with uv sync --locked, and the tests run there. The working tree
# is only read: a dirty one is refused, as make verify-export refuses it, and never touched.
#
# The verdict is the last line, and a failed test's node id is printed above it, one per
# line:
#   exit 0  red-first: <n> failed                          pytest exit 1, with failed tests
#   exit 1  red-first: GREEN — nothing failed              pytest exit 0
#   exit 2  red-first: ERROR (pytest exit <n>)             any other exit: a collection
#           error or a path that does not exist is not a kill; also a refusal (dirty tree)
#           and an export, sync or patch that failed
#   exit 64 a wrong call
# A pytest exit 1 with an error and no failed test is exit 2 as well: a fixture that breaks
# is not a test that bites.
#
# Output names no absolute path: the temporary directory is never printed, paths are
# relative to the repository root, and pytest's own output, which holds the paths of the
# machine, is not shown.
#
# Written for bash 3.2 (macOS) and BSD userland, and later.
set -euo pipefail
export LC_ALL=C

usage() {
  echo "usage: red-first.sh --tests \"IDS\" [--base REV --files \"PATHS\"] [--patch FILE]" >&2
  echo "  one of --base or --patch is required; --files needs --base" >&2
  exit 64
}

tests=
base=
files=
patch=
while [ $# -gt 0 ]; do
  if [ $# -lt 2 ]; then usage; fi
  case "$1" in
    --tests) tests=$2 ;;
    --base) base=$2 ;;
    --files) files=$2 ;;
    --patch) patch=$2 ;;
    *) usage ;;
  esac
  shift 2
done
selection=()
read -r -a selection <<< "$tests"
if [ "${#selection[@]}" -eq 0 ] || { [ -z "$base" ] && [ -z "$patch" ]; }; then usage; fi
if [ -n "$files" ] && [ -z "$base" ]; then usage; fi

# Anything a tool says that is shown: an absolute path is not.
scrub() {
  sed -E "s#(^|[[:space:]\"'(=])/[^[:space:]\"')]+#\\1<path>#g"
}

fail() {
  echo "red-first: $1" >&2
  exit 64
}

# The mode git records for a path at a revision: empty when the revision lacks it.
mode_at() {
  git ls-tree "$1" -- "$2" | cut -c1-6
}

# The lines of pytest's short test summary that name a failed test or an error.
summary() {
  awk '
    /^=+ short test summary info =+$/ { in_summary = 1; next }
    /^=+ / { in_summary = 0 }
    in_summary && /^(FAILED|ERROR) / { print }
  ' "$1"
}

root=$(git rev-parse --show-toplevel 2> /dev/null) || fail "not inside a git repository"
cd "$root"

if [ -n "$base" ]; then
  base_commit=$(git rev-parse --verify --quiet "$base^{commit}") || fail "BASE is not a commit"
fi
paths=()
if [ -n "$files" ]; then
  set -f
  for path in $files; do
    case "$path" in
      /* | .. | ../* | */.. | */../* | ./* | */./* | */) fail "FILES holds a path that is not inside the repository, written from its root" ;;
    esac
    at_base=$(mode_at "$base_commit" "$path")
    at_head=$(mode_at HEAD "$path")
    if [ -z "$at_base" ] && [ -z "$at_head" ]; then fail "neither BASE nor HEAD holds $path"; fi
    for mode in "$at_base" "$at_head"; do
      case "$mode" in
        "" | 100644 | 100755) ;;
        *) fail "$path is not a regular file" ;;
      esac
    done
    paths+=("$path")
  done
  set +f
fi
if [ -n "$patch" ]; then
  if [ ! -f "$patch" ]; then fail "PATCH is not a file"; fi
  case "$patch" in
    /*) patch_file=$patch ;;
    *) patch_file=$root/$patch ;;
  esac
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "red-first: the working tree is dirty. This target runs the commit, not the tree: commit or stash first." >&2
  echo "red-first: ERROR (working tree is dirty)"
  exit 2
fi

work=
child=
cleanup() {
  if [ -n "$child" ]; then
    kill -TERM "$child" 2> /dev/null || true
    wait "$child" 2> /dev/null || true
  fi
  if [ -n "$work" ]; then rm -rf "$work"; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# A command in the background, waited for: a TERM to this script ends it at once, not after
# the command (bounded.sh). Its output goes to the file named first.
run_to() {
  local out=$1 rc=0
  shift
  "$@" > "$out" 2>&1 &
  child=$!
  wait "$child" || rc=$?
  child=
  return "$rc"
}

# The caller's uv settings never point this run at the repository's own environment.
unset UV_PROJECT_ENVIRONMENT VIRTUAL_ENV

work=$(mktemp -d "${TMPDIR:-/tmp}/gora-red-first.XXXXXX")
export_dir=$work/export
out=$work/out
mkdir "$export_dir"
echo "red-first: running the tests on commit $(git rev-parse HEAD), exported"
git archive --format=tar HEAD | tar -x -C "$export_dir"
git -C "$export_dir" -c init.defaultBranch=main init -q

for path in ${paths[@]+"${paths[@]}"}; do
  rm -f "${export_dir:?}/$path"
  if [ -n "$(mode_at "$base_commit" "$path")" ]; then
    mkdir -p "$(dirname "$export_dir/$path")"
    git show "$base_commit:$path" > "$export_dir/$path"
    if [ "$(mode_at "$base_commit" "$path")" = 100755 ]; then chmod +x "$export_dir/$path"; fi
    echo "red-first: $path as at $base"
  else
    echo "red-first: $path removed, absent at $base"
  fi
done

if [ -n "$patch" ]; then
  rc=0
  git -C "$export_dir" apply "$patch_file" > "$out" 2>&1 || rc=$?
  if [ "$rc" -ne 0 ]; then
    scrub < "$out" >&2
    echo "red-first: ERROR (the patch does not apply, git apply exit $rc)"
    exit 2
  fi
  echo "red-first: patch applied"
fi

cd "$export_dir"
rc=0
run_to "$out" uv sync --locked --all-extras --group test || rc=$?
if [ "$rc" -ne 0 ]; then
  scrub < "$out" >&2
  echo "red-first: ERROR (uv sync exit $rc)"
  exit 2
fi

rc=0
run_to "$out" uv run --no-sync pytest -q --no-header -p no:cacheprovider --color=no -rfE \
  "${selection[@]}" || rc=$?

# What is shown of pytest's output: the ids of the short test summary, and nothing else.
lines=$(summary "$out")
failed=$(printf '%s\n' "$lines" | grep -c '^FAILED ' || true)
if [ -n "$lines" ]; then
  printf '%s\n' "$lines" | sed -E -e 's/ - .*$//' -e 's/^FAILED //' -e 's/^ERROR /error: /' | scrub
fi
code=2
case "$rc" in
  0)
    verdict="GREEN — nothing failed"
    code=1
    ;;
  1)
    if [ "$failed" -gt 0 ]; then
      verdict="$failed failed"
      code=0
    else
      verdict="ERROR (pytest exit 1, an error and no failed test)"
    fi
    ;;
  *) verdict="ERROR (pytest exit $rc)" ;;
esac
echo "red-first: $verdict"
if [ "$code" -ne 0 ]; then exit "$code"; fi
