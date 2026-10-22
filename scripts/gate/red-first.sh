#!/usr/bin/env bash
# The red-first proof of ADR-016 rule 1, run by the repository: named tests are run on
# committed HEAD, exported, with named files taken from a base commit or a defect patch
# applied, and the proof holds only when they pass without the change and fail with it.
# Gate-defining file (ADR-0016 rule 7).
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
# test group is synced there with uv sync --locked (and the build backend, as make sync-test
# does), and the tests run there, first as HEAD
# has them. Only a baseline that is green counts: a test already red on HEAD would be red
# whatever the change did, and proves nothing (ADR-016 rule 2). The files and the patch are
# applied after it, the group is synced again (a change may touch pyproject.toml or uv.lock),
# and the tests run again. The working tree is only read: a dirty one is refused, as make
# verify-export refuses it, and never touched.
#
# The verdict is the last line, and a failed test's node id is printed above it, one per
# line:
#   exit 0  red-first: <n> failed                  second run: pytest exit 1, with failed tests
#   exit 1  red-first: GREEN — nothing failed      second run: pytest exit 0
#   exit 2  red-first: ERROR (baseline is not green on HEAD)
#           the first run did not exit 0. Each id that failed or errored is printed above the
#           verdict as "baseline: <id>", and nothing is applied
#   exit 2  red-first: ERROR (pytest exit <n>)     second run: any other exit. A collection
#           error or a path that does not exist is not a kill; also a refusal (dirty tree)
#           and an export, sync or patch that failed
#   exit 64 a wrong call
# A second run with pytest exit 1, an error and no failed test is exit 2 as well: a fixture
# that breaks is not a test that bites.
#
# Output names no path of this machine. What a tool prints that is shown goes through scrub:
# the temporary directory, the repository root and HOME are replaced as strings, each as it
# was given, in its logical form and in its physical form, whatever comes before them (a
# file:// URL, a colon, a comma); any other absolute path is replaced when it follows white
# space, a quote, a parenthesis, = or file://. Paths are otherwise relative to the
# repository root, and pytest's own output, which holds the paths of the machine, is not
# shown.
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

# The paths of this machine that the script knows, one per line in scrub_forms. A path comes
# as it was given, as pwd writes it after a cd (logical) and as pwd -P writes it (physical):
# a tool prints whichever it resolved, and TMPDIR on macOS is a link, with a trailing slash.
scrub_forms=
add_forms() {
  local given=$1 form
  if [ -z "$given" ]; then return 0; fi
  while [ "${#given}" -gt 1 ] && [ "${given%/}" != "$given" ]; do given=${given%/}; done
  scrub_forms=$scrub_forms$given$'\n'
  if form=$(cd -- "$given" 2> /dev/null && pwd); then scrub_forms=$scrub_forms$form$'\n'; fi
  if form=$(cd -- "$given" 2> /dev/null && pwd -P); then scrub_forms=$scrub_forms$form$'\n'; fi
}

# Anything a tool says that is shown: no path of this machine is. The known paths are
# replaced first, as strings (index and substr, never a pattern, so a dot or a bracket in a
# directory name means itself), the longest first so that a directory inside another is not
# cut short by it. The pattern then takes the absolute paths it can tell by what comes
# before them. The forms reach awk through the environment, which does not read escapes.
scrub() {
  SCRUB_FORMS=$scrub_forms awk '
    BEGIN {
      n = split(ENVIRON["SCRUB_FORMS"], given, "\n")
      for (i = 1; i <= n; i++) {
        form = given[i]
        if (length(form) < 2 || substr(form, 1, 1) != "/" || (form in seen)) continue
        seen[form] = 1
        count++
        for (j = count; j > 1 && length(forms[j - 1]) < length(form); j--) forms[j] = forms[j - 1]
        forms[j] = form
      }
    }
    {
      line = $0
      for (i = 1; i <= count; i++) {
        done = ""
        while ((at = index(line, forms[i])) > 0) {
          done = done substr(line, 1, at - 1) "<path>"
          line = substr(line, at + length(forms[i]))
        }
        line = done line
      }
      print line
    }
  ' | sed -E "s#(^|[[:space:]\"'(=]|file://)/[^[:space:]\"')]+#\\1<path>#g"
}

fail() {
  echo "red-first: $1" >&2
  exit 64
}

# The mode git records for a path at a revision: empty when the revision lacks it.
mode_at() {
  git -C "$root" ls-tree "$1" -- "$2" | cut -c1-6
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
# The baseline run must leave nothing the second run could trust: a source file that a
# change rewrites with the same size within the same second keeps its bytecode otherwise.
export PYTHONDONTWRITEBYTECODE=1

work=$(mktemp -d "${TMPDIR:-/tmp}/gora-red-first.XXXXXX")
export_dir=$work/export
out=$work/out
mkdir "$export_dir"
add_forms "$work"
add_forms "${TMPDIR:-/tmp}"
add_forms "$root"
add_forms "${HOME:-}"
echo "red-first: running the tests on commit $(git rev-parse HEAD), exported"
git archive --format=tar HEAD | tar -x -C "$export_dir"
git -C "$export_dir" -c init.defaultBranch=main init -q

# uv sync into the export: a failure ends the run.
sync_group() {
  local rc=0
  run_to "$out" uv sync --locked --all-extras --group test --group build \
    --no-build-isolation-package gora || rc=$?
  if [ "$rc" -ne 0 ]; then
    scrub < "$out" >&2
    echo "red-first: ERROR (uv sync exit $rc)"
    exit 2
  fi
}

# The tests in the export: pytest's output is in $out, its exit code in rc.
run_tests() {
  rc=0
  run_to "$out" uv run --no-sync pytest -q --no-header -p no:cacheprovider --color=no -rfE \
    "${selection[@]}" || rc=$?
}

# The baseline: the tests as HEAD has them, before anything is applied.
cd "$export_dir"
sync_group
run_tests
if [ "$rc" -ne 0 ]; then
  lines=$(summary "$out")
  if [ -n "$lines" ]; then
    printf '%s\n' "$lines" | sed -E -e 's/ - .*$//' -e 's/^(FAILED|ERROR) /baseline: /' | scrub
  fi
  echo "red-first: ERROR (baseline is not green on HEAD)"
  exit 2
fi
echo "red-first: baseline green on HEAD"

for path in ${paths[@]+"${paths[@]}"}; do
  rm -f "${export_dir:?}/$path"
  if [ -n "$(mode_at "$base_commit" "$path")" ]; then
    mkdir -p "$(dirname "$export_dir/$path")"
    git -C "$root" show "$base_commit:$path" > "$export_dir/$path"
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

sync_group
run_tests

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
