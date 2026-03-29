"""MCP server authentication and authorization.

Validates API keys for MCP tool calls, enforces role-based tool access,
and provides audit logging.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool access by role
# ---------------------------------------------------------------------------

# Tools accessible by each role
ROLE_TOOLS: dict[str, set[str]] = {
    "admin": set(),  # empty = all tools allowed
    "analyst": {
        "periphery_health",
        "periphery_query",
        "periphery_search",
        "periphery_snapshot",
        "periphery_clusters",
        "periphery_cluster_detail",
        "periphery_entities",
        "periphery_entity_detail",
        "periphery_relationships",
        "periphery_emerging",
        "periphery_anomalies",
        "periphery_trajectories",
        "periphery_critic_scores",
        "periphery_legibility_gradient",
        "periphery_query_history",
        "periphery_ingest",
        "periphery_ingest_stats",
        "periphery_pipeline_status",
    },
    "ingest": {
        "periphery_health",
        "periphery_ingest",
        "periphery_ingest_stats",
    },
}

# PII-sensitive parameter names to redact in audit logs
PII_PARAM_PATTERNS = re.compile(
    r"(ssn|social_security|email|phone|address|dob|date_of_birth|password|secret|token)",
    re.IGNORECASE,
)


def tool_allowed(role: str, tool_name: str) -> bool:
    """Check if a role is allowed to use a specific tool."""
    allowed = ROLE_TOOLS.get(role, set())
    if not allowed:  # empty set = admin = all tools
        return True
    return tool_name in allowed


def redact_params(params: dict[str, Any]) -> dict[str, Any]:
    """Redact PII-sensitive parameter values for audit logging."""
    redacted = {}
    for key, value in params.items():
        if PII_PARAM_PATTERNS.search(key):
            redacted[key] = "[REDACTED]"
        elif isinstance(value, str) and len(value) > 500:
            redacted[key] = value[:100] + "...[truncated]"
        else:
            redacted[key] = value
    return redacted


class MCPAuditLogger:
    """Structured audit logger for MCP tool calls."""

    def __init__(self) -> None:
        self._logger = logging.getLogger("periphery_mcp.audit")

    def log_tool_call(
        self,
        key_id: str | None,
        role: str,
        tool_name: str,
        params: dict[str, Any],
        success: bool,
        error: str | None = None,
        data_classification: str | None = None,
    ) -> None:
        """Log a tool call for audit purposes."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "key_id": key_id or "unknown",
            "role": role,
            "tool": tool_name,
            "params": redact_params(params),
            "success": success,
            "data_classification": data_classification or "PUBLIC",
        }
        if error:
            entry["error"] = error

        self._logger.info("mcp_tool_call %s", json.dumps(entry))

    def log_auth_failure(
        self,
        reason: str,
        key_prefix: str | None = None,
    ) -> None:
        """Log a failed authentication attempt."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "auth_failure",
            "reason": reason,
            "key_prefix": key_prefix,
        }
        self._logger.warning("mcp_auth_failure %s", json.dumps(entry))


# Global audit logger
audit_logger = MCPAuditLogger()


class MCPAuthContext:
    """Authentication context for the current MCP session."""

    def __init__(
        self,
        key_id: str | None = None,
        role: str = "analyst",
        classification_scope: list[str] | None = None,
        label: str = "anonymous",
    ) -> None:
        self.key_id = key_id
        self.role = role
        self.classification_scope = classification_scope or ["PUBLIC"]
        self.label = label

    def can_use_tool(self, tool_name: str) -> bool:
        """Check if this auth context allows using the given tool."""
        return tool_allowed(self.role, tool_name)
