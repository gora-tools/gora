#!/usr/bin/env bash
# Check that a pull request title is a Conventional Commit (L11, ADR-022 sections 2 and 5),
# and that the subject it makes on main is at most 72 bytes: the squash commit takes the
# title as its subject and appends " (#<number>)", and GORA computes versions from the
# subjects on main. Gate-defining file (ADR-0016 rule 7).
#
#   pr-title.sh TITLE NUMBER AUTHOR AUTHOR_TYPE
#
# NUMBER is the pull request's number; AUTHOR and AUTHOR_TYPE are its author's login and
# account type, as the event names them.
#
# The grammar of Conventional Commits 1.0.0, and nothing else:
#   <type>[(<scope>)][!]: <subject>
#   type     one or more letters, in any case; any type, the grammar has no list of them
#   scope    one or more characters other than parentheses and white space
#   !        a breaking change
#   ": "     a colon and one space
#   subject  starts with a character other than white space
# The limit: the title with " (#NUMBER)" is at most 72 bytes, so a
# character outside ASCII counts as more than one. Dependabot's generated titles are exempt,
# by actor and never by text: the author dependabot[bot] with the account type Bot. A
# person's title that reads like Dependabot's is held to the limit. A title holding a control
# character (a line break, a tab, an escape) is refused. Exit 0 when the title conforms, 1
# when it does not, 64 on a wrong call. The title is never printed: a line of output can be a
# command to the runner.
#
# Written for bash 3.2 (macOS) and later.
set -euo pipefail
export LC_ALL=C

LIMIT=72

if [ $# -ne 4 ] || ! [[ $2 =~ ^[1-9][0-9]*$ ]]; then
  echo "usage: pr-title.sh TITLE NUMBER AUTHOR AUTHOR_TYPE (NUMBER a pull request number)" >&2
  exit 64
fi
title=$1
number=$2
author=$3
author_type=$4
grammar='^[A-Za-z]+(\([^()[:space:]]+\))?!?: [^[:space:]]'
control='[[:cntrl:]]'

if ! [[ $title =~ $grammar ]] || [[ $title =~ $control ]]; then
  echo "pr-title.sh: the title is not a Conventional Commit, <type>[(<scope>)][!]: <subject>," \
    "on one line, for example: fix(cli): refuse an empty stage" >&2
  exit 1
fi
if [ "$author" = "dependabot[bot]" ] && [ "$author_type" = "Bot" ]; then
  echo "pr-title.sh: the title is a Conventional Commit; Dependabot's title has no length limit"
  exit 0
fi
suffix=" (#$number)"
length=$((${#title} + ${#suffix}))
if [ "$length" -gt "$LIMIT" ]; then
  echo "pr-title.sh: the subject on main would be $length bytes with \"$suffix\"," \
    "over $LIMIT: shorten the title to $((LIMIT - ${#suffix})) bytes at most" >&2
  exit 1
fi
echo "pr-title.sh: the title is a Conventional Commit, and the subject on main is $length" \
  "bytes"
exit 0
