"""The registry adapter on GHCR, on answers recorded from GHCR (ADR-006, the Registry Adapter).

The fixtures in ``tests/fixtures/registry/ghcr`` are what ``ghcr.io`` answered for the image
index of run 46029301799 of ``ghcr.io/gora-tools/gora``,
``sha256:2001a74729cdbfcde78bc50c216cce4783966fa56f618416ee8fb0770389c7d7``, recorded on
2026-11-27 anonymously by the control plane, each body checked against its digest.
``recorded.json`` lists each one's request path, status, content type, digest and size. A blob
is recorded as its redirect's status and host only: the ``Location`` is a signed link that
expires and is not recorded, so the test's own is synthetic, of the measured shape, on the
storage host.

The suite is offline (TESTING rule 6): the answers are served through ``mock_transport``, as
streams, byte for byte, with the content types recorded.
"""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest

from gora.adapters.ghcr import GhcrRegistry, RegistryError
from gora.adapters.ghcr.adapter import MAX_REFERRERS
from gora.adapters.registries import REGISTRIES, check_image, registry
from tests.audit_fakes import mock_transport

FIXTURES = Path(__file__).parent.parent / "fixtures" / "registry" / "ghcr"
IMAGE = "ghcr.io/gora-tools/gora"
PATH = "gora-tools/gora"
INDEX = "sha256:2001a74729cdbfcde78bc50c216cce4783966fa56f618416ee8fb0770389c7d7"
FALLBACK_TAG = "sha256-2001a74729cdbfcde78bc50c216cce4783966fa56f618416ee8fb0770389c7d7"
OTHER = "sha256:" + "ab" * 32
STORAGE = "pkg-containers.githubusercontent.com"
SERVED = "fake-sentinel-registry-1"  # made up, in no shape the scan reads (test_egress_echo.py)
BUNDLE = "application/vnd.dev.sigstore.bundle.v0.3+json"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"

INDEX_BYTES = (FIXTURES / "index.json").read_bytes()
BUNDLE_1 = (FIXTURES / "bundle-1.json").read_bytes()
BUNDLE_2 = (FIXTURES / "bundle-2.json").read_bytes()
RECORDED = json.loads((FIXTURES / "recorded.json").read_text(encoding="utf-8"))


def _sha(body: bytes) -> str:
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


