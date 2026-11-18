#!/usr/bin/env bash
# Check that a pull request body is fit to be the body of the squash commit on main (ADR-022
# section 2): the facts of the change, and nothing a merged commit cannot take back. The
# repository's settings make the pull request body the squash commit's body, GORA reads that
# body when it computes versions (a BREAKING CHANGE: footer in it is a major bump, ADR-022
# section 5), and a merged body cannot change: main is linear and protect-main allows no
# bypass. Gate-defining file (ADR-0016 rule 7).
#
#   pr-body.sh BODY NUMBER AUTHOR AUTHOR_TYPE
#
# NUMBER is the pull request's number; AUTHOR and AUTHOR_TYPE are its author's login and
# account type, as the event names them.
#
# It refuses, and names the line and the rule of each refusal, never the text:
#   size          a body over 4096 bytes. Dependabot's is exempt, by actor and never by text:
#                 the author dependabot[bot] with the account type Bot.
#   gate-output   for every author, a line that opens a fenced block (three backticks or three
#                 tildes at the start of a line, after at most three spaces: four spaces or a
#                 tab make code) or holds <details, in any case.
#   token-shape   for every author, the prefix of a token and no more, since GORA treats a
#                 token as opaque and a fake one is refused like a real one: a GitHub one (a
#                 prefix of gh with p, o, u, s or r, an underscore and one character of a
#                 token; github_pat_), a Slack one (xox with a, b or p and a hyphen; xapp-), an
#                 AWS access key id (AKIA or ASIA and 16 upper-case letters or digits), or an
#                 Amazon resource name (arn:aws). Sources: GitHub, "About authentication to
#                 GitHub" (token formats); Slack, "Token types"; AWS IAM, "Understand unique ID
#                 prefixes".
#   machine       for every author, a path under a home directory or a private-temp directory
#                 of the machine that wrote the body, and an IPv4 address (four dotted numbers
#                 of 0 to 255) other than the loopback one.
#   control       a control character other than tab, carriage return and line feed. Web edits
#                 store the body with CRLF line ends.
# An empty body conforms. Exit 0 when the body conforms, 1 when it does not, 64 on a wrong
# call. The body is never printed: a line of output can be a command to the runner.
#
# Written for bash 3.2 (macOS) and later.
set -euo pipefail
export LC_ALL=C

LIMIT=4096

if [ $# -ne 4 ] || ! [[ $2 =~ ^[1-9][0-9]*$ ]]; then
  echo "usage: pr-body.sh BODY NUMBER AUTHOR AUTHOR_TYPE (NUMBER a pull request number)" >&2
  exit 64
fi
body=$1
number=$2
author=$3
author_type=$4

fence='^ {0,3}(```|~~~)'
details='<[dD][eE][tT][aA][iI][lL][sS]'
token='gh[pousr]_[A-Za-z0-9]|github_pat_|xox[abp]-|xapp-|(AKIA|ASIA)[A-Z0-9]{16}|arn:aws'
machine='/Users/|/home/|/private/'
control='[[:cntrl:]]'
# An address is four octets, set off from other digits and dots: a leading or trailing digit
# or dot makes it part of a longer number. Groups 2 to 5 are the octets.
octet='(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])'
ipv4='(^|[^0-9.])'"${octet}[.]${octet}[.]${octet}[.]${octet}"'($|[^0-9.]|[.]($|[^0-9]))'

refused=0
refuse() {
  echo "pr-body.sh: $1" >&2
  refused=1
}

# Whether $1 holds an IPv4 address other than the loopback one: a match of it is skipped when
# it is that one, and the rest of the line is searched.
has_foreign_ipv4() {
  local rest=$1 found
  while [[ $rest =~ $ipv4 ]]; do
    found="${BASH_REMATCH[2]}.${BASH_REMATCH[3]}.${BASH_REMATCH[4]}.${BASH_REMATCH[5]}"
    if [ "$found" != "127.0.0.1" ]; then
      return 0
    fi
    rest=${rest#*"${BASH_REMATCH[0]}"}
  done
  return 1
}

size=${#body}
exempt=0
if [ "$author" = "dependabot[bot]" ] && [ "$author_type" = "Bot" ]; then
  exempt=1
fi
if [ "$exempt" -eq 0 ] && [ "$size" -gt "$LIMIT" ]; then
  refuse "[size] the body is $size bytes, over $LIMIT: it holds the facts of the change"
fi

line_number=0
while IFS= read -r line || [ -n "$line" ]; do
  line_number=$((line_number + 1))
  text=${line//$'\r'/}
  if [[ $text =~ $fence ]] || [[ $text =~ $details ]]; then
    refuse "line $line_number [gate-output]"
  fi
  if [[ $text =~ $token ]]; then
    refuse "line $line_number [token-shape]"
  fi
  if [[ $text =~ $machine ]] || has_foreign_ipv4 "$text"; then
    refuse "line $line_number [machine]"
  fi
  plain=${text//$'\t'/}
  if [[ $plain =~ $control ]]; then
    refuse "line $line_number [control]"
  fi
done <<< "$body"

if [ "$refused" -ne 0 ]; then
  echo "pr-body.sh: the body of #$number is the squash commit's body on main, and a merged" \
    "body cannot change: facts of the change only, no output, no token, no trace of a machine" >&2
  exit 1
fi
if [ "$exempt" -eq 1 ]; then
  echo "pr-body.sh: the body of #$number conforms; Dependabot's body has no size limit"
else
  echo "pr-body.sh: the body of #$number conforms, $size bytes"
fi
exit 0
