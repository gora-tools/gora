"""Tests for the release notifier (no network — fake poster).

The notifier is Slack-agnostic: it builds a ``Notification`` and hands it to a poster.
Every value goes through ``visible`` first; Slack-specific escaping is the adapter's job
(tests/unit/test_slack_adapter.py).
"""

import json
import re
from typing import Any

import httpx
import pytest
from gora.domain.notification import Notification

from gora.adapters.slack import SlackAdapter, SlackMessage
from gora.config.settings import Settings
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


_NOTE = Notification(summary="s", detail="d")


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
    # Avoid constructing a live httpx client — swap the adapter for a fake.
    import gora.adapters.slack as slack_mod

    monkeypatch.setattr(slack_mod, "SlackAdapter", lambda token: FakePoster())
    settings = Settings(slack_enabled=True, slack_bot_token="xoxb-x", slack_channel="C1")
    assert build_notifier(settings).active is True


# --- the four events, plain values -------------------------------------------------------


def test_cut_rc() -> None:
    fake = FakePoster()
    Notifier(fake, "C1").cut_rc("0.2.0-rc.5", "sha256:abc")
    assert _only(fake) == Notification(summary="Cut RC 0.2.0-rc.5", detail="Digest sha256:abc")


def test_promoted() -> None:
    fake = FakePoster()
    Notifier(fake, "C1").promoted("staging", "0.2.0-rc.5", "sha256:abc")
    assert _only(fake) == Notification(
        summary="Promoted 0.2.0-rc.5 to staging", detail="Digest sha256:abc"
    )


def test_rolled_back() -> None:
    fake = FakePoster()
    Notifier(fake, "C1").rolled_back("staging", "0.2.0-rc.4", "sha256:abc")
    assert _only(fake) == Notification(
        summary="Rolled back staging to 0.2.0-rc.4", detail="Digest sha256:abc"
    )


def test_promotion_pr() -> None:
    fake = FakePoster()
    Notifier(fake, "C1").promotion_pr("staging", "0.2.0-rc.5", "sha256:abc", "https://h/o/r/pull/7")
    assert _only(fake) == Notification(
        summary="Promotion PR for 0.2.0-rc.5 to staging",
        detail="Digest sha256:abc",
        link="https://h/o/r/pull/7",
    )


def test_only_the_promotion_pr_carries_a_link() -> None:
    fake = FakePoster()
    n = Notifier(fake, "C1")
    n.cut_rc("v", "d")
    n.promoted("s", "v", "d")
    n.rolled_back("s", "v", "d")
    assert [note.link for _, note in fake.posts] == [None, None, None]


# --- hostile values ----------------------------------------------------------------------

# (value, as shown in plain_text, as shown in the fallback text, as shown in a link)
# Literal pairs on purpose: the expectation does not come from the code under test.
E_ACUTE = "\N{LATIN SMALL LETTER E WITH ACUTE}"
GRINNING = "\N{GRINNING FACE}"
HOSTILE = [
    ("<!here>", "<!here>", "&lt;!here&gt;", "&lt;!here&gt;"),
    ("<@U123|x>", "<@U123|x>", "&lt;@U123|x&gt;", "&lt;@U123%7Cx&gt;"),
    ("a&b", "a&b", "a&amp;b", "a&amp;b"),
    ("*bold*", "*bold*", "*bold*", "*bold*"),
    ("_it_", "_it_", "_it_", "_it_"),
    ("~s~", "~s~", "~s~", "~s~"),
    ("`code`", "`code`", "`code`", "`code`"),
    (":rocket:", ":rocket:", ":rocket:", ":rocket:"),
    ("a\nb", "a\\x0ab", "a\\x0ab", "a\\x0ab"),
    ("a\N{RIGHT-TO-LEFT OVERRIDE}b", "a\\u202eb", "a\\u202eb", "a\\u202eb"),
    ("a\N{ZERO WIDTH SPACE}b", "a\\u200bb", "a\\u200bb", "a\\u200bb"),
    ("a\N{LINE SEPARATOR}b", "a\\u2028b", "a\\u2028b", "a\\u2028b"),
    ("a\\b", "a\\\\b", "a\\\\b", "a\\\\b"),
    (E_ACUTE, E_ACUTE, E_ACUTE, E_ACUTE),
    ("\U0001f600", "\U0001f600", "\U0001f600", "\U0001f600"),
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
    "cut_rc": ["version", "digest"],
    "promoted": ["version", "stage", "digest"],
    "rolled_back": ["version", "stage", "digest"],
    "promotion_pr": ["version", "stage", "digest", "url"],
}
CASES = [(event, slot) for event, slots in SLOTS.items() for slot in slots]
CASE_IDS = [f"{event}-{slot}" for event, slot in CASES]


def _summary(event: str, version: str, stage: str) -> str:
    return {
        "cut_rc": f"Cut RC {version}",
        "promoted": f"Promoted {version} to {stage}",
        "rolled_back": f"Rolled back {stage} to {version}",
        "promotion_pr": f"Promotion PR for {version} to {stage}",
    }[event]