class Answer:
    """What a fake host answers for a path: a status, a content type and a body."""

    def __init__(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.body, self.content_type, self.status = body, content_type, status


class Fake:
    """GHCR and its storage host, as recorded, and every request they receive.

    ``manifests`` is keyed by the reference asked (a digest, or the fallback tag), ``blobs`` by
    the blob's digest. A blob's path answers ``307`` with a synthetic ``Location`` on the
    storage host, which serves the bytes.
    """

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.token_status = 200
        self.manifests: dict[str, Answer] = {}
        self.blobs: dict[str, bytes] = {}
        for entry in RECORDED:
            if "file" not in entry:
                continue
            body = (FIXTURES / entry["file"]).read_bytes()
            kind = entry["content_type"]
            if entry["path"].startswith("/v2/gora-tools/gora/manifests/"):
                self.manifests[entry["path"].rpartition("/")[2]] = Answer(body, kind)
            else:
                self.blobs[entry["digest"]] = body

    def transport(self) -> httpx2.MockTransport:
        return mock_transport(self._answer)

    def hosts(self) -> list[str]:
        return [request.url.host for request in self.requests]

    def replace_artifact(self, n: int, change: Callable[[dict[str, Any]], None]) -> str:
        """Artifact ``n`` (1 or 2) with ``change`` applied, in the place of the recorded one, and
        the fallback index pointing at it by its new digest. The new digest is returned."""
        artifact = (FIXTURES / f"artifact-{n}.json").read_bytes()
        old = _sha(artifact)
        manifest = json.loads(artifact)
        change(manifest)
        body = json.dumps(manifest).encode()
        new = _sha(body)
        del self.manifests[old]
        self.manifests[new] = Answer(body, OCI_MANIFEST)
        self._repoint(old, new)
        return new

    def _repoint(self, old: str, new: str) -> None:
        listed = self.manifests[FALLBACK_TAG]
        self.manifests[FALLBACK_TAG] = Answer(
            listed.body.replace(old.encode(), new.encode()), listed.content_type
        )

    def _answer(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        path = request.url.path
        if request.url.host == STORAGE:
            body = self.blobs[path.rpartition("/")[2]]
            return httpx2.Response(
                200, content=body, headers={"Content-Type": "application/octet-stream"}
            )
        assert request.url.host == "ghcr.io", f"a request to {request.url.host}"
        if path == "/token":
            if self.token_status != 200:
                return httpx2.Response(self.token_status, json={"errors": [{"code": "DENIED"}]})
            return httpx2.Response(200, json={"token": SERVED})
        prefix = f"/v2/{PATH}/"
        reference = path.removeprefix(prefix + "manifests/")
        if path.startswith(prefix + "manifests/"):
            answer = self.manifests.get(reference)
            if answer is None:
                return httpx2.Response(404, json={"errors": [{"code": "MANIFEST_UNKNOWN"}]})
            headers = {"Content-Type": answer.content_type}
            return httpx2.Response(answer.status, content=answer.body, headers=headers)
        digest = path.removeprefix(prefix + "blobs/")
        assert path.startswith(prefix + "blobs/") and digest in self.blobs, f"asked {path}"
        # GHCR's measured shape: a path ``/ghcr1/blobs/<digest>`` and a query of percent-encoded
        # parameters, here made up.
        location = f"https://{STORAGE}/ghcr1/blobs/{digest}?made-up=1&expires=2&signature=x3%2By%3D"
        return httpx2.Response(307, headers={"Location": location})


def adapter(fake: Fake) -> GhcrRegistry:
    return GhcrRegistry(PATH, transport=fake.transport())


def refusal(fake: Fake, call: Callable[[GhcrRegistry], object]) -> str:
    with pytest.raises(RegistryError) as info:
        call(adapter(fake))
    return str(info.value)


# --- the recorded answers -----------------------------------------------------------------------


def test_the_manifest_of_the_index_is_the_bytes_ghcr_answered() -> None:
    assert adapter(Fake()).manifest(INDEX) == INDEX_BYTES


def test_the_bundles_of_the_index_are_the_two_recorded_ones_in_the_indexs_order() -> None:
    assert adapter(Fake()).bundles(INDEX) == (BUNDLE_1, BUNDLE_2)


def test_the_recorded_answers_are_what_the_digests_say() -> None:
    """Calibration: the fixtures hash to the digests ``recorded.json`` lists, so a refusal below
    is the adapter's and not a fixture's."""
    for entry in RECORDED:
        if "file" in entry:
            assert _sha((FIXTURES / entry["file"]).read_bytes()) == entry["digest"]
            assert len((FIXTURES / entry["file"]).read_bytes()) == entry["size"]
    assert _sha(INDEX_BYTES) == INDEX


# --- where requests go, and what they carry -----------------------------------------------------


def test_every_request_goes_to_ghcr_or_storage_and_the_token_is_asked_first_and_once() -> None:
    fake = Fake()
    reader = adapter(fake)

    reader.bundles(INDEX)
    reader.manifest(INDEX)

    assert set(fake.hosts()) == {"ghcr.io", STORAGE}
    paths = [request.url.path for request in fake.requests]
    assert paths[0] == "/token"
    assert paths.count("/token") == 1
    token = fake.requests[0]
    assert dict(token.url.params) == {"scope": f"repository:{PATH}:pull", "service": "ghcr.io"}
    assert "authorization" not in token.headers


def test_the_token_goes_to_ghcr_only() -> None:
    fake = Fake()

    adapter(fake).bundles(INDEX)

    for request in fake.requests[1:]:
        if request.url.host == "ghcr.io":
            assert request.headers["authorization"] == f"Bearer {SERVED}"
        else:
            assert "authorization" not in request.headers
    assert fake.hosts().count(STORAGE) == 2  # one for each bundle


def test_a_blob_is_fetched_from_the_location_ghcr_gave_and_no_header_of_the_first_request() -> None:
    fake = Fake()

    adapter(fake).bundles(INDEX)

    blob = next(request for request in fake.requests if request.url.host == STORAGE)
    assert blob.url.path == f"/ghcr1/blobs/{_sha(BUNDLE_1)}"
    assert blob.url.query.startswith(b"made-up=1&")
    # The measured shape, percent-encoding included, is written as it is sent, so it is followed
    # byte for byte.
    assert blob.url.query.endswith(b"&signature=x3%2By%3D")
    assert "authorization" not in blob.headers


# --- what is refused ----------------------------------------------------------------------------


def test_a_manifest_that_does_not_hash_to_the_digest_asked_is_refused() -> None:
    fake = Fake()
    fake.manifests[INDEX] = Answer(INDEX_BYTES + b" ", OCI_INDEX)

    assert "does not hash to the digest asked" in refusal(fake, lambda r: r.manifest(INDEX))


@pytest.mark.parametrize("media", ["application/json", "text/html", ""])
def test_a_content_type_that_is_not_an_oci_index_or_manifest_is_refused(media: str) -> None:
    fake = Fake()
    fake.manifests[INDEX] = Answer(INDEX_BYTES, media)

    assert "not an OCI image index or an OCI image manifest" in refusal(
        fake, lambda r: r.manifest(INDEX)
    )


def test_a_media_type_that_is_not_an_oci_index_or_manifest_is_refused() -> None:
    body = json.dumps(
        {"schemaVersion": 2, "mediaType": "application/vnd.docker.distribution.manifest.v2+json"}
    ).encode()
    fake = Fake()
    fake.manifests[_sha(body)] = Answer(body, OCI_MANIFEST)

    assert "not an OCI image index or an OCI image manifest" in refusal(
        fake, lambda r: r.manifest(_sha(body))
    )


def test_a_content_type_and_a_media_type_that_differ_are_refused() -> None:
    fake = Fake()
    fake.manifests[INDEX] = Answer(INDEX_BYTES, OCI_MANIFEST)  # the body says it is an index

    assert "the Content-Type and the mediaType differ" in refusal(fake, lambda r: r.manifest(INDEX))


def test_a_fallback_index_over_the_cap_is_refused() -> None:
    entries = [
        {"mediaType": OCI_MANIFEST, "size": 1, "digest": OTHER, "artifactType": BUNDLE}
        for _ in range(MAX_REFERRERS + 1)
    ]
    fake = Fake()
    fake.manifests[FALLBACK_TAG] = Answer(
        json.dumps({"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": entries}).encode(),
        OCI_INDEX,
    )

    message = refusal(fake, lambda r: r.bundles(INDEX))

    assert f"more than {MAX_REFERRERS} entries" in message
    assert INDEX in message


def test_a_fallback_index_of_exactly_the_cap_is_read() -> None:
    """Calibration: the cap refuses one entry more, and only that. The entries are of another
    type, so none is fetched and the index holds no bundle."""
    entries = [
        {"mediaType": OCI_MANIFEST, "size": 1, "digest": OTHER, "artifactType": "x/y"}
    ] * MAX_REFERRERS
    fake = Fake()
    fake.manifests[FALLBACK_TAG] = Answer(
        json.dumps({"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": entries}).encode(),
        OCI_INDEX,
    )

    assert "holds no bundle" in refusal(fake, lambda r: r.bundles(INDEX))


def test_an_artifact_manifest_that_does_not_hash_to_its_entrys_digest_is_refused() -> None:
    fake = Fake()
    original = fake.manifests[
        "sha256:1c522e75d8611823d33103fa17a30a376daa3936c9d84d8aa54f167e92aaa5d0"
    ]
    fake.manifests[_sha(original.body)] = Answer(original.body + b" ", OCI_MANIFEST)

    assert "does not hash to the digest asked" in refusal(fake, lambda r: r.bundles(INDEX))


def test_an_artifact_manifest_whose_subject_is_another_digest_is_refused() -> None:
    fake = Fake()
    fake.replace_artifact(1, lambda m: m["subject"].update(digest=OTHER))

    assert "subject is not the digest asked" in refusal(fake, lambda r: r.bundles(INDEX))


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c.update(mediaType="application/vnd.oci.image.config.v1+json"),
        lambda c: c.update(digest=OTHER),
        lambda c: c.update(size=3),
    ],
    ids=["media type", "digest", "size"],
)
def test_an_artifact_whose_config_is_not_the_oci_empty_descriptor_is_refused(
    change: Callable[[dict[str, Any]], None],
) -> None:
    fake = Fake()
    fake.replace_artifact(1, lambda m: change(m["config"]))

    assert "config is not the OCI empty descriptor" in refusal(fake, lambda r: r.bundles(INDEX))


