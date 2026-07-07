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

That is exactly ADR-004. It demonstrated, in real production use, that:

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

## What to carry into GORA's v0.2 Release BOM

The current v0.2 (Release BOM) sketch in ADR-003 is thinner than this proven model. When
v0.2 is formalized (Milestone 7), fold in: the `status` lifecycle, global + per-artifact
**locking**, a **hotfix counter**, the **dependency graph** (`dependsOn`) with a shared-
library flag, `artifact_type`, `promotedBy`, and separate per-artifact metadata. Keep the
manifest **digest-only and thin** — absorb the concepts, not the heavier pipeline machinery.
