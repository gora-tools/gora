"""An image registry's evidence, read through one port (ADR-006, ADR-018).

Registration verifies the evidence attached to a digest, and that evidence is kept in the
registry the image is in. ``Registry`` is what the services need of the adapter that reads
one, and each registry has an adapter that serves it (``gora.adapters.registries`` says which
one reads an image). It is owned by its consumer, as ``CodeHost`` is. The adapter imports
nothing of a service; the mapping that chooses an adapter names the port as the type its
factories return, so that mypy checks each adapter against it.
"""

from typing import Protocol


class Registry(Protocol):
    """The two reads of a digest and its evidence, and no write.

    The contract every adapter keeps:

    - **No write.** An adapter of this port holds no method that changes the registry.
    - **Every answer is checked against what was asked.** A body hashes to the digest it was
      asked by, its type is the expected one, and a value the registry sends that is not the
      one expected is the adapter's error, never a value.
    - **A missing tag is an error,** never an empty answer: ``bundles`` never answers empty,
      since an empty answer would read as no evidence required.
    - **A caller's mistake is a ``ValueError``,** raised before any request: a digest outside
      its grammar.
    """

    def manifest(self, digest: str) -> bytes:
        """The bytes of the image index or image manifest at ``digest``, which hash to it."""
        ...

    def bundles(self, digest: str) -> tuple[bytes, ...]:
        """The bytes of each Sigstore bundle attached to ``digest``, in the registry's order.

        At least one: a digest with no bundle is an error that names it.
        """
        ...
