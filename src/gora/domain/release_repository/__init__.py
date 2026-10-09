"""The format layer of the release files (ADR-026 sections 10 and 15).

Pure: callers pass bytes and values, nothing here reads a file or the network. ``yaml12`` is
the strict YAML 1.2 loader, ``canonical`` the canonical writer, the RFC 8785 serialisation and
the policy digest. No command uses them yet; GORA still reads schema v0.1 with PyYAML.
"""
