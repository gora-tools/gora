"""Audit logging for mutating GORA operations.

Every mutating release operation (cut-rc, promote, rollback) MUST be
auditable (ADR-002, ADR-004). This logger produces one structured JSON
line per event via the standard logging system. It is injectable so
transports and tests can capture, redirect, or silence audit output.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("gora.audit")


class AuditLogger:
    """Emits structured audit events for mutating operations."""

    def __init__(self, actor: str = "cli") -> None:
        self._actor = actor

    def record(self, action: str, **details: Any) -> dict[str, Any]:
        """Record one audit event and return it.

        The event is emitted as a single JSON line on the ``gora.audit``
        logger. The event dict is also returned so callers (and tests) can
        inspect exactly what was recorded.
        """
        event: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "actor": self._actor,
            "action": action,
            "details": details,
        }
        logger.info(json.dumps(event, sort_keys=True))
        return event
