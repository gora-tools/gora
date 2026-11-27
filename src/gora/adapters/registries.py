"""Which adapter reads an image: the registry its name begins with (ADR-006).

An image is named by its repository, ``<registry>/<path>``, the form ``IMAGE`` accepts
(``gora.domain.release.identifiers``). The part before the first ``/`` is the registry, and the
adapter that reads it is one entry of one read-only mapping. A second registry is a second
entry: there is no registration function and no plug-in mechanism, so what GORA reads is what
this module names.

``check_image`` is the one check of an image: ``IMAGE`` and the registry's entry, with no
request made. ``registry`` takes an image, checks it, and returns the port (``Registry``). A
caller's error is a ``ValueError`` before any request: an image outside ``IMAGE``, or on a
registry with no entry, which is named through ``gora.text.visible``. A message is value-free
but for the registry.
"""

from collections.abc import Callable, Mapping
from types import MappingProxyType

import httpx2

from gora.adapters.ghcr import GhcrRegistry
from gora.domain.release.identifiers import IMAGE
from gora.services.registry import Registry
from gora.text import visible

type Factory = Callable[[str, httpx2.BaseTransport | None], Registry]


def _ghcr(path: str, transport: httpx2.BaseTransport | None) -> Registry:
    return GhcrRegistry(path, transport=transport)


# One entry for each registry GORA reads. The adapter is checked against ``Registry`` where its
# factory returns it. It is a constant of the code: no setting adds a registry.
REGISTRIES: Mapping[str, Factory] = MappingProxyType({"ghcr.io": _ghcr})


def check_image(image: str) -> None:
    """``ValueError`` unless ``image`` is a name ``IMAGE`` accepts on a registry GORA reads.

    The grammar, then the registry's entry; nothing is requested.
    """
    problem = IMAGE.problem(image)
    if problem is not None:
        raise ValueError(f"image {problem}")
    host = image.partition("/")[0]
    if host not in REGISTRIES:
        raise ValueError(f"no adapter reads the registry {visible(host)}")


def registry(image: str, *, transport: httpx2.BaseTransport | None = None) -> Registry:
    """The adapter that reads ``image``, by the registry the name begins with."""
    check_image(image)
    host, _, path = image.partition("/")
    return REGISTRIES[host](path, transport)
