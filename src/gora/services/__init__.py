"""Application service layer.

Services own deterministic workflow sequencing for release operations
(ADR-002: deterministic code owns sequencing). They load Git-native state,
apply domain operations, persist results, and emit audit events. Transports
(CLI, API, Slack) call services; they do not embed release logic themselves.
"""
