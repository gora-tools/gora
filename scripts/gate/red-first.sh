#!/usr/bin/env bash
# The red-first proof of ADR-016 rule 1, run by the repository: a check is run on committed
# HEAD, exported, first as HEAD has it and then with named files taken from a base commit or
# a defect patch applied, and the proof holds only when it passes without the change and
# fails with it. The check is named tests, or the export's own docker-smoke.
# Gate-defining file (ADR-0016 rule 7).
#
#   red-first.sh --tests "IDS" [--base REV --files "PATHS"] [--patch FILE]
#   red-first.sh --check docker-smoke --expect "TEXT" [--base REV --files "PATHS"] [--patch FILE]
#
# IDS      pytest node ids or paths, separated by spaces.
# --check  docker-smoke, the only one: the export is committed and `make docker-smoke` runs in
#          it, which builds the release image from the export's HEAD and smoke-tests it.
# --expect with --check, and only with it: the text, a fixed string on one line, that the
#          failing run must print. A failure without it is not the defect (a failed pull, a
#          refused network). It is matched against the output as shown, after scrub, so an
#          absolute path in it is written <path>; an --expect that scrub would change is a
#          wrong call (exit 64), refused before anything is exported.
# --base   with --files: each path takes its content at REV, and is removed when REV lacks
#          it. A path that neither REV nor HEAD holds is a wrong call. --base with no file
#          changes nothing.
# --patch  a diff applied with git apply after the files, paths relative to the repository
#          root (a/ and b/ prefixes). Both forms may be combined; one of them is required.
# Exactly one of --tests and --check; anything else is a wrong call.
#
# HEAD is exported with git archive into a temporary directory that every exit removes.
# Tests: the test group is synced there with uv sync --locked (and the build backend, as make
# sync-test does), and the tests run there, first as HEAD
# has them. Only a baseline that is green counts: a test already red on HEAD would be red
# whatever the change did, and proves nothing (ADR-016 rule 2). The files and the patch are
# applied after it, the group is synced again (a change may touch pyproject.toml or uv.lock),
# and the tests run again. The working tree is only read: a dirty one is refused, as make
# verify-export refuses it, and never touched.
#
# Check: the export is committed (a fixed author and committer, no hook, no signing, no ignore
# file of this machine) and its tree must be HEAD's. The check is `make -C EXPORT
# docker-smoke IMAGE=TAG` with TAG the run's own, under env -i with only PATH, HOME, TMPDIR,
# LC_ALL and each DOCKER_* variable of the caller: no make variable, MAKEFLAGS or other
# variable of the caller's reaches it, so the build is on the Docker host's own platform. It
# runs as the export has it (the baseline), which must exit 0 without TEXT in its output
# (ADR-016 rule 2), then the change is applied, the export is committed again and it runs
# again. No uv environment is synced. The run's image is removed after each run and on every
# exit, INT and TERM included, when it is present; containers are run-container.sh's.
#
# The verdict is the last line. Tests: a failed test's node id is printed above it, one per
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
# Check, with the lines of the run as shown (scrubbed) above the verdict:
#   exit 0  red-first: docker-smoke failed, as EXPECT says
#           the second run exited non-zero and printed EXPECT; each shown line holding it
#           is printed above
#   exit 1  red-first: GREEN — docker-smoke passed
#   exit 2  red-first: ERROR (baseline docker-smoke failed)
#           red-first: ERROR (EXPECT is in the green baseline's output)
#           red-first: ERROR (docker-smoke failed without EXPECT)
#           each with the last 20 shown lines above it, nothing applied after the first two;
#           also the errors of tests: a refusal (dirty tree), an export or a patch that
#           failed, and an export whose tree is not HEAD's
#   exit 64 a wrong call
# No verdict of this mode prints an exit code: make ends every failed recipe with its own
# exit 2, so the code says nothing; what failed is in the lines above the verdict.
#
# Output names none of the paths the script knows. What a tool prints that is shown goes
# through scrub: the temporary directory, the repository root and HOME are replaced as
# strings, each as it was given, in its logical form and in its physical form, whatever comes
# before them (a file:// URL, a colon, a comma); any other absolute path is replaced only
# when it follows white space, a quote, a parenthesis, = or file://. Paths are otherwise
# relative to the repository root, and pytest's own output, which holds the paths of the
# machine, is not shown.
#
# Written for bash 3.2 (macOS) and BSD userland, and later.
set -euo pipefail
export LC_ALL=C

usage() {
  echo "usage: red-first.sh --tests \"IDS\" [--base REV --files \"PATHS\"] [--patch FILE]" >&2
  echo "       red-first.sh --check docker-smoke --expect \"TEXT\" [--base REV --files \"PATHS\"] [--patch FILE]" >&2
  echo "  exactly one of --tests and --check; --expect goes with --check and only with it" >&2
  echo "  one of --base or --patch is required; --files needs --base" >&2
  exit 64
}

tests=
check=
expect=
base=
files=
patch=
while [ $# -gt 0 ]; do
  if [ $# -lt 2 ]; then usage; fi
  case "$1" in
    --tests) tests=$2 ;;
    --check) check=$2 ;;
    --expect) expect=$2 ;;
    --base) base=$2 ;;
    --files) files=$2 ;;
    --patch) patch=$2 ;;
    *) usage ;;
  esac
  shift 2
done
selection=()
read -r -a selection <<< "$tests"
if [ -n "$tests" ] && [ -n "$check" ]; then usage; fi
if [ -z "$tests" ] && [ -z "$check" ]; then usage; fi
if [ -n "$check" ]; then
  if [ "$check" != docker-smoke ] || [ -z "$expect" ]; then usage; fi
  case "$expect" in
    *$'\n'*) usage ;;
  esac
