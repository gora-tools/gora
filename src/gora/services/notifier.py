"""Release notifier — optional Slack notifications on release events (ADR-015).

No-op when Slack is disabled or unconfigured, so GORA stays CLI-only by default.
Slack reflects Git state; it never drives it (ADR-002). The notifier takes a
poster (the bounded Slack adapter, or None) so it is trivially testable offline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from gora.domain.notification import Notification
from gora.text import visible

if TYPE_CHECKING:
    from gora.adapters.slack import SlackMessage
    from gora.config.settings import Settings


class SlackPoster(Protocol):
    """The one operation the notifier needs from the Slack adapter."""

    def post_message(self, channel: str, notification: Notification) -> SlackMessage: ...


# GORA's own words: plain ASCII, no markup, no emoji-shortcode shape. Values are shown
# through ``visible`` and nothing else here; escaping for Slack is the adapter's job.
CUT_RC = "Cut RC {version}"
PROMOTED = "Promoted {version} to {stage}"
ROLLED_BACK = "Rolled back {stage} to {version}"
PROMOTION_PR = "Promotion PR for {version} to {stage}"
DETAIL = "Digest {digest}"


class Notifier:
    """Posts formatted release events to a channel, or no-ops when inactive."""

    def __init__(self, adapter: SlackPoster | None, channel: str, *, enabled: bool = True) -> None:
        self._adapter = adapter
        self._channel = channel
        self._enabled = enabled

    @property
    def active(self) -> bool:
        return self._enabled and self._adapter is not None and bool(self._channel)

    def notify(self, notification: Notification) -> SlackMessage | None:
        if not self.active:
            return None
        assert self._adapter is not None  # narrowed by `active`
        return self._adapter.post_message(self._channel, notification)

    def cut_rc(self, version: str, digest: str) -> SlackMessage | None:
        return self.notify(Notification(CUT_RC.format(version=visible(version)), _detail(digest)))

    def promoted(self, stage: str, version: str, digest: str) -> SlackMessage | None:
        summary = PROMOTED.format(version=visible(version), stage=visible(stage))
        return self.notify(Notification(summary, _detail(digest)))

    def rolled_back(self, stage: str, version: str, digest: str) -> SlackMessage | None:
        summary = ROLLED_BACK.format(stage=visible(stage), version=visible(version))
        return self.notify(Notification(summary, _detail(digest)))

    def promotion_pr(self, stage: str, version: str, digest: str, url: str) -> SlackMessage | None:
        summary = PROMOTION_PR.format(version=visible(version), stage=visible(stage))
        return self.notify(Notification(summary, _detail(digest), visible(url)))


def _detail(digest: str) -> str:
    return DETAIL.format(digest=visible(digest))


def build_notifier(settings: Settings) -> Notifier:
    """Build a notifier from settings — active only if Slack is fully configured."""
    if settings.slack_enabled and settings.slack_bot_token and settings.slack_channel:
        from gora.adapters.slack import SlackAdapter

        return Notifier(
            SlackAdapter(settings.slack_bot_token), settings.slack_channel, enabled=True
        )
    return Notifier(None, "", enabled=False)
