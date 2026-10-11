"""Tests for the release notifier (no network, fake poster).

The notifier is Slack-agnostic: it builds a ``Notification`` of structured spans and hands it
to a poster. Every value goes through ``visible`` first; Slack-specific rules are the
adapter's job (tests/unit/test_slack_adapter.py).
"""

import json
import re
from typing import Any

import httpx
import pytest

from gora.adapters.slack import SlackAdapter, SlackMessage
from gora.config.settings import Settings
from gora.domain.notification import Notification, Span
from gora.services import notifier as notifier_module
from gora.services.notifier import Notifier, build_notifier


class FakePoster:
    def __init__(self) -> None:
        self.posts: list[tuple[str, Notification]] = []

    def post_message(self, channel: str, notification: Notification) -> SlackMessage:
        self.posts.append((channel, notification))
        return SlackMessage(channel=channel, ts="1.1")


def _only(fake: FakePoster) -> Notification:
    assert len(fake.posts) == 1
    channel, notification = fake.posts[0]
    assert channel == "C1"
    return notification


def _w(text: str) -> Span:
    return Span(text, "word")


def _v(text: str) -> Span:
    return Span(text, "value")


def _d(text: str) -> Span:
    return Span(text, "digest")


_NOTE = Notification(summary=(_w("s"),), detail=(_w("d"),))


def test_notify_noop_when_disabled() -> None:
    fake = FakePoster()
    assert Notifier(fake, "C1", enabled=False).notify(_NOTE) is None
    assert fake.posts == []


def test_notify_noop_when_no_adapter() -> None:
    assert Notifier(None, "C1").notify(_NOTE) is None


def test_notify_noop_when_no_channel() -> None:
    fake = FakePoster()
    assert Notifier(fake, "").notify(_NOTE) is None
    assert fake.posts == []


def test_build_notifier_inactive_by_default() -> None:
    assert build_notifier(Settings()).active is False


def test_build_notifier_active_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    # Avoid constructing a live httpx client: swap the adapter for a fake.
    import gora.adapters.slack as slack_mod

    monkeypatch.setattr(slack_mod, "SlackAdapter", lambda token: FakePoster())
    settings = Settings(slack_enabled=True, slack_bot_token="fake-sentinel-x", slack_channel="C1")
    assert build_notifier(settings).active is True


# --- Notification.plain ---------------------------------------------------------------------


def test_plain_joins_the_span_texts() -> None:
    note = Notification(
        summary=(_w("Promoted "), _v("1.0"), _w(" to "), _v("dev")),
        detail=(_w("Digest: "), _d("sha256:abc")),
    )
    assert note.plain() == ("Promoted 1.0 to dev", "Digest: sha256:abc")


def test_plain_of_nothing_is_empty() -> None:
    assert Notification(summary=(), detail=()).plain() == ("", "")


# --- the three events, plain values ---------------------------------------------------------


def test_promoted() -> None:
    fake = FakePoster()
    Notifier(fake, "C1").promoted("staging", "0.2.0-rc.5", "sha256:abc")
    assert _only(fake) == Notification(
        summary=(_w("Promoted "), _v("0.2.0-rc.5"), _w(" to "), _v("staging")),
        detail=(_w("Digest: "), _d("sha256:abc")),
    )


def test_rolled_back() -> None:
    fake = FakePoster()
    Notifier(fake, "C1").rolled_back("staging", "0.2.0-rc.4", "sha256:abc")
    assert _only(fake) == Notification(
        summary=(_w("Rolled back "), _v("staging"), _w(" to "), _v("0.2.0-rc.4")),
        detail=(_w("Digest: "), _d("sha256:abc")),
    )


def test_promotion_pr() -> None:
    fake = FakePoster()
    Notifier(fake, "C1").promotion_pr("staging", "0.2.0-rc.5", "sha256:abc", "https://h/o/r/pull/7")
    assert _only(fake) == Notification(
        summary=(_w("Promotion PR for "), _v("0.2.0-rc.5"), _w(" to "), _v("staging")),
        detail=(_w("Digest: "), _d("sha256:abc")),
        link="https://h/o/r/pull/7",
    )


def test_only_the_promotion_pr_carries_a_link() -> None:
    fake = FakePoster()
    n = Notifier(fake, "C1")
    n.promoted("s", "v", "d")
    n.rolled_back("s", "v", "d")
    assert [note.link for _, note in fake.posts] == [None, None]


# --- hostile values -------------------------------------------------------------------------

