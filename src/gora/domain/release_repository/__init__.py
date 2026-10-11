"""The release files of a release repository (ADR-026): their format, models and schemas.

Pure: callers pass bytes and values, nothing here reads a file or the network.

- ``yaml12``: the strict YAML 1.2 loader (section 10).
- ``canonical``: the canonical writer, the RFC 8785 serialisation and the policy digest
  (sections 10 and 15).
- ``v1alpha1``: the models of ``gora.tools/v1alpha1``, each holding the rules its file holds
  on its own (sections 3 to 7).
- ``files``: the layout of a release repository and the parsing of one file (sections 2, 3).
- ``tree``: the rules between the release files of one commit (sections 2, 3, 5, 9, 14).
- ``formats``: the field formats that are not identifiers (sections 4 to 7).
- ``keys``: the structural check of a signer's public key (section 3, ADR-027 section 7).
- ``inputs``: the input glob matcher, and the release paths it removes first (section 9).
- ``schema``: the published JSON Schemas (section 11).

No command uses them yet.
"""