def _emit(notifier: Notifier, event: str, values: dict[str, str]) -> None:
    v, s, d, u = values["version"], values["stage"], values["digest"], values["url"]
    if event == "cut_rc":
        notifier.cut_rc(v, d)
    elif event == "promotion_pr":
        notifier.promotion_pr(s, v, d, u)
    else:
        getattr(notifier, event)(s, v, d)


def _shown(slot: str, column: str) -> dict[str, str]:
    """Defaults, with one slot replaced by its hostile text (url keeps its https prefix)."""
    shown = dict(DEFAULTS)
    shown[slot] = URL_PREFIX + column if slot == "url" else column
    return shown


@pytest.mark.parametrize(("value", "plain", "fallback", "link"), HOSTILE, ids=HOSTILE_IDS)
@pytest.mark.parametrize(("event", "slot"), CASES, ids=CASE_IDS)
def test_notifier_shows_every_value_literally(
    event: str, slot: str, value: str, plain: str, fallback: str, link: str
) -> None:
    fake = FakePoster()
    _emit(Notifier(fake, "C1"), event, _shown(slot, value))
    shown = _shown(slot, plain)
    assert _only(fake) == Notification(
        summary=_summary(event, shown["version"], shown["stage"]),
        detail=f"Digest {shown['digest']}",
        link=shown["url"] if event == "promotion_pr" else None,
    )


def _body(summary: str, detail: str, text: str, link_text: str | None) -> dict[str, Any]:
    elements: list[dict[str, Any]] = [{"type": "plain_text", "text": detail, "emoji": False}]
    if link_text is not None:
        elements.append({"type": "mrkdwn", "text": link_text, "verbatim": True})
    return {
        "channel": "C1",
        "text": text,
        "mrkdwn": False,
        "unfurl_links": False,
        "unfurl_media": False,
        "blocks": [
            {"type": "section", "text": {"type": "plain_text", "text": summary, "emoji": False}},
            {"type": "context", "elements": elements},
        ],
    }


def _posted(event: str, values: dict[str, str]) -> dict[str, Any]:
    """Run one event through the notifier and the real adapter; return the JSON body sent."""
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "channel": "C1", "ts": "1.1"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    _emit(Notifier(SlackAdapter("xoxb-fake-0000", client=client), "C1"), event, values)
    assert len(captured) == 1
    return captured[0]


@pytest.mark.parametrize(("value", "plain", "fallback", "link"), HOSTILE, ids=HOSTILE_IDS)
@pytest.mark.parametrize(("event", "slot"), CASES, ids=CASE_IDS)
def test_hostile_values_reach_slack_escaped_per_field(
    event: str, slot: str, value: str, plain: str, fallback: str, link: str
) -> None:
    body = _posted(event, _shown(slot, value))
    in_plain, in_text, in_link = _shown(slot, plain), _shown(slot, fallback), _shown(slot, link)
    text = f"{_summary(event, in_text['version'], in_text['stage'])}. Digest {in_text['digest']}"
    link_text = None
    if event == "promotion_pr":
        text += f" {in_text['url']}"
        link_text = f"<{in_link['url']}|Open pull request>"
    assert body == _body(
        _summary(event, in_plain["version"], in_plain["stage"]),
        f"Digest {in_plain['digest']}",
        text,
        link_text,
    )


def test_order_proof_ampersand_is_escaped_first() -> None:
    # A value that already looks like an entity must be shown as typed in plain_text and
    # arrive in the fallback text as the escaped ampersand, not as a second-level entity.
    body = _posted("promoted", {**DEFAULTS, "version": "&lt;"})
    assert body["blocks"][0]["text"]["text"] == "Promoted &lt; to dev"
    assert body["text"] == "Promoted &amp;lt; to dev. Digest sha256:abc"


def test_every_plain_text_object_says_emoji_false_explicitly() -> None:
    body = _posted("promotion_pr", DEFAULTS)
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "plain_text":
                found.append(node)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(body["blocks"])
    assert len(found) == 2
    assert all("emoji" in obj and obj["emoji"] is False for obj in found)


# --- constant strings ---------------------------------------------------------------------


def _templates() -> list[str]:
    return [
        notifier_module.CUT_RC,
        notifier_module.PROMOTED,
        notifier_module.ROLLED_BACK,
        notifier_module.PROMOTION_PR,
        notifier_module.DETAIL,
    ]


@pytest.mark.parametrize("template", _templates())
def test_templates_are_ascii_and_carry_no_markup(template: str) -> None:
    assert template.isascii()
    assert not set(template) & set("&<>*_~`")
    assert re.search(r":[a-z0-9_+-]+:", template) is None


def test_notifications_from_ascii_values_are_ascii() -> None:
    fake = FakePoster()
    n = Notifier(fake, "C1")
    n.cut_rc("v", "d")
    n.promoted("s", "v", "d")
    n.rolled_back("s", "v", "d")
    n.promotion_pr("s", "v", "d", "https://h/p")
    for _, note in fake.posts:
        assert note.summary.isascii()
        assert note.detail.isascii()
