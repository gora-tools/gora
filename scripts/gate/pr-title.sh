#!/usr/bin/env bash
# Check that a pull request title is a Conventional Commit (L11, ADR-022 sections 2 and 5):
# the squash commit takes the title as its subject, and GORA computes versions from the
# subjects on main. Gate-defining file (ADR-0016 rule 7).
#
#   pr-title.sh TITLE
#
# The grammar of Conventional Commits 1.0.0, and nothing else:
#   <type>[(<scope>)][!]: <subject>
#   type     one or more letters, in any case; any type, the grammar has no list of them
#   scope    one or more characters other than parentheses and white space
#   !        a breaking change
#   ": "     a colon and one space
#   subject  starts with a character other than white space
# No length limit: a Dependabot title is long and valid. A title holding a control character
# (a line break, a tab, an escape) is refused. Exit 0 when the title conforms, 1 when it does
# not. The title is never printed: a line of output can be a command to the runner.
#
# Written for bash 3.2 (macOS) and later.
set -euo pipefail
export LC_ALL=C

if [ $# -ne 1 ]; then
  echo "usage: pr-title.sh TITLE" >&2
  exit 64
fi
title=$1
grammar='^[A-Za-z]+(\([^()[:space:]]+\))?!?: [^[:space:]]'
control='[[:cntrl:]]'

if [[ $title =~ $grammar ]] && ! [[ $title =~ $control ]]; then
  echo "pr-title.sh: the title is a Conventional Commit"
  exit 0
fi
echo "pr-title.sh: the title is not a Conventional Commit, <type>[(<scope>)][!]: <subject>," \
  "on one line, for example: fix(cli): refuse an empty stage" >&2
exit 1
