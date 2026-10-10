"""Audit logging for mutating GORA operations.

Every mutating release operation MUST be auditable (ADR-002, ADR-004). This
logger produces one structured JSON line per event via the standard logging
system. It is injectable so transports and tests can capture, redirect, or
silence audit output.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("gora.audit")


def _line(event: dict[str, Any]) -> str:
    """The event as one printable-ASCII JSON line.

    ``json.dumps`` escapes controls below U+0020 and everything non-ASCII, but not DEL.
    Escaping it too means the line, written to the journal or to a terminal as it is,
    carries no character a terminal acts on.
    """
    return json.dumps(event, sort_keys=True).replace("\x7f", "\\u007f")


class AuditLogger:
    """Emits structured audit events for mutating operations."""

    def __init__(self, actor: str = "cli") -> None:
        self._actor = actor

    def record(self, action: str, **details: Any) -> dict[str, Any]:
        """Record one audit event and return it.

        The event is emitted as a single JSON line on the ``gora.audit``
        logger, where the journal sink (``gora.audit.sink``) picks it up once a
        mutating command attaches it; no command does until ``gora register``.
        The event dict is also returned so callers (and tests) can inspect
        exactly what was recorded.
        """
        event: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "actor": self._actor,
            "action": action,
            "details": details,
        }
        logger.info(_line(event))
        return event
