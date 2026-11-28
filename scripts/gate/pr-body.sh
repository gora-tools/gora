#!/usr/bin/env bash
# Check that a pull request body is fit to be the body of the squash commit on main. The rules
# are ADR-022 section 2's, and stated only there; this file is the mechanism beside the code.
# Gate-defining file (ADR-0016 rule 7).
#
#   pr-body.sh BODY NUMBER AUTHOR AUTHOR_TYPE
#
# NUMBER is the pull request's number; AUTHOR and AUTHOR_TYPE are its author's login and
# account type, as the event names them.
#
# A body whose author is dependabot[bot] with the account type Bot is GitHub's, and is not
# checked: exit 0. A login that ends in [bot] cannot be taken by a person, and the type is the
# event's, which a body cannot change. Any other body is read line by line, in bash and
# nothing else, and each refusal names the line and the rule, never the text: a line of output
# can be a command to the runner. Exit 0 when the body conforms, 1 when it does not, 64 on a
# wrong call. An empty body conforms.
#
# How each rule is matched, under LC_ALL=C, so that a byte is a character:
#   size          the bytes of the body: a character outside ASCII counts as its UTF-8 bytes,
#                 and a CRLF line end as two. Web edits store the body with CRLF.
#   gate-output   a line that opens a fence, three backticks or three tildes after at most
#                 three spaces (four spaces or a tab make code), or that holds <details, in
#                 any case.
#   token-shape   the prefix of a credential and one character of it, with a boundary on its
#                 left, so that a longer word that ends in a prefix is no credential; and a
#                 private-key header. GORA treats a token as opaque, so a fake one is refused
#                 like a real one. The GitHub and Slack prefixes are the shapes GORA's adapters
#                 hide (GITHUB_TOKEN_SHAPES in gora.adapters.github, _SLACK in the Slack
#                 adapter), and a test holds them together. Sources: GitHub, "About
#                 authentication to GitHub" (token formats); Slack, "Token types"; AWS IAM,
#                 "Understand unique ID prefixes".
#   machine       a path of the machine that wrote the body, and an address. An address has a
#                 boundary on each side: a letter, a digit, an underscore or a dot next to it
#                 makes it part of a longer word or number (a version, a test id, a time). The
#                 first octet of an IPv4 address is 1 to 255 without 127, so a version that
#                 starts with 0 and the loopback network are no address. An IPv6 address is
#                 eight groups, or at least one group on each side of one "::", so that "::2"
#                 and a slice such as "[::2]" are none.
#   control       an ASCII control character other than tab, carriage return and line feed,
#                 and, as the UTF-8 bytes that spell them, the C1 controls (U+0080 to U+009F),
#                 the soft hyphen (U+00AD), the tag characters (U+E0000 to U+E007F) and the
#                 characters that hide or reorder text: U+200B to U+200F, U+2028 to U+202E,
#                 U+2060 to U+206F and U+FEFF. The bytes are listed, not ranges, so that
#                 collation plays no part.
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

if [ "$author" = "dependabot[bot]" ] && [ "$author_type" = "Bot" ]; then
  echo "pr-body.sh: the body of #$number is GitHub's, written by Dependabot: it is not checked"
  exit 0
fi

fence='^ {0,3}(```|~~~)'
details='<[dD][eE][tT][aA][iI][lL][sS]'
token='(^|[^A-Za-z0-9_])((gh[pousr]_|github_pat_|xox[a-z]-|xapp-)[A-Za-z0-9]|(AKIA|ASIA)[A-Z0-9]{16}|arn:aws[a-z-]*:)|-----BEGIN [A-Z ]*PRIVATE KEY'
machine='/Users/|/home/|/root/|/private/|/var/folders/|/tmp/'
octet='(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])'
first='(25[0-5]|2[0-4][0-9]|1[013-9][0-9]|12[0-689]|[1-9][0-9]?)'
ipv4='(^|[^0-9A-Za-z_.])'"${first}[.]${octet}[.]${octet}[.]${octet}"'($|[^0-9.]|[.]($|[^0-9]))'
h='[0-9A-Fa-f]{1,4}'
ipv6='(^|[^0-9A-Za-z_:.])('"${h}(:${h}){7}|(${h}:)+:${h}(:${h})*"')($|[^0-9A-Za-z_:])'
c1=$'\xc2''['$'\x80\x81\x82\x83\x84\x85\x86\x87\x88\x89\x8a\x8b\x8c\x8d\x8e\x8f\x90\x91\x92\x93\x94\x95\x96\x97\x98\x99\x9a\x9b\x9c\x9d\x9e\x9f'']'
fmt=$'\xe2\x80''['$'\x8b\x8c\x8d\x8e\x8f\xa8\xa9\xaa\xab\xac\xad\xae'']|'$'\xe2\x81''['$'\xa0\xa1\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xab\xac\xad\xae\xaf'']|'$'\xef\xbb\xbf'
soft=$'\xc2\xad'
tags=$'\xf3\xa0''['$'\x80\x81'']''['$'\x80\x81\x82\x83\x84\x85\x86\x87\x88\x89\x8a\x8b\x8c\x8d\x8e\x8f\x90\x91\x92\x93\x94\x95\x96\x97\x98\x99\x9a\x9b\x9c\x9d\x9e\x9f\xa0\xa1\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xab\xac\xad\xae\xaf\xb0\xb1\xb2\xb3\xb4\xb5\xb6\xb7\xb8\xb9\xba\xbb\xbc\xbd\xbe\xbf'']'
control="[[:cntrl:]]|$c1|$soft|$tags|$fmt"

refused=0
refuse() {
  echo "pr-body.sh: $1" >&2
  refused=1
}

size=${#body}
if [ "$size" -gt "$LIMIT" ]; then
  refuse "[size] the body is $size bytes, over $LIMIT"
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
  if [[ $text =~ $machine ]] || [[ $text =~ $ipv4 ]] || [[ $text =~ $ipv6 ]]; then
    refuse "line $line_number [machine]"
  fi
  plain=${text//$'\t'/}
  if [[ $plain =~ $control ]]; then
    refuse "line $line_number [control]"
  fi
done <<< "$body"

if [ "$refused" -ne 0 ]; then
  echo "pr-body.sh: the body of #$number is the squash commit's body on main and cannot change" \
    "once merged: ADR-022 section 2 says what it holds" >&2
  exit 1
fi
echo "pr-body.sh: the body of #$number conforms, $size bytes"
exit 0
