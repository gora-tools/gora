# Prior art — BOM-based release model

This note captures a **prior, production-proven release model** that predates GORA and
validates its core thesis. It is recorded as institutional memory and as justification
for the GORA release contract (ADR-003) and promotion model (ADR-004). Only the model and
its lessons are captured here — no source system, organisation, or service names.

## What it was

A multi-service delivery system that coordinated the release of many services as a single
**platform release** using a Git-stored **Bill of Materials (BOM)**. A pipeline job
("analyzer") updated the BOM on every build; a separate promotion job moved a locked BOM to
production. Release status was reported to a wiki page.

## What it proved (why it matters for GORA)

The model already embodied GORA's central principle, in its own words:

> With immutable build versions, promotion does **not** retag or rebuild artifacts. The
> build artifact **is** the production artifact.

That is ADR-004: promotion never rebuilds. GORA adds one registry tag, the final version, to the
same digest when it enters `prod`, and never moves a tag (L10, L12). It demonstrated, in real
production use, that:

- a single Git-native contract can coordinate promotion of many services at once;
- promotion is a controlled state transition (with a manual approval gate), not a rebuild;
- the whole thing is auditable because the contract and its history live in Git.

GORA is therefore not a hypothesis — it is a clean re-architecture of a model that already
worked.

## The model (fields and lifecycle worth carrying forward)

**BOM top-level:**

- `bomVersion` — release-candidate tag; `platformVersion` — the release line.
- `status` — lifecycle: `rc → promoting → released`.
- `locked` — global lock; `hotfixCount` — hotfix promotion counter.
- `releasedAt`, `promotedBy` — audit fields.

**Per-service entry:**

- `artifactVersion` (immutable) + `buildCommit` — what was built.
- `git`, `artifact_type` (e.g. container / library), `locked` (per-service lock).
- `dependency` — flag marking a shared library (excluded from promotion UI).
- `dependsOn` — an explicit **dependency graph** between services.
- Fuller per-artifact metadata kept in separate files, not inlined in the BOM.

**Promotion job (shape):** read BOM → lock check → build a promotion plan → summary →
manual approval → lock + update BOM → tag repos / merge release branches → BOM metadata
commit (`status: released`) → changelog → promotion report. It supported **partial vs full
promotion** and **hotfix branches**.

## How GORA improves on it

- **Release identity is the digest** (`sha256:…`), not `version + commit`. This is true
  immutability — the same bytes move through every stage (ADR-003).
- **GitHub + GitOps** delivery, not a Groovy-scripted pipeline monolith.
- **Deterministic engine + bounded adapters** (ADR-002, ADR-006) instead of pipeline
  scripts owning workflow logic.
- **Supply chain built in** — SBOM + provenance + signing (ADR-011). The prior system had
  code scanning (SAST / quality) but no supply-chain attestation.
- **New surfaces** — an AI assistant that interprets intent but never controls privileged
  execution (ADR-010), and a read-only visualization of the mechanism.

## What GORA kept, and what it did not

This page records prior art; the decisions are in the ADRs.

| Prior-art concept | In GORA | Where decided |
| --- | --- | --- |
| A Git-native contract, history in Git | Kept: the manifest in the release repository, changed only by GORA's pull requests | ADR-003, ADR-019, L16 |
| The build artifact is the production artifact | Kept, by digest | ADR-003, ADR-004, L12 |
| Manual approval before production | Kept, as an `approval` gate: a review on the promotion pull request | ADR-023, L15 |
| `status` lifecycle `rc → promoting → released` | A version `X.Y.Z-rc`, its promotion pull requests, and `X.Y.Z` once it enters `prod` | ADR-024 |
| `platformVersion`, the release line | Platform states `prod/YYYY.MM.DD.N`; no release line | ADR-022, ADR-024 |
| Global and per-service `locked` | Not kept: a lock that holds changes back is a freeze | ADR-022 section 12, L20 |
| `hotfixCount`, hotfix branches | Not kept: a hotfix is an ordinary candidate from `main` | L1, L19 |
| Merging release branches at promotion | Not kept: there are no release branches | L1 |
| `dependsOn`, the shared-library flag | Not kept: no dependencies between artifacts; a library is not a deployable artifact | ADR-025, release-model section 12 |
| `artifact_type` | Gives GORA no rule: no kinds of artifact | ADR-025 |
| `promotedBy` | Not written to Git: who asked and who approved stay in the code host and the audit journal | ADR-023, ADR-017 |
| Per-service metadata in separate files | Each artifact has its own directory in the release repository | ADR-023 section 3 |