else
  if [ -n "$expect" ] || [ "${#selection[@]}" -eq 0 ]; then usage; fi
fi
if [ -z "$base" ] && [ -z "$patch" ]; then usage; fi
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

# Anything a tool says that is shown holds none of the paths the script knows. They are
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
image=

# The run's image, if there is one: removed when it is present, left alone when it is not.
remove_image() {
  if [ -z "$image" ]; then return 0; fi
  if docker image inspect "$image" > /dev/null 2>&1; then
    if ! docker image rm "$image" > /dev/null 2>&1; then
      echo "red-first: the image $image could not be removed: docker image rm $image" >&2
    fi
  fi
}

cleanup() {
  if [ -n "$child" ]; then
    kill -TERM "$child" 2> /dev/null || true
    wait "$child" 2> /dev/null || true
  fi
  remove_image
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
# EXPECT is matched against the output as shown, which scrub has changed, so a path in it
# could never match and the run would end in ERROR after two builds. It is refused here,
# before anything is exported, and not echoed: it holds the path.
if [ -n "$check" ] && [ "$(printf '%s\n' "$expect" | scrub)" != "$expect" ]; then
  fail "EXPECT holds an absolute path, which the output shows as <path>: write it as <path>"
fi
if [ -n "$check" ]; then
  echo "red-first: running $check on commit $(git rev-parse HEAD), exported"
else
  echo "red-first: running the tests on commit $(git rev-parse HEAD), exported"
fi

# git in the export, as nothing on this machine configures it: no global or system
# configuration, a fixed author and committer, no hook, no signing, no ignore file.
export_git() {
  env GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
    GIT_AUTHOR_NAME=red-first GIT_AUTHOR_EMAIL=red-first@example.invalid \
    GIT_COMMITTER_NAME=red-first GIT_COMMITTER_EMAIL=red-first@example.invalid \
    git -C "$export_dir" -c core.hooksPath=/dev/null -c core.excludesFile=/dev/null \
    -c commit.gpgSign=false -c tag.gpgSign=false "$@"
}

git archive --format=tar HEAD | tar -x -C "$export_dir"
export_git -c init.defaultBranch=main init -q

# The change, in the export: each file as at the base, then the patch.
apply_change() {
  local path rc=0
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
    git -C "$export_dir" apply "$patch_file" > "$out" 2>&1 || rc=$?
    if [ "$rc" -ne 0 ]; then
      scrub < "$out" >&2
      echo "red-first: ERROR (the patch does not apply, git apply exit $rc)"
      exit 2
    fi
    echo "red-first: patch applied"
  fi
}

# Check mode ends here, in its own flow; the test mode follows it unchanged.
if [ -n "$check" ]; then
  shown=$work/shown

  # The environment of the check, and nothing else of the caller's.
  check_env=("PATH=${PATH:-}" "HOME=${HOME:-}" "TMPDIR=${TMPDIR:-/tmp}" "LC_ALL=C")
  names=$(compgen -e DOCKER_ || true)
  while IFS= read -r name; do
    if [ -n "$name" ]; then check_env+=("$name=${!name}"); fi
  done <<< "$names"

  # The export as a commit: the check builds the image from the export's own HEAD.
  commit_export() {
    local rc=0
    {
      export_git add -A &&
        export_git commit -q --allow-empty --no-verify --no-gpg-sign -m "$1"
    } > "$out" 2>&1 || rc=$?
    if [ "$rc" -ne 0 ]; then
      scrub < "$out" >&2
      echo "red-first: ERROR (the export could not be committed, git exit $rc)"
      exit 2
    fi
  }

  # The check in the export: its output is in $out, scrubbed in $shown, its exit code in rc,
  # and the image it built is gone. The tag is the run's, the same for both runs, and is only
  # taken here, so that a run that never gets this far asks docker nothing.
  run_check() {
    rc=0
    if [ -z "$image" ]; then image=gora-red-first:$$-$(date +%s); fi
    run_to "$out" env -i "${check_env[@]}" make -C "$export_dir" docker-smoke IMAGE="$image" || rc=$?
    scrub < "$out" > "$shown"
    remove_image
  }

  # How many shown lines hold the text, and which.
  count_expect() {
    grep -a -c -F -e "$expect" "$shown" || true
  }
  show_expect() {
    grep -a -F -e "$expect" "$shown" || true
  }
  show_tail() {
    tail -n 20 "$shown"
  }

  export_git add -A
  if [ "$(export_git write-tree)" != "$(git rev-parse 'HEAD^{tree}')" ]; then
    echo "red-first: ERROR (the export is not HEAD's tree)"
    exit 2
  fi
  commit_export "red-first: HEAD"

  # The baseline: the check as HEAD has it, before anything is applied.
  run_check
  if [ "$rc" -ne 0 ]; then
    show_tail
    echo "red-first: ERROR (baseline $check failed)"
    exit 2
  fi
  if [ "$(count_expect)" -ne 0 ]; then
    show_tail
    echo "red-first: ERROR (EXPECT is in the green baseline's output)"
    exit 2
  fi
  echo "red-first: baseline green on HEAD"

  apply_change
  commit_export "red-first: HEAD with the change"
  run_check
  if [ "$rc" -eq 0 ]; then
    echo "red-first: GREEN — $check passed"
    exit 1
  fi
  if [ "$(count_expect)" -eq 0 ]; then
    show_tail
    echo "red-first: ERROR ($check failed without EXPECT)"
    exit 2
  fi
  show_expect
  echo "red-first: $check failed, as EXPECT says"
  exit 0
fi

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

apply_change
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
