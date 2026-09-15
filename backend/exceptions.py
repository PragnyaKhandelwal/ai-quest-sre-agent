"""
Custom Exception Hierarchy for SRE Agent API
=============================================
Replaces generic HTTPException(...) calls with specific, informative
exception types carrying a stable machine-readable `error_code` and a
structured `detail` payload for API consumers.

Dr Agent recommendation: "Refactor single catch-all exception handler
into more specific handlers to improve API robustness and provide more
informative client-facing error responses."
"""
from typing import Optional


class SREAgentException(Exception):
    """Base exception for all SRE Agent errors."""
    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, detail: Optional[dict] = None):
        self.message = message
        self.detail = detail or {}
        super().__init__(message)


# -- Incident exceptions -----------------------------------------------------
class IncidentNotFoundError(SREAgentException):
    status_code = 404
    error_code = "INCIDENT_NOT_FOUND"

    def __init__(self, incident_id: str):
        super().__init__(
            f"Incident '{incident_id}' not found",
            {"incident_id": incident_id, "hint": "Check GET /incidents for valid IDs"},
        )


class IncidentAlreadyResolvedError(SREAgentException):
    status_code = 409
    error_code = "INCIDENT_ALREADY_RESOLVED"

    def __init__(self, incident_id: str, current_status: str):
        super().__init__(
            f"Incident '{incident_id}' is already {current_status}",
            {"incident_id": incident_id, "current_status": current_status},
        )


# -- HITL exceptions ----------------------------------------------------------
class HITLActionNotFoundError(SREAgentException):
    status_code = 404
    error_code = "HITL_ACTION_NOT_FOUND"

    def __init__(self, incident_id: str):
        super().__init__(
            f"No pending HITL action found for incident '{incident_id}'",
            {"incident_id": incident_id, "hint": "Action may have already been decided, or the incident_id is wrong"},
        )


class HITLAlreadyDecidedError(SREAgentException):
    status_code = 409
    error_code = "HITL_ALREADY_DECIDED"

    def __init__(self, incident_id: str, decision: str):
        super().__init__(
            f"HITL action for incident '{incident_id}' already {decision}",
            {"incident_id": incident_id, "decision": decision},
        )


class HITLTimeoutError(SREAgentException):
    status_code = 408
    error_code = "HITL_TIMEOUT"

    def __init__(self, incident_id: str, timeout_seconds: int):
        super().__init__(
            f"HITL approval timed out after {timeout_seconds}s for incident '{incident_id}'",
            {"incident_id": incident_id, "timeout_seconds": timeout_seconds},
        )


# -- Pipeline exceptions -------------------------------------------------------
class AgentPipelineError(SREAgentException):
    status_code = 503
    error_code = "AGENT_PIPELINE_ERROR"

    def __init__(self, agent_name: str, reason: str):
        super().__init__(
            f"Agent pipeline failed at '{agent_name}': {reason}",
            {
                "agent_name": agent_name,
                "reason": reason,
                "hint": "Pipeline will retry automatically. Use GET /incidents to check status.",
            },
        )


class AgentHallucinationError(SREAgentException):
    status_code = 422
    error_code = "AGENT_HALLUCINATION_DETECTED"

    def __init__(self, agent_name: str, signals: list):
        super().__init__(
            f"Hallucination detected in '{agent_name}' output",
            {
                "agent_name": agent_name,
                "hallucination_signals": signals,
                "action": "Output rejected -- incident escalated to human review",
            },
        )


# -- Scenario exceptions -------------------------------------------------------
class InvalidScenarioError(SREAgentException):
    status_code = 422
    error_code = "INVALID_SCENARIO"

    def __init__(self, scenario: int):
        super().__init__(
            f"Scenario {scenario} is not valid. Must be 1-6.",
            {
                "provided": scenario,
                "valid_range": "1-6",
                "scenarios": {
                    1: "P1 Memory Leak",
                    2: "P2 Bad Deploy",
                    3: "P1 DB Deadlock",
                    4: "P2 Node Disk Full",
                    5: "P2 CPU Throttling",
                    6: "P3 Certificate Expiry",
                },
            },
        )


# -- Tool exceptions ------------------------------------------------------------
class ToolNotFoundError(SREAgentException):
    status_code = 404
    error_code = "TOOL_NOT_FOUND"

    def __init__(self, tool_name: str, available: list):
        super().__init__(
            f"Tool '{tool_name}' not found in registry",
            {"tool_name": tool_name, "available_tools": available},
        )


class DestructiveActionBlockedError(SREAgentException):
    status_code = 403
    error_code = "DESTRUCTIVE_ACTION_BLOCKED"

    def __init__(self, command: str, incident_id: str):
        super().__init__(
            "Destructive action blocked by Lyzr Safe AI guardrail",
            {
                "command_preview": command[:50] + ("..." if len(command) > 50 else ""),
                "incident_id": incident_id,
                "action": "Routed to HITL approval queue",
                "policy": "All DELETE/DROP/REBOOT/DRAIN-style actions require human approval",
            },
        )


# -- Secret exceptions -----------------------------------------------------------
class SecretNotFoundError(SREAgentException):
    status_code = 503
    error_code = "SECRET_NOT_FOUND"

    def __init__(self, key: str, provider: str):
        super().__init__(
            f"Required secret '{key}' not found in {provider}",
            {"key": key, "provider": provider, "hint": f"Set {key} in your environment or secret manager"},
        )
