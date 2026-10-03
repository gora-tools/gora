# ADR-021: Platforms — One Image Index, Every Gate on Native Hardware

**Status:** Accepted (decision); implementation pending
**Date:** 2026-10-03

---

## Context

- The release image is built for `linux/amd64` only. Users run Kubernetes nodes on both
  `linux/amd64` and `linux/arm64` (AWS Graviton, Ampere, Apple silicon for local clusters),
  and Kubernetes tools publish one image for both (Argo CD, Kargo, cert-manager).
- The local container gates run `linux/amd64` because CI does. On Apple silicon that is
  emulation (Rosetta), and on 2026-10-02 it hung the suite twice: processes started from a
  thread pool deadlocked the interpreter, and nothing inside the process ran again (TESTING
  rule 8). On the same Mac the test run also takes about 2.6 times as long under emulation
  (102 s against 39 s natively).
- The gate mixes two kinds of check. Lint, format, type and shell checks read source text: their
  result does not depend on the machine. Tests and the image smoke run code: their result can.
  One source-check tool, `shellcheck-py`, ships no wheel for `linux/arm64` and would build from
  source, downloading a binary at install time.
- GitHub's hosted `linux/arm64` runners (`ubuntu-24.04-arm`) are available to private
  repositories since 2026-01-29, within the plan's free minutes.

The number 020 is pinned to the Project Intelligence ADR (ADR-014), so this ADR is 021.

## Decision

### 1. Two platforms, one image index, one digest

GORA supports `linux/amd64` and `linux/arm64`. A candidate is **one OCI image index** with one
image manifest per platform. The index digest is the release identity (ADR-003): it is the
digest the manifest records, the digest every stage promotes and the final release retags
(ADR-018 section 7). A node pulls the index and runs its own platform.

### 2. Each platform is built and smoke-tested on its own hardware

The release workflow builds each platform on a native runner (`ubuntu-24.04` for amd64,
`ubuntu-24.04-arm` for arm64), pushes it by digest without a tag, and runs the smoke test on
that pushed digest on the same runner. A final job assembles the index from the two digests.
No build step runs under emulation.

### 3. Evidence covers every digest a node can pull (amends ADR-018 section 2)

- **Signature:** the index and each platform manifest.
- **Provenance:** each platform manifest, from the job that built it; and the index, from the
  job that assembled it. All from the reusable workflow (SLSA Build L3).
- **SBOM:** one CycloneDX document per platform manifest, because an SBOM describes one
  filesystem.

`cut-rc` verifies the index and every platform manifest in it. Any missing or failing piece
refuses the candidate. The exact attestation subjects GHCR stores are checked when ADR-018 is
implemented (its open questions).

### 4. Gates are placed by what they read

- **Source checks** — ruff check, ruff format `--check`, mypy, shellcheck, and the gate-change
  check (ADR-016 rule 7). Their result is the same on every machine, so they run **once** per
  commit: in CI in one job on `ubuntu-24.04`, and locally on the developer's own machine.
- **Behaviour checks** — the test suite, the image build and its smoke run. They run on
  **every supported platform, on native hardware**: in CI as a matrix over `ubuntu-24.04` and
  `ubuntu-24.04-arm`; locally on the host operating system and in a Linux container of the
  host's own architecture.
- The development dependencies are split the same way: the source-check tools in one group,
  the test tools in another. A behaviour job installs only its group, so no platform installs a
  tool that has no build for it. `uv.lock` stays one universal lock.

### 5. Emulation is never a gate

Locally, the container gates default to the host's native architecture. Another platform can
still be requested for diagnosis; such a run says it is emulated and is never reported as
evidence that a gate passed. Native hardware for the other platform is CI's job.

### 6. Releases are built only in CI

A release image is built only by the release workflow on a pushed candidate tag (ADR-018
section 7). A locally built image carries no provenance from the trusted identity, so GORA
cannot accept it as a candidate (ADR-018 section 4). Local builds exist for the smoke test.

## Why not otherwise

- **amd64 only, arm64 later.** Moving from a single image to an index changes what a release
  digest points at after users have pinned digests. Deciding before the first verified release
  avoids that.
- **Cross-building with QEMU on one runner.** Slower, and the build and smoke would run under
  emulation, the failure mode of 2026-10-02.
- **The emulated platform as the local gate.** It hung, it is slow, and its result is not what
  native hardware produces; CI has the native hardware.
- **Every check on every platform.** Source checks give identical results, cost runner minutes
  twice, and pull in tools some platforms cannot install.

## Consequences

- CI gets three kinds of job: `check` (once), `test` (amd64, arm64) and `image` (build and
  smoke, amd64, arm64). Which jobs a ruleset requires is set by a human after the first run.
- The Makefile separates the source checks from the behaviour checks; `make verify` still runs
  both on the developer's machine. `PLATFORM` defaults to the host's architecture, and
  `docker-platform-check` keeps refusing a platform the host cannot run.
- The release workflow builds per platform, assembles the index, and attaches evidence to the
  index and to each platform manifest; ADR-018 is implemented on that workflow.
- TESTING rule 2, the CLAUDE.md Verify section and the ADR-016 enforcement table are updated
  by the implementing pull request, together with the Makefile and CI.
- Rosetta and the emulated platform stay available on a developer's machine for diagnosis;
  GORA's gates do not need them.
- `linux/arm64` runners for private repositories have 2 vCPUs; the arm64 jobs are slower.

## Sources

- arm64 hosted runners in private repositories, GitHub changelog 2026-01-29:
  https://github.blog/changelog/2026-01-29-arm64-standard-runners-are-now-available-in-private-repositories/
- OCI image index specification: https://github.com/opencontainers/image-spec/blob/main/image-index.md
- shellcheck-py release files (no linux aarch64 wheel): https://pypi.org/project/shellcheck-py/#files
