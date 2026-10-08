# ADR-025: No Dependencies Between Artifacts — a Migration Is an Artifact Like Any Other

**Status:** Accepted
**Date:** 2026-10-08
**Amends:** ADR-003, ADR-004, ADR-013, ADR-019, ADR-022, ADR-023

---

## Context

ADR-003 lets an artifact declare the artifacts it depends on (`dependsOn`). ADR-022 section 9
promotes artifacts linked that way in one group pull request, in dependency order, and section
11 gives database migrations rules of their own: forward only, expand before contract, a
contract step promoted "only when no rollback target in that stage needs the old schema", a
rollback of kind B that never moves a migration and is refused when an application "does not run
on the current schema", and a declaration of the oldest application a migration serves, left to
a schema decided later.

A review of the model on 2026-10-08, while ADR-024 was written, found that this part does not
hold:

- **The contract condition is never met.** Every version a stage held and that is not rejected
  stays a rollback target, so some target always needs the old schema.
- **A group skips versions.** A group carries one version of the dependency; in a stage whose
  gate is `approval`, the versions of the dependency between the one the stage holds and that
  one are never offered there, which ADR-024 section 4 forbids.
- **A group collides with the order of each artifact**: the dependency has its own open
  promotion (L14) and its own place in the stage's order.
- **Which version of the dependency** a group carries was left to the time of registration,
  which is the order in which builds finish, not the order of history.

Every rule tried on 2026-10-08 to repair this had GORA judge whether one artifact works with
another: what a migration removes, which application versions use it, which version of one
artifact another was built against. Each left cases where versions wait for each other forever.
Whether two versions work together is a property of the code, which the project's development
owns and tests; a mechanism does not decide whether a developer was right. GORA promotes
versions and returns a stage to what it held before.

The decision was taken with the maintainer on 2026-10-08.

## Decision

### 1. No dependencies between artifacts

- GORA has no dependencies between artifacts and no kinds of artifact. Policy declares
  artifacts, their inputs, stages, gates and verifications; it has no `dependsOn`.
- A promotion pull request moves one artifact. There is no group promotion. Each artifact is
  promoted on its own, through the stage loop and the gates of ADR-024, in its own order.
- Whether versions of different artifacts work together, and in what order they must reach a
  stage, is the project's: its CI and review hold it, as they hold L4, and an `approval` gate is
  where a person who knows waits for the right moment.

### 2. A migration is an artifact like any other

- A database migration is an image of the project's migration tool (Flyway, Liquibase, Alembic,
  goose, an EF Core bundle), with its own inputs, its own versions, the same stages and the same
  loop. GORA promotes it; the CD system runs it.
- Forward-only migrations, expand before contract, and a schema that works with the application
  version before it are the project's discipline. GORA does not read what a migration does.

### 3. A rollback returns to what was before

- A rollback of kind A returns one artifact, a migration included, to a version the stage held
  before (ADR-022 section 10). A rollback of kind B returns every artifact of the stage to its
  version in the named platform state, migrations included; an artifact the state does not
  contain keeps its version.
- What an older version of a migration does to the data when it runs again is the project's,
  like everything else a version does.

### 4. The law

L21 is reworded through this ADR: "GORA has no dependencies between artifacts and no kinds of
artifact: a migration is an artifact like any other. Whether versions work together, and in
what order they reach a stage, is the project's."

## Why not otherwise

- **A group promotion** (ADR-022 section 9 as first written). It skips versions of the dependency
  in an `approval` stage, collides with the dependency's own open promotion, and needs a rule for
  which version it carries.
- **An order computed from `dependsOn`**: a version enters a stage only after the dependency it
  was merged with. It is a judgement of compatibility from history: across code repositories
  there is no tested combination to follow, an application may depend on a migration that is
  rejected for a reason unrelated to it, and the relation is declared by hand where the code
  already knows. It adds a state in which versions wait for each other, for no rule the
  project's own CI does not hold better.
- **Rules of GORA's own for migrations**: a contract step that waits for applications, a
  rollback refused below a schema, a migration that never rolls back. Each needs GORA to know
  what a migration does; the first is never met, and the others were sources of versions waiting
  for each other. A stage that returns to what it held before is the one rollback that needs no
  such knowledge.

## Consequences

Amendments, each recorded in the amended ADR:

- **ADR-003:** `dependsOn` is not kept for v0.2, and `artifact_type` gives GORA no rule.
- **ADR-004:** a rollback of kind B returns every artifact of the state, migrations included.
- **ADR-013:** there is no group promotion branch.
- **ADR-019:** section 1 (a project's artifacts do not depend on each other).
- **ADR-022:** section 9 (no group promotion), section 10 (kind B returns every artifact, and
  GORA does not check a schema), section 11 (a migration is an artifact like any other) and
  section 12 (no `dependsOn` group).
- **ADR-023:** section 3 (a project's artifacts have no dependencies; a promotion carries one
  artifact; a rollback may carry a migration), section 6 (no group branch) and section 10 (kind
  B returns every artifact).

In `docs/architecture/release-model.md`: the vocabulary, the platform states, rollback and
migrations sections, the connect conditions and L21.

Nothing is left to build for this ADR beyond what ADR-024 builds: GORA has no dependency to
compute.

## Sources

- Martin Fowler, "ParallelChange" (expand, migrate, contract: a discipline of the code):
  https://martinfowler.com/bliki/ParallelChange.html
- Pramod Sadalage and Martin Fowler, "Evolutionary Database Design":
  https://martinfowler.com/articles/evodb.html
