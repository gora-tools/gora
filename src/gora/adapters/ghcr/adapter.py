"""GHCR registry adapter: a digest and the Sigstore bundles attached to it, read-only (ADR-006).

Registration verifies the evidence attached to a digest (ADR-018 sections 2 to 4), and this is
how GORA reads it. It serves the port ``gora.services.registry.Registry``: ``manifest(digest)``,
the bytes of the image index or image manifest at that digest, and ``bundles(digest)``, the
bytes of each Sigstore bundle attached to it.

- **GHCR only, public packages only.** The two hosts are constants: ``GHCR_API`` for the token
  and the API, and ``STORAGE_API`` for a blob's one redirect, which receives no token. The
  token is anonymous, asked once per adapter and lazily, for ``repository:<path>:pull``. A
  private package is the token endpoint's ``401``, and a name with no package its ``403``
  (measured, ADR-006): the first is an error that says the package is not public and GORA reads
  public packages only, the second one that says there is no public package of that name (which
  a private package of another owner would answer as well). ``egress`` writes those words, from
  the token request's ``Host.refusals``; the adapter reads no error's text.
- **Every answer is checked against what was asked.** A manifest hashes to its digest, and its
  type is an OCI image index or an OCI image manifest, the same in its ``Content-Type`` and its
  ``mediaType``. A blob hashes to its digest and has its descriptor's size.
- **Bundles come from the referrers fallback tag** ``sha256-<hex>``, since GHCR answers ``404``
  to the OCI 1.1 referrers API: an OCI image index whose entries of the bundle's
  ``artifactType`` are read, the others ignored. Each such manifest must have the digest of its
  entry, name the digest asked as its ``subject``, hold the OCI empty descriptor as its config
  (GHCR adds an ``artifactType`` there, which is ignored) and exactly one layer of the bundle's
  type, which is the bundle. Annotations are unsigned and are not read. ``bundles`` never
  answers empty.
- **Bounded.** Every request is sent by ``gora.adapters.egress.BoundedRequest``, and a blob's
  one redirect is the exception that module states. The adapter takes no URL and no client,
  only, for a test, a transport.
"""

import hashlib
import re
from collections.abc import Callable
from typing import Any

import httpx2

from gora.adapters import egress
from gora.adapters.egress import Answer, UnexpectedError
from gora.domain.release.identifiers import DIGEST, IMAGE

GHCR_API = "https://ghcr.io"
STORAGE_API = "https://pkg-containers.githubusercontent.com"
TOKEN_PATH = "/token"
GHCR_HOST = "ghcr.io"

INDEX_TYPE = "application/vnd.oci.image.index.v1+json"
MANIFEST_TYPE = "application/vnd.oci.image.manifest.v1+json"
BUNDLE_TYPE = "application/vnd.dev.sigstore.bundle.v0.3+json"
EMPTY_TYPE = "application/vnd.oci.empty.v1+json"
EMPTY_DIGEST = "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
EMPTY_SIZE = 2

# The most entries a fallback index may hold: 10 times the most measured, 3 (the signature, the
# provenance and the SBOM of a platform manifest, ADR-006). More is an error, never a read.
MAX_REFERRERS = 30

# The token of the ``Bearer`` scheme GORA sends: RFC 6750 section 2.1, ``b64token``, at most
# 4096 characters. The measured token is 52.
_B64TOKEN = re.compile(r"[A-Za-z0-9._~+/-]+=*")
MAX_TOKEN = 4096

# What the token endpoint's refusals mean, each no more than was measured (ADR-006): 401 for a
# package that is private, 403 for a name with no package, which a private package of another
# owner would answer as well.
NOT_PUBLIC = "the package is not public; GORA reads public packages only"
NO_PUBLIC_PACKAGE = "no public package of that name"
NOT_A_TOKEN = "the token answer is not an object with a token that is a Bearer token"
NOT_AN_OBJECT = "the answer is not a JSON object"
NOT_THE_DIGEST = "the body does not hash to the digest asked"
NOT_THE_TYPE = "the type is not an OCI image index or an OCI image manifest"
TYPES_DIFFER = "the Content-Type and the mediaType differ"
NOT_AN_INDEX = "the fallback tag is not an OCI image index"
TOO_MANY = f"the fallback index holds more than {MAX_REFERRERS} entries"
NOT_AN_ENTRY = "an entry of the fallback index is not a descriptor"
NOT_THE_ENTRY = "the manifest does not hash to its entry's digest"
NOT_THE_SUBJECT = "the manifest's subject is not the digest asked"
NOT_EMPTY = "the manifest's config is not the OCI empty descriptor"
NOT_ONE_LAYER = "the manifest does not have exactly one layer of the bundle's type"
NOT_THE_BLOB = "the blob does not hash to its digest"
NOT_THE_SIZE = "the blob does not have its descriptor's size"
NO_BUNDLE = "the fallback index holds no bundle"


