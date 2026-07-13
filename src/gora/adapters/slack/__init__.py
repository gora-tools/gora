"""Slack tool adapter (bounded outgoing notifications)."""

from gora.adapters.slack.adapter import SlackAdapter, SlackError, SlackMessage

__all__ = ["SlackAdapter", "SlackError", "SlackMessage"]
