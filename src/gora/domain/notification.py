"""A release notification, independent of where it is shown (ADR-015).

The notifier builds one from release facts; an adapter renders it for its own medium and
owns every rule of that medium. Fields hold text already passed through ``gora.text.visible``.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Notification:
    summary: str  # one line: GORA constant words plus values
    detail: str  # one line under the summary
    link: str | None = None  # the pull request URL, promotion PR only
