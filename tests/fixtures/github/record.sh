#!/usr/bin/env bash
# Record the answers of the code host that tests/unit/test_github_reader.py replays (ADR-006).
# Run once and committed with what it wrote; never re-recorded (see README.md).
#
#   tests/fixtures/github/record.sh
#
# Each answer lands in <name>.http as the host sent it, cut to what the tests read: the status
# line, the headers Content-Type, Location, Link, X-RateLimit-Reset and Retry-After, a blank
# line and the body. The repositories are public and are read as whoever `gh` is logged in as.
#
# `gh api` follows a redirect, so the answer of the old name, a 301, is read with curl, which
# does not. The token goes to curl on standard input, never on its command line.
#
# Check-then-act: a file that exists is kept, and an answer whose status is not the one
# expected is not written, the script ending red.
#
# Written for bash 3.2 (macOS) and later.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

fixtures="gora-tools/gora-fixtures"
old_name="gora-tools/gora-fixtures-before"
gora="gora-tools/gora"

# The annotated tag objects of the fixture repository, read from its matching-refs/tags/v on
# 2026-11-14: v0.0.1-rc, v0.0.2-rc and v0.0.3-rc. The last sha names no object.
sha_rc1=9117328866550023d4a7ee750a0e4226072da71f
sha_rc2=0ffeddcefe03df638ca1f78c8ffdbb39f149ca13
sha_rc3=3365fad432f0657966bcf83c0187ef90c44c674b
sha_unknown=1111111111111111111111111111111111111111

# The raw answer (-i: status line, headers, body) cut to the headers the tests read.
cut_to_what_tests_read() {
  awk '
    { sub(/\r$/, "") }
    state == 0 { print; state = 1; next }
    state == 1 && $0 == "" { print ""; state = 2; next }
    state == 1 {
      name = tolower($0)
      sub(/:.*/, "", name)
      if (name ~ /^(content-type|location|link|x-ratelimit-reset|retry-after)$/) print
      next
    }
    { print }
  '
}

fetch_following() {
  gh api -i "$1" || true
}

fetch_not_following() {
  printf 'header = "Authorization: Bearer %s"\n' "$(gh auth token)" |
    curl --silent --show-error --include --config - \
      --header "Accept: application/vnd.github+json" \
      --header "X-GitHub-Api-Version: 2022-11-28" \
      "https://api.github.com/$1"
}

# record NAME STATUS FETCHER ENDPOINT
record() {
  local name=$1 status=$2 fetcher=$3 endpoint=$4
  local file="$here/$name.http"
  if [ -e "$file" ]; then
    echo "record: $name kept: already recorded"
    return 0
  fi
  local part="$file.part"
  if ! "$fetcher" "$endpoint" | cut_to_what_tests_read >"$part"; then
    rm -f "$part"
    echo "record: $name FAILED: no answer" >&2
    return 1
  fi
  local code=""
  read -r _ code _ <"$part" || true
  if [ "$code" != "$status" ]; then
    rm -f "$part"
    echo "record: $name FAILED: expected $status, the host answered ${code:-nothing}" >&2
    return 1
  fi
  mv "$part" "$file"
  echo "record: $name recorded: $status"
}

record repo 200 fetch_following "repos/$fixtures"
record repo-renamed 301 fetch_not_following "repos/$old_name"
record ref-main 200 fetch_following "repos/$fixtures/git/ref/heads/main"
record tag-refs 200 fetch_following "repos/$fixtures/git/matching-refs/tags/v"
record tag-refs-none 200 fetch_following "repos/$fixtures/git/matching-refs/tags/fixture/v"
record tag-v0.0.1-rc 200 fetch_following "repos/$fixtures/git/tags/$sha_rc1"
record tag-v0.0.2-rc 200 fetch_following "repos/$fixtures/git/tags/$sha_rc2"
record tag-v0.0.3-rc 200 fetch_following "repos/$fixtures/git/tags/$sha_rc3"
record tag-unknown 404 fetch_following "repos/$fixtures/git/tags/$sha_unknown"
record gora-tag-refs 200 fetch_following "repos/$gora/git/matching-refs/tags/v"