class RegistryError(RuntimeError):
    """A registry call failed: an error status, a transport error, or a bad response."""


# OCI puts the registry's text in ``errors[].message``, never in a top-level field, and egress
# shows only a top-level string: GHCR sends no ``message`` there, so none is shown.
_TEXT_FIELD = "message"
# GitHub's token shapes, the tokens GHCR also takes. The anonymous token is the credential of
# the request, which egress already hides.
_TOKEN_SHAPES = (r"gh[pousr]_", r"github_pat_")

# The manifests and the blobs. It names no refusal: a 403 there need not be a private package.
_GHCR = egress.Host(
    api=GHCR_API,
    error=RegistryError,
    text_field=_TEXT_FIELD,
    rate_limits={},
    token_shapes=_TOKEN_SHAPES,
)

# The token request: the same host, and the words of its two refusals.
_GHCR_TOKEN = egress.Host(
    api=GHCR_API,
    error=RegistryError,
    text_field=_TEXT_FIELD,
    rate_limits={},
    token_shapes=_TOKEN_SHAPES,
    refusals={401: NOT_PUBLIC, 403: NO_PUBLIC_PACKAGE},
)


def _sha256(body: bytes) -> str:
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _checked[T](operation: str, read: Callable[[], T]) -> T:
    """Read a value out of an answer; one that is not the expected one is a RegistryError."""
    try:
        return read()
    except UnexpectedError as exc:
        raise RegistryError(f"{operation} failed: unexpected response ({exc.reason})") from exc
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        raise RegistryError(f"{operation} failed: unexpected response") from exc


def _object(answer: Answer) -> dict[str, Any]:
    data = answer.json()
    if not isinstance(data, dict):
        raise UnexpectedError(NOT_AN_OBJECT)
    return data


def _digest_of(value: Any) -> str:
    if not (isinstance(value, str) and DIGEST.is_valid(value)):
        raise UnexpectedError(NOT_AN_ENTRY)
    return value


def _size_of(value: Any) -> int:
    # ``isinstance`` admits a bool: the type is exactly int.
    if type(value) is not int or value < 0:
        raise UnexpectedError(NOT_AN_ENTRY)
    return value


