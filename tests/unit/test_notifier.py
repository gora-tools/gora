"""Tests for the release notifier (no network — fake poster)."""

import pytest

from gora.adapters.slack import SlackMessage
from gora.config.settings import Settings
from gora.services.notifier import Notifier, build_notifier


class FakePoster:
    def __init__(self) -> None:
        self.posts: list[tuple[str, str]] = []

    def post_message(self, channel: str, text: str) -> SlackMessage:
        self.posts.append((channel, text))
        return SlackMessage(channel=channel, ts="1.1")


def test_notify_noop_when_disabled() -> None:
    fake = FakePoster()
    assert Notifier(fake, "C1", enabled=False).notify("x") is None
    assert fake.posts == []


def test_notify_noop_when_no_adapter() -> None:
    assert Notifier(None, "C1").notify("x") is None


def test_notify_noop_when_no_channel() -> None:
    fake = FakePoster()
    assert Notifier(fake, "").notify("x") is None
    assert fake.posts == []


def test_promoted_posts_formatted() -> None:
    fake = FakePoster()
    Notifier(fake, "C1").promoted("staging", "0.2.0-rc.5", "sha256:abc")
    channel, text = fake.posts[0]
    assert channel == "C1"
    assert "Promoted" in text and "staging" in text and "0.2.0-rc.5" in text


def test_build_notifier_inactive_by_default() -> None:
    assert build_notifier(Settings()).active is False


def test_build_notifier_active_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    # Avoid constructing a live httpx client — swap the adapter for a fake.
    import gora.adapters.slack as slack_mod

    monkeypatch.setattr(slack_mod, "SlackAdapter", lambda token: FakePoster())
    settings = Settings(slack_enabled=True, slack_bot_token="xoxb-x", slack_channel="C1")
    assert build_notifier(settings).active is True
