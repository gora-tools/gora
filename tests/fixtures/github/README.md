# Recorded answers of the code host

The real answers of GitHub's API that `tests/unit/test_github_reader.py` replays through
`httpx.MockTransport` (ADR-006, TESTING rule 5: a reader of foreign answers is proven on
answers a foreign producer made). Recorded once, on 2026-11-14, by `record.sh`, and never
re-recorded: a test asserts these values, and a fresh recording would be another set of facts.
`record.sh` keeps a file that exists; to record one again, delete it by hand and say why in the
pull request.

Each `*.http` file is the status line, the headers the tests read (`Content-Type`, `Location`,
`Link`, `X-RateLimit-Reset`, `Retry-After`; the rest are cut), a blank line and the body, as
the host sent them.

## The repositories

| Repository | Id | Why |
| --- | --- | --- |
| `gora-tools/gora-fixtures` | 1457970812 | created by the maintainer on 2026-11-14 as `gora-fixtures-before`, then renamed; public; holds the tags below |
| `gora-tools/gora` | 1205220004 | GORA's own repository: its `v0.2.0-rc.1` to `v0.2.0-rc.6` are lightweight and outside the rc grammar |

## The tags of `gora-fixtures`

Created through the API, as GORA's App creates a candidate's tag: an annotated tag is a
`POST /git/tags` (the tag's name, its message, the object it points at and that object's type)
and then a `POST /git/refs` (`refs/tags/<name>` at the tag object's sha); a lightweight tag is
the `POST /git/refs` alone, at a commit. The invocations were the maintainer's and are not
kept; the calls are as the recorded objects show them. All point at the one commit of `main`,
`51c9f03ab5fdfb0ddd440eecd0dde61ba5bb7e01`, but `v0.0.3-rc`.

| Tag | Kind | What it is for |
| --- | --- | --- |
| `v0.0.1-rc` | annotated | its message is the one `rc_tag_message` writes, for the artifact `fixture`: `tags.recorded` accepts it |
| `v0.0.2-rc` | annotated | a message with no `Gora-candidate` trailer: `tags.recorded` refuses it |
| `v0.0.3-rc` | annotated | a tag of `v0.0.1-rc`'s tag object, not of a commit: the reader refuses to follow a chain |
| `v0.0.4-rc` | lightweight | a ref that points at a commit |
| `vnext` | lightweight | the prefix is a string prefix: `tags/v` matches it, and it is no rc tag |

## The files

| File | Request | What it shows |
| --- | --- | --- |
| `repo.http` | `GET /repos/gora-tools/gora-fixtures` | `id`, and `full_name` as the host spells it |
| `repo-renamed.http` | `GET /repos/gora-tools/gora-fixtures-before` | the old name's real 301, with its `Location`; read with curl, since `gh api` follows a redirect |
| `ref-main.http` | `GET .../git/ref/heads/main` | a branch's ref, its object a commit |
| `tag-refs.http` | `GET .../git/matching-refs/tags/v` | the five refs, in one response, with no `Link` header |
| `tag-refs-none.http` | `GET .../git/matching-refs/tags/fixture/v` | no match: `200 []` |
| `tag-v0.0.1-rc.http`, `tag-v0.0.2-rc.http`, `tag-v0.0.3-rc.http` | `GET .../git/tags/<sha>` | the three annotated tag objects |
| `tag-unknown.http` | `GET .../git/tags/1111111111111111111111111111111111111111` | an unknown tag sha: the real 404 |
| `gora-tag-refs.http` | `GET /repos/gora-tools/gora/git/matching-refs/tags/v` | six lightweight refs, `v0.2.0-rc.1` to `v0.2.0-rc.6` |

Tagger names, e-mail addresses and node ids in the bodies are the host's, as it answered.