E_ACUTE = "\N{LATIN SMALL LETTER E WITH ACUTE}"
GRINNING = "\N{GRINNING FACE}"
# (value, as shown inside rich_text, as shown in the fallback text)
# Literal triples on purpose: the expectation does not come from the code under test. Inside
# rich_text nothing is entity-escaped; in the fallback & < > are.
HOSTILE = [
    ("<!here>", "<!here>", "&lt;!here&gt;"),
    ("<@U123|x>", "<@U123|x>", "&lt;@U123|x&gt;"),
    ("a&b", "a&b", "a&amp;b"),
    ("*bold*", "*bold*", "*bold*"),
    ("_it_", "_it_", "_it_"),
    ("~s~", "~s~", "~s~"),
    ("`code`", "`code`", "`code`"),
    (":rocket:", ":rocket:", ":rocket:"),
    ("a\nb", "a\\x0ab", "a\\x0ab"),
    ("a\N{RIGHT-TO-LEFT OVERRIDE}b", "a\\u202eb", "a\\u202eb"),
    ("a\N{ZERO WIDTH SPACE}b", "a\\u200bb", "a\\u200bb"),
    ("a\N{LINE SEPARATOR}b", "a\\u2028b", "a\\u2028b"),
    ("a\\b", "a\\\\b", "a\\\\b"),
    (E_ACUTE, E_ACUTE, E_ACUTE),
    (GRINNING, GRINNING, GRINNING),
]
HOSTILE_IDS = [repr(h[0]) for h in HOSTILE]

URL_PREFIX = "https://h/"
DEFAULTS = {
    "version": "1.0.0",
    "stage": "dev",
    "digest": "sha256:abc",
    "url": URL_PREFIX + "pull/7",
}

# Which value slots each event takes.
SLOTS = {
    "promoted": ["version", "stage", "digest"],
    "rolled_back": ["version", "stage", "digest"],
    "promotion_pr": ["version", "stage", "digest", "url"],
}
CASES = [(event, slot) for event, slots in SLOTS.items() for slot in slots]
CASE_IDS = [f"{event}-{slot}" for event, slot in CASES]


def _summary(event: str, version: str, stage: str) -> list[tuple[str, str]]:
    """The summary as (kind, text) pairs, the spans the notifier is specified to build."""
    return {
        "promoted": [("word", "Promoted "), ("value", version), ("word", " to "), ("value", stage)],
        "rolled_back": [
            ("word", "Rolled back "), ("value", stage), ("word", " to "), ("value", version)
        ],
        "promotion_pr": [
            ("word", "Promotion PR for "), ("value", version), ("word", " to "), ("value", stage)
        ],
    }[event]  # fmt: skip


def _emit(notifier: Notifier, event: str, values: dict[str, str]) -> None:
    v, s, d, u = values["version"], values["stage"], values["digest"], values["url"]
    if event == "promotion_pr":
        notifier.promotion_pr(s, v, d, u)
    else:
        getattr(notifier, event)(s, v, d)


def _shown(slot: str, column: str) -> dict[str, str]:
    """Defaults, with one slot replaced by its hostile text (url keeps its https prefix)."""
    shown = dict(DEFAULTS)
    shown[slot] = URL_PREFIX + column if slot == "url" else column
    return shown


@pytest.mark.parametrize(("value", "plain", "fallback"), HOSTILE, ids=HOSTILE_IDS)
@pytest.mark.parametrize(("event", "slot"), CASES, ids=CASE_IDS)
def test_notifier_shows_every_value_literally(
    event: str, slot: str, value: str, plain: str, fallback: str
) -> None:
    fake = FakePoster()
    _emit(Notifier(fake, "C1"), event, _shown(slot, value))
    shown = _shown(slot, plain)
    assert _only(fake) == Notification(
        summary=tuple(Span(t, k) for k, t in _summary(event, shown["version"], shown["stage"])),  # type: ignore[arg-type]
        detail=(_w("Digest: "), _d(shown["digest"])),
        link=shown["url"] if event == "promotion_pr" else None,
    )


WORDS = {
    "Promoted ",
    "Rolled back ",
    "Promotion PR for ",
    " to ",
    "Digest: ",
    "Image: ",
    "\n",
}
STYLE = {"word": None, "value": {"bold": True}, "digest": {"code": True}}


def _element(kind: str, text: str) -> dict[str, Any]:
    element: dict[str, Any] = {"type": "text", "text": text}
    if STYLE[kind]:
        element["style"] = STYLE[kind]
    return element