def test_the_artifact_type_ghcr_adds_to_the_config_is_ignored() -> None:
    """Calibration: the recorded config carries an ``artifactType``, and the recorded answers
    are read."""
    config = json.loads((FIXTURES / "artifact-1.json").read_bytes())["config"]

    assert "artifactType" in config
    assert adapter(Fake()).bundles(INDEX) == (BUNDLE_1, BUNDLE_2)


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m.update(layers=[]),
        lambda m: m.update(layers=m["layers"] * 2),
        lambda m: m["layers"][0].update(mediaType="application/octet-stream"),
    ],
    ids=["no layer", "two layers", "a layer of another type"],
)
def test_an_artifact_without_exactly_one_layer_of_the_bundle_type_is_refused(
    change: Callable[[dict[str, Any]], None],
) -> None:
    fake = Fake()
    fake.replace_artifact(1, change)

    assert "exactly one layer of the bundle's type" in refusal(fake, lambda r: r.bundles(INDEX))


def test_a_blob_that_does_not_hash_to_its_digest_is_refused() -> None:
    fake = Fake()
    fake.blobs[_sha(BUNDLE_1)] = BUNDLE_1[:-1] + b" "

    assert "does not hash to its digest" in refusal(fake, lambda r: r.bundles(INDEX))


def test_a_blob_that_does_not_have_its_descriptors_size_is_refused() -> None:
    fake = Fake()
    fake.replace_artifact(1, lambda m: m["layers"][0].update(size=len(BUNDLE_1) + 1))

    assert "does not have its descriptor's size" in refusal(fake, lambda r: r.bundles(INDEX))


