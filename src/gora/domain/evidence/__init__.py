"""Evidence domain: the rules GORA holds a build's evidence to (ADR-018 sections 2 and 3,
ADR-027 section 7).

Pure: callers pass bytes and values, nothing here reads a registry, a certificate or the
network. Verifying a signature and reading a certificate's fields are the adapters'.

- ``certificate``: the fields of a CI signer's certificate, and their binding to the
  repository, the ref and the commit its role requires (ADR-027 section 7).
- ``provenance``: SLSA provenance in the one form GORA accepts, GitHub's ``workflow/v1``, its
  binding to the artifact's declaration and to the builder's certificate, and the build level
  it gives (ADR-018 sections 2 and 3).

No command uses them yet.
"""
