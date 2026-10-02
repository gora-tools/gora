"""A release notification, independent of where it is shown (ADR-015).

The notifier builds one from release facts; an adapter renders it for its own medium and
owns every rule of that medium. It is structured, not formatted: each span says what kind of
text it is, so emphasis is chosen by the adapter from the kind and never read from
characters in the text. Value and digest spans hold text already passed through
``gora.text.visible``; word spans are GORA constants.
"""

from dataclasses import dataclass
from typing import Literal

Kind = Literal["word", "value", "digest"]


@dataclass(frozen=True)
class Span:
    text: str
    kind: Kind


@dataclass(frozen=True)
class Notification:
    summary: tuple[Span, ...]  # the headline: GORA words and values
    detail: tuple[Span, ...]  # the line under it
    link: str | None = None  # the pull request URL, promotion PR only

    def plain(self) -> tuple[str, str]:
        """The summary and the detail as plain text, span texts joined."""
        return (
            "".join(span.text for span in self.summary),
            "".join(span.text for span in self.detail),
        )