def _body(elements: list[dict[str, Any]], text: str) -> dict[str, Any]:
    return {
        "channel": "C1",
        "text": text,
        "mrkdwn": False,
        "unfurl_links": False,
        "unfurl_media": False,
        "blocks": [
            {
                "type": "rich_text",
                "elements": [{"type": "rich_text_section", "elements": elements}],
            }
        ],
    }


def _posted(event: str, values: dict[str, str]) -> dict[str, Any]:
    """Run one event through the notifier and the real adapter; return the JSON body sent."""
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "channel": "C1", "ts": "1.1"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    _emit(Notifier(SlackAdapter("fake-sentinel-0000", client=client), "C1"), event, values)
    assert len(captured) == 1
    return captured[0]


@pytest.mark.parametrize(("value", "plain", "fallback"), HOSTILE, ids=HOSTILE_IDS)
@pytest.mark.parametrize(("event", "slot"), CASES, ids=CASE_IDS)
def test_hostile_values_reach_slack_literal_inside_and_escaped_in_the_fallback(
    event: str, slot: str, value: str, plain: str, fallback: str
) -> None:
    body = _posted(event, _shown(slot, value))
    inside, outside = _shown(slot, plain), _shown(slot, fallback)
    elements = [_element(k, t) for k, t in _summary(event, inside["version"], inside["stage"])]
    elements += [
        _element("word", "\n"),
        _element("word", "Digest: "),
        _element("digest", inside["digest"]),
    ]
    summary_text = "".join(t for _, t in _summary(event, outside["version"], outside["stage"]))
    text = f"{summary_text}. Digest: {outside['digest']}"
    if event == "promotion_pr":
        elements += [
            _element("word", "\n"),
            {"type": "link", "url": inside["url"], "text": "Open pull request"},
        ]
        text += f" {outside['url']}"
    assert body == _body(elements, text)


def test_order_proof_ampersand_is_escaped_first() -> None:
    # A value that already looks like an entity is sent as typed inside rich_text and
    # arrives in the fallback as the escaped ampersand, not as a second-level entity.
    body = _posted("promoted", {**DEFAULTS, "version": "&lt;"})
    elements = body["blocks"][0]["elements"][0]["elements"]
    assert elements[1] == {"type": "text", "text": "&lt;", "style": {"bold": True}}
    assert body["text"] == "Promoted &amp;lt; to dev. Digest: sha256:abc"


@pytest.mark.parametrize("event", list(SLOTS))
def test_an_empty_value_is_omitted_never_sent_as_an_empty_element(event: str) -> None:
    empty = {"version": "", "stage": "", "digest": "", "url": ""}
    body = _posted(event, empty)
    elements = body["blocks"][0]["elements"][0]["elements"]
    assert elements, "the words are still there"
    assert [e for e in elements if e.get("type") == "text" and e["text"] == ""] == []
    assert all(e.get("text") != "" and e.get("url") != "" for e in elements)


@pytest.mark.parametrize("event", list(SLOTS))
def test_words_carry_no_style_and_styles_are_exactly_bold_and_code(event: str) -> None:
    body = _posted(event, DEFAULTS)
    elements = body["blocks"][0]["elements"][0]["elements"]
    styles = [e["style"] for e in elements if e.get("type") == "text" and "style" in e]
    assert all(style in ({"bold": True}, {"code": True}) for style in styles)
    for element in elements:
        if element.get("type") == "text" and "style" not in element:
            assert element["text"] in WORDS
    assert {"code": True} in styles  # the digest
    assert sum(1 for style in styles if style == {"code": True}) == 1


# --- constant strings -----------------------------------------------------------------------


def _words() -> list[str]:
    return [
        notifier_module.PROMOTED,
        notifier_module.ROLLED_BACK,
        notifier_module.PROMOTION_PR,
        notifier_module.TO,
        notifier_module.DIGEST,
        notifier_module.IMAGE,
    ]


@pytest.mark.parametrize("word", _words())
def test_words_are_ascii_and_carry_no_markup(word: str) -> None:
    assert word.isascii()
    assert not set(word) & set("&<>*_~`")
    # A colon followed by a space is allowed; a ":name:" shape is not.
    assert re.search(r":[a-z0-9_+-]+:", word) is None


def test_the_words_are_the_specified_ones() -> None:
    assert _words() == [
        "Promoted ",
        "Rolled back ",
        "Promotion PR for ",
        " to ",
        "Digest: ",
        "Image: ",
    ]


def test_a_body_from_ascii_values_is_ascii() -> None:
    for event in SLOTS:
        body = _posted(event, DEFAULTS)
        assert json.dumps(body, ensure_ascii=False).isascii()