def test_a_fallback_tag_that_answers_404_is_an_error_that_names_the_digest() -> None:
    fake = Fake()
    del fake.manifests[FALLBACK_TAG]

    message = refusal(fake, lambda r: r.bundles(INDEX))

    assert INDEX in message
    assert "404" in message


def test_a_fallback_index_with_no_entry_of_the_bundle_type_is_an_error_not_an_empty_answer() -> (
    None
):
    fake = Fake()
    listed = fake.manifests[FALLBACK_TAG]
    fake.manifests[FALLBACK_TAG] = Answer(
        listed.body.replace(BUNDLE.encode(), b"x/y"), listed.content_type
    )

    message = refusal(fake, lambda r: r.bundles(INDEX))

    assert INDEX in message
    assert "holds no bundle" in message


def test_an_entry_of_another_artifact_type_is_ignored_and_not_fetched() -> None:
    fake = Fake()
    listed = json.loads(fake.manifests[FALLBACK_TAG].body)
    ignored = {"mediaType": OCI_MANIFEST, "size": 9, "digest": OTHER, "artifactType": "text/x-sbom"}
    listed["manifests"].insert(1, ignored)
    fake.manifests[FALLBACK_TAG] = Answer(json.dumps(listed).encode(), OCI_INDEX)

    assert adapter(fake).bundles(INDEX) == (BUNDLE_1, BUNDLE_2)
    assert all(OTHER not in str(request.url) for request in fake.requests)


NOT_PUBLIC_WORDS = "the package is not public; GORA reads public packages only"
NO_PACKAGE_WORDS = "no public package of that name"


def test_a_token_endpoint_that_answers_401_says_the_package_is_not_public() -> None:
    fake = Fake()
    fake.token_status = 401

    message = refusal(fake, lambda r: r.manifest(INDEX))

    assert message.startswith("GET /token -> 401")
    assert message.endswith(" (the package is not public; GORA reads public packages only)")
    assert NO_PACKAGE_WORDS not in message
    assert fake.hosts() == ["ghcr.io"]


def test_a_token_endpoint_that_answers_403_says_there_is_no_public_package_of_that_name() -> None:
    fake = Fake()
    fake.token_status = 403

    message = refusal(fake, lambda r: r.manifest(INDEX))

    assert message.startswith("GET /token -> 403")
    assert message.endswith(" (no public package of that name)")
    assert NOT_PUBLIC_WORDS not in message
    assert "public packages only" not in message
    assert fake.hosts() == ["ghcr.io"]