class GhcrRegistry:
    """The two reads of a digest and its bundles on GHCR, and no write."""

    def __init__(
        self,
        repository: str,
        *,
        timeout: float = 10.0,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        problem = IMAGE.problem(f"{GHCR_HOST}/{repository}")
        if problem is not None:
            raise ValueError(f"GHCR repository: {problem}")
        self._repository = repository
        self._timeout = timeout
        self._transport = transport
        self._anonymous = egress.BoundedRequest(
            _GHCR_TOKEN,
            headers={"Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )
        self._authorized: egress.BoundedRequest | None = None

    def manifest(self, digest: str) -> bytes:
        """The bytes of the image index or image manifest at ``digest``."""
        self._check_digest(digest)
        return self._manifest(digest, digest, (INDEX_TYPE, MANIFEST_TYPE))[0]

    def bundles(self, digest: str) -> tuple[bytes, ...]:
        """The bytes of each Sigstore bundle attached to ``digest``, in the index's order."""
        self._check_digest(digest)
        tag = digest.replace(":", "-")
        try:
            _, data = self._manifest(tag, None, (INDEX_TYPE,))
            kept = _checked(f"GET {self._manifest_path(tag)}", lambda: self._entries(data))
        except RegistryError as exc:  # an absent tag is one: the error names the digest
            raise RegistryError(f"the bundles of {digest} could not be read: {exc}") from exc
        if not kept:
            raise RegistryError(f"the bundles of {digest} could not be read: {NO_BUNDLE}")
        return tuple(self._bundle(digest, entry) for entry in kept)

    # --- one request each ---

    def _check_digest(self, digest: str) -> None:
        problem = DIGEST.problem(digest)
        if problem is not None:
            raise ValueError(f"digest {problem}")

    def _manifest_path(self, reference: str) -> str:
        return f"/v2/{self._repository}/manifests/{reference}"

    def _manifest(
        self, reference: str, digest: str | None, types: tuple[str, ...]
    ) -> tuple[bytes, dict[str, Any]]:
        """A manifest by ``reference``, a digest or a tag, checked: against ``digest`` when it
        is one, for its type, which must be among ``types``, and as a JSON object."""
        path = self._manifest_path(reference)
        operation = f"GET {path}"
        answer = self._requests().send("GET", path)
        data = _checked(operation, lambda: self._typed(answer, digest, types))
        return answer.body, data

    @staticmethod
    def _typed(answer: Answer, digest: str | None, types: tuple[str, ...]) -> dict[str, Any]:
        if digest is not None and _sha256(answer.body) != digest:
            raise UnexpectedError(NOT_THE_DIGEST)
        data = _object(answer)
        media = data.get("mediaType")
        content = answer.headers.get("content-type", "").partition(";")[0].strip().lower()
        if media not in types or content not in types:
            raise UnexpectedError(NOT_THE_TYPE if types != (INDEX_TYPE,) else NOT_AN_INDEX)
        if media != content:
            raise UnexpectedError(TYPES_DIFFER)
        return data

    @staticmethod
    def _entries(data: dict[str, Any]) -> list[dict[str, Any]]:
        """The entries of the fallback index of the bundle's type, in order; the others, and
        what they say, are ignored."""
        entries = data["manifests"]
        if not isinstance(entries, list):
            raise UnexpectedError(NOT_AN_ENTRY)
        if len(entries) > MAX_REFERRERS:
            raise UnexpectedError(TOO_MANY)
        kept: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise UnexpectedError(NOT_AN_ENTRY)
            if entry.get("artifactType") == BUNDLE_TYPE:
                _digest_of(entry["digest"])
                kept.append(entry)
        return kept

    def _bundle(self, digest: str, entry: dict[str, Any]) -> bytes:
        """The bundle of one entry of the fallback index: its manifest, then its layer."""
        entry_digest = entry["digest"]
        path = self._manifest_path(entry_digest)
        operation = f"GET {path}"
        _, manifest = self._manifest(entry_digest, entry_digest, (MANIFEST_TYPE,))
        layer = _checked(operation, lambda: self._layer(manifest, digest))
        return self._blob(*layer)

    @staticmethod
    def _layer(manifest: dict[str, Any], digest: str) -> tuple[str, int]:
        subject = manifest["subject"]
        if not (isinstance(subject, dict) and subject["digest"] == digest):
            raise UnexpectedError(NOT_THE_SUBJECT)
        config = manifest["config"]
        if not (
            isinstance(config, dict)
            and config["mediaType"] == EMPTY_TYPE
            and config["digest"] == EMPTY_DIGEST
            and type(config["size"]) is int
            and config["size"] == EMPTY_SIZE
        ):
            raise UnexpectedError(NOT_EMPTY)
        layers = manifest["layers"]
        if not (
            isinstance(layers, list)
            and len(layers) == 1
            and isinstance(layers[0], dict)
            and layers[0]["mediaType"] == BUNDLE_TYPE
        ):
            raise UnexpectedError(NOT_ONE_LAYER)
        return _digest_of(layers[0]["digest"]), _size_of(layers[0]["size"])

    def _blob(self, digest: str, size: int) -> bytes:
        path = f"/v2/{self._repository}/blobs/{digest}"
        operation = f"GET {path}"
        answer = self._requests().send("GET", path, redirect=STORAGE_API)
        if _sha256(answer.body) != digest:
            raise RegistryError(f"{operation} failed: unexpected response ({NOT_THE_BLOB})")
        if len(answer.body) != size:
            raise RegistryError(f"{operation} failed: unexpected response ({NOT_THE_SIZE})")
        return answer.body

    # --- the token ---

    def _requests(self) -> egress.BoundedRequest:
        """The bounded request that carries the anonymous token, asked for on first use."""
        if self._authorized is None:
            token = self._token()
            self._authorized = egress.BoundedRequest(
                _GHCR,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": f"{INDEX_TYPE}, {MANIFEST_TYPE}",
                },
                timeout=self._timeout,
                transport=self._transport,
            )
        return self._authorized

    def _token(self) -> str:
        """An anonymous token for ``repository:<path>:pull``. The request sends no
        ``Authorization``; the token is no more than the credential of the requests after it."""
        params = {"scope": f"repository:{self._repository}:pull", "service": GHCR_HOST}
        # A 401 or a 403 is an error whose words egress writes, from ``_GHCR_TOKEN.refusals``.
        answer = self._anonymous.send("GET", TOKEN_PATH, params=params)
        return _checked(f"GET {TOKEN_PATH}", lambda: self._read_token(answer))

    @staticmethod
    def _read_token(answer: Answer) -> str:
        token = _object(answer).get("token")
        if not (
            isinstance(token, str)
            and len(token) <= MAX_TOKEN
            and _B64TOKEN.fullmatch(token) is not None
        ):
            raise UnexpectedError(NOT_A_TOKEN)
        return token