# --- the image line (ADR-015) ---------------------------------------------------------------

IMAGE = "ghcr.io/test/service"
EVENTS = {
    "promoted": lambda n, image: n.promoted("dev", "1.2.0-rc.1", "sha256:abc", image=image),
    "rolled_back": lambda n, image: n.rolled_back("dev", "1.1.9", "sha256:abc", image=image),
    "promotion_pr": lambda n, image: n.promotion_pr(
        "dev", "1.2.0-rc.1", "sha256:abc", "https://h/pull/7", image=image
    ),
}


@pytest.mark.parametrize("event", sorted(EVENTS))
def test_with_an_image_the_detail_is_the_pull_safe_reference(event: str) -> None:
    fake = FakePoster()
    EVENTS[event](Notifier(fake, "C1"), IMAGE)
    assert _only(fake).detail == (_w("Image: "), _d(f"{IMAGE}@sha256:abc"))


@pytest.mark.parametrize("event", sorted(EVENTS))
def test_without_an_image_the_detail_stays_the_digest(event: str) -> None:
    fake = FakePoster()
    EVENTS[event](Notifier(fake, "C1"), None)
    assert _only(fake).detail == (_w("Digest: "), _d("sha256:abc"))


def test_the_image_is_shown_literally_too() -> None:
    fake = FakePoster()
    Notifier(fake, "C1").promoted("dev", "1.0.0", "sha256:a\nb", image="<!here>\u202e:x:")
    assert _only(fake).detail == (_w("Image: "), _d("<!here>\\u202e:x:@sha256:a\\x0ab"))


@pytest.mark.parametrize("event", sorted(EVENTS))
def test_the_image_line_reaches_slack_as_one_code_span(event: str) -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "channel": "C1", "ts": "1.1"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    EVENTS[event](Notifier(SlackAdapter("fake-sentinel-0000", client=client), "C1"), IMAGE)

    elements = captured[0]["blocks"][0]["elements"][0]["elements"]
    newline = elements.index({"type": "text", "text": "\n"})
    assert elements[newline + 1 : newline + 3] == [
        {"type": "text", "text": "Image: "},
        {"type": "text", "text": f"{IMAGE}@sha256:abc", "style": {"code": True}},
    ]
    assert f". Image: {IMAGE}@sha256:abc" in captured[0]["text"]


# --- the three states: off, skipped, posting ------------------------------------------------

SKIP = "(slack notify skipped: GORA_SLACK_ENABLED is true but {names} empty)"
TOKEN, CHANNEL = "GORA_SLACK_BOT_TOKEN", "GORA_SLACK_CHANNEL"


def _fake_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    import gora.adapters.slack as slack_mod

    monkeypatch.setattr(slack_mod, "SlackAdapter", lambda token: FakePoster())


def test_not_enabled_is_inactive_and_silent_whatever_else_is_set() -> None:
    for settings in (
        Settings(),
        Settings(slack_enabled=False, slack_bot_token="fake-sentinel-x", slack_channel="C1"),
        Settings(slack_enabled=False, slack_bot_token="", slack_channel=""),
    ):
        built = build_notifier(settings)
        assert built.active is False
        assert built.skipped is None


@pytest.mark.parametrize(
    ("token", "channel", "names"),
    [
        ("", "C1", TOKEN),
        ("fake-sentinel-x", "", CHANNEL),
        ("", "", f"{TOKEN} and {CHANNEL}"),
    ],
    ids=["token-missing", "channel-missing", "both-missing"],
)
def test_enabled_but_incomplete_is_inactive_and_says_what_is_missing(
    token: str, channel: str, names: str
) -> None:
    built = build_notifier(
        Settings(slack_enabled=True, slack_bot_token=token, slack_channel=channel)
    )
    assert built.active is False
    assert built.skipped == SKIP.format(names=names)


def test_the_skip_message_never_carries_a_value() -> None:
    settings = Settings(slack_enabled=True, slack_bot_token="fake-sentinel-9", slack_channel="")
    skipped = build_notifier(settings).skipped
    assert skipped is not None
    assert "fake-sentinel-9" not in skipped


def test_fully_configured_is_active_not_skipped_and_knows_its_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_adapter(monkeypatch)
    settings = Settings(slack_enabled=True, slack_bot_token="fake-sentinel-x", slack_channel="C1")
    built = build_notifier(settings)
    assert built.active is True
    assert built.skipped is None
    assert built.channel == "C1"