def test_a_token_endpoint_that_fails_otherwise_does_not_say_public_packages_only() -> None:
    fake = Fake()
    fake.token_status = 500

    message = refusal(fake, lambda r: r.manifest(INDEX))

    assert "public packages" not in message
    assert NO_PACKAGE_WORDS not in message


@pytest.mark.parametrize("status", [401, 403])
def test_a_manifest_request_refused_after_the_token_carries_neither_wording(status: int) -> None:
    """The words are the token request's alone: a 401 or a 403 of a manifest, with the token
    served, need not mean a private package."""
    fake = Fake()
    fake.manifests[INDEX] = Answer(b"{}", OCI_INDEX, status=status)

    message = refusal(fake, lambda r: r.manifest(INDEX))

    assert message.startswith(f"GET /v2/{PATH}/manifests/{INDEX} -> {status}")
    assert NOT_PUBLIC_WORDS not in message
    assert NO_PACKAGE_WORDS not in message
    assert "public" not in message


@pytest.mark.parametrize("token", ["", "has space", "x" * 5000, None, 7])
def test_a_token_that_is_not_a_bearer_token_is_refused_and_not_shown(token: object) -> None:
    def answer(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"token": token})

    reader = GhcrRegistry(PATH, transport=mock_transport(answer))

    with pytest.raises(RegistryError) as info:
        reader.manifest(INDEX)

    assert "not an object with a token that is a Bearer token" in str(info.value)
    if isinstance(token, str) and token:
        assert token not in str(info.value)


# --- what a caller got wrong, before any request -----------------------------------------------


def _no_request(request: httpx2.Request) -> httpx2.Response:
    raise AssertionError("a request was sent")


@pytest.mark.parametrize(
    "digest", ["", "sha256:abc", "sha256:" + "A" * 64, "md5:" + "a" * 32, INDEX + "\n"]
)
def test_a_digest_outside_the_grammar_is_a_value_error_before_any_request(digest: str) -> None:
    reader = GhcrRegistry(PATH, transport=mock_transport(_no_request))

    with pytest.raises(ValueError, match="digest"):
        reader.manifest(digest)
    with pytest.raises(ValueError, match="digest"):
        reader.bundles(digest)


@pytest.mark.parametrize(
    "image", ["quay.io/gora-tools/gora", "docker.io/library/alpine", "localhost:5000/a/b"]
)
def test_an_image_on_another_registry_is_a_value_error_that_names_its_host(image: str) -> None:
    host = image.partition("/")[0]

    with pytest.raises(ValueError) as info:
        registry(image, transport=mock_transport(_no_request))

    assert str(info.value) == f"no adapter reads the registry {host}"
    with pytest.raises(ValueError, match="no adapter reads the registry"):
        check_image(image)


@pytest.mark.parametrize(
    "image",
    [
        "",
        "gora-tools/gora",
        "ghcr.io",
        "ghcr.io/Gora/UPPER",
        "ghcr.io/gora:tag",
        f"ghcr.io/a@{INDEX}",
    ],
)
def test_an_image_outside_the_grammar_is_a_value_error_before_any_request(image: str) -> None:
    with pytest.raises(ValueError, match="image must be"):
        registry(image, transport=mock_transport(_no_request))


def test_an_image_whose_host_holds_a_bidirectional_control_is_refused_by_the_grammar() -> None:
    """U+202E, built with ``chr`` so that no source file holds it: ``IMAGE`` refuses the name
    before a registry is looked up, and the message does not hold the character."""
    override = chr(0x202E)
    with pytest.raises(ValueError, match="image must be") as info:
        check_image(f"ghcr.io{override}/x/y")

    assert override not in str(info.value)


def test_a_repository_outside_the_grammar_is_a_value_error() -> None:
    with pytest.raises(ValueError, match="GHCR repository"):
        GhcrRegistry("Gora/UPPER", transport=mock_transport(_no_request))


def test_ghcr_is_the_one_registry_and_its_entry_is_the_adapter() -> None:
    assert list(REGISTRIES) == ["ghcr.io"]
    assert isinstance(registry(IMAGE, transport=Fake().transport()), GhcrRegistry)
    check_image(IMAGE)
