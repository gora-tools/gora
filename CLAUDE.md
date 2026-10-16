# GORA — rules for agents

GORA is a deterministic GitOps release engine: the release control layer between CI, which
builds immutable artifacts, and CD, which deploys them. It promotes the same digest through the
stages of a project, through pull requests to the state it keeps in Git. What it is and how to
run it: [README.md](README.md). These rules bind cowork and every agent in this repository.

## The release model

`docs/architecture/release-model.md` is the map every change is checked against: its
vocabulary, its lifecycle and its laws (L1 to L25, section 10). It is not imported, because
of its size; `.claude/rules/release-model.md` has an agent read it in full before changing a
file under `src/`, `tests/`, `docs/adr/` or `docs/architecture/`. The ADRs in `docs/adr/`
hold the decisions behind it and are authoritative; their index is in README.

- **Stop and ask.** The model's rule for every change binds every agent, and extends to the
  ADRs: a contradiction stops the work and goes to the maintainer.
- **Architecture.** Deterministic code owns workflow sequencing (ADR-002); an LLM interprets
  intent and never controls privileged execution (ADR-010). Git is the authoritative state, and
  Slack only an interface. External systems are reached only through narrow, bounded adapters
  (ADR-006). No orchestration engine before it is justified (ADR-007). GORA stays a release
  engine (ADR-001). It supports feature-based delivery and is no one organization's delivery
  model; feature flags and application rollback belong to the application.
- **Where things go.** Source in `src/gora/`, laid out as ADR-008 shows; the stack is ADR-005.

## Documents

- **One fact, one place.** The map states the model; an ADR states a decision and why; README
  is the entry point, the configuration and the ADR index; TESTING.md is the test policy;
  `docs/reference/` is background, never a decision.
- **A decision changes through an ADR,** which amends the earlier ADRs in place: an "Amended"
  line in the header, a dated note where the text changed, and an entry under "Amendments".
  A new ADR is written only for a decision no ADR holds; an amendment comes in the pull
  request whose change needs it. In the same pull request, every other statement of the
  changed fact is found (search `docs/`, README, TESTING.md and this file) and corrected or
  noted.
- **Nothing claims more than is true.** "In force", "implemented" or "done" names the mechanism
  that makes it so; what is decided and not built is listed in release-model section 13.

## Verify

Python 3.12 (any patch locally) and uv from its floor in `pyproject.toml`; the exact versions,
which CI runs, are declared in `deployments/docker/Dockerfile` (TESTING rule 3). Every target is
in the `Makefile`, and the policy behind them is TESTING.md.

```bash
make sync           # install uv.lock exactly (--locked)
make check          # source checks, once per commit: ruff, shellcheck, format --check, mypy
make behaviour      # behaviour checks: pytest
make verify         # both, on this machine
make verify-export  # pre-push gate: make verify on committed HEAD, in a fresh export
make verify-linux   # behaviour checks on HEAD in a Linux container of this host's platform
make docker-smoke   # build the release image natively, run it read-only and offline
```

- **CI is the gate of record** (ADR-021). Local runs save a round trip; they are never
  evidence.
- **Commit first, then run the pre-push gate:** `make verify-export`, plus `make verify-linux`
  from macOS. Both check committed `HEAD` and refuse a dirty tree.
- A leftover gate container stops the next run (exit 2), and the message names the
  `docker rm -f` that clears it (TESTING rule 8).

## Pull requests

A pull request that adds or changes a check carries its **red-first proof in its
description**: the command and its red output, on the code before the change or with the
defect injected (ADR-016 rule 1). The squash commit keeps it on `main`. A handoff never
limits a PR description so that the proof does not fit. Making the proof checks out or
restores files, so the handoff of such a pull request carries the disk-guard of
`~/workspace/CLAUDE.md`. Pull requests on this repository go through `gh` (GitHub).

An agent never submits a review, not even a comment review: an approval, and the maintainer's
`/approve` on a pull request they opened, are a person's decision (L25, ADR-026 section 13).

## Notes

Where this repository keeps its open queue and its settled decisions. `/park` reads these
addresses rather than guessing, so they stay accurate or the skill writes to the wrong place.

    Backlog: ~/workspace/notes/gora/backlog.md
    ADR:     docs/adr/   (`NNNN-title-with-dashes.md`, as adr-tools and MADR do)

Two addresses and nothing else. *Why* the queue lives outside this repository:
`~/workspace/docs/adr/0017-decisions-travel-queues-stay.md`. *Who* may write these paths:
Agent guardrails in `~/workspace/CLAUDE.md` — that rule lives in one place, on purpose.

## Commit and delegation

Conventional Commits, no AI authorship, and the cowork and agent handoff protocol with its
guardrails live in `~/workspace/CLAUDE.md`; only this repository's rules are here.
