# GORA Testing Policy

Authoritative — as binding as an ADR. In the spirit of Debian Policy: quality is a **rule,
not a wish**. GORA is a release-control mechanism, so it holds itself to a higher bar than
what it manages. "Release when it's ready."

## Non-negotiable rules

1. **CI parity.** `make verify` (ruff check + ruff format --check + mypy + pytest) mirrors CI
   exactly and must be green before any push. No exceptions.
2. **Gates reproduce production, not the working tree.** `make docker-smoke` builds from
   `git archive HEAD` (tracked files only) and runs in CI on every PR. Local build checks
   for a dependency/Dockerfile change require **commit → docker-smoke** (a dirty tree tests
   the old HEAD).
3. **Determinism first — dogfood the thesis.** Committed lockfiles, digest-pinned artifacts,
   reproducible builds. Apply GORA's own standard to GORA.
4. **Every bug becomes a test or check.** A defect found once gets a regression guard so it
   cannot return.
5. **Test the real thing, not a proxy.** Supply chain verified by `cosign verify` /
   attestation checks in CI, never assumed. The release pipeline self-verifies its own
   artifact (signature + SBOM + provenance) before it is trusted.
6. **No network and no machine config in automated tests — every layer.** External systems
   are behind bounded adapters (ADR-006) and are **mocked** in unit and integration tests
   alike (`httpx.MockTransport`, fakes, FastAPI `TestClient` + dependency overrides). Settings
   come from code defaults and the test itself: no `.env` from the cwd, no `GORA_*` from the
   shell. Enforced by autouse guards in `tests/conftest.py`: any connect or DNS lookup fails
   the test, even when the code under test swallows the error. Fast, deterministic, offline.
7. **Fail closed.** On any failure the delegated agent stops and reports — no work-arounds.

## Test layers

- **Unit** — pure domain invariants (immutability, digest preservation, supersession, linear
  promotion) and service sequencing with mocked adapters.
- **Integration** — CLI end-to-end via the Typer runner; API via FastAPI `TestClient`.
- **Build / supply chain** — image builds from a clean tracked-files export; signature, SBOM,
  and provenance verified from the digest.
- **Self-release** — the top-level integration test: GORA releases and promotes itself.

## Production-grade requirement

GitHub and Slack are **production requirements** (ADR-014). Their adapters must be prod-grade
tested: narrow, typed, audited, mocked in unit tests, and exercised end-to-end. New adapters
or transports do not merge without tests at every applicable layer above.

## Coverage expectation

Every public behaviour has a test. Prefer a small number of meaningful tests over coverage
theatre — but a code path that can fail silently in a release is a code path that must be
tested. When in doubt, add the test.
