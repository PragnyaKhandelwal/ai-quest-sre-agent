"""
agents/remediation_agent.py

Agent 3: Remediation Planner Agent.

Turns a DiagnosisHypothesis into an ordered RunbookProposal of typed
RemediationAction objects -- NEVER raw shell-command strings floating
around the pipeline. Every action is independently classified SAFE vs
DESTRUCTIVE by keyword inspection (config.DESTRUCTIVE_KEYWORDS).

Safety contract:
  * SAFE actions may be marked auto-executed (mocked -- this demo never
    touches a real cluster/database).
  * DESTRUCTIVE actions are NEVER auto-executed. They are always flagged
    `hitl_required=True` and routed to the HITL approval queue by the
    pipeline. This is the Lyzr Safe AI gate.
  * This classification is re-verified independently in
    backend/aims_logger.py before any "executed" event is ever logged --
    defense in depth, two separate checks against the same source list.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Tuple

from agents import config
from agents.lyzr_client import client
from agents.schemas import DiagnosisHypothesis, RemediationAction, RiskLevel, RunbookProposal

AGENT_NAME = "RemediationPlannerAgent"


def is_destructive_command(command: str) -> bool:
    """Independent, centrally-sourced destructive-action check. Called here
    at proposal time; re-checked again in backend/aims_logger.py before any
    execution/log event -- see module docstring."""
    upper = command.upper()
    return any(keyword in upper for keyword in config.DESTRUCTIVE_KEYWORDS)


@dataclass
class RunbookTemplate:
    match_keywords: List[str]
    steps: List[dict]  # description, command, risk_level, reason


_RUNBOOK_LIBRARY: List[RunbookTemplate] = [
    RunbookTemplate(
        match_keywords=["memory leak", "oomkilled", "heap growth", "java heap"],
        steps=[
            {
                "description": "Rolling restart of affected pods to clear leaked connections",
                "command": "kubectl rollout restart deployment/payment-service -n production",
                "risk_level": RiskLevel.LOW,
                "reason": "Non-destructive; Kubernetes performs a graceful rolling restart with zero downtime.",
            },
            {
                "description": "Scale up replicas temporarily to absorb load during restart",
                "command": "kubectl scale deployment/payment-service --replicas=6 -n production",
                "risk_level": RiskLevel.LOW,
                "reason": "Non-destructive capacity increase; safe to auto-execute.",
            },
        ],
    ),
    RunbookTemplate(
        match_keywords=["bad container image", "missing required environment", "v2.3.1", "rollout"],
        steps=[
            {
                "description": "Roll back api-gateway to the last known-good image",
                "command": "kubectl rollout undo deployment/api-gateway -n production --to-revision=v2.3.0",
                "risk_level": RiskLevel.LOW,
                "reason": "Non-destructive rollback restores previous stable revision; safe to auto-execute.",
            },
        ],
    ),
    RunbookTemplate(
        match_keywords=["deadlock", "orders table", "long-running transaction"],
        steps=[
            {
                "description": "Identify and terminate the long-running blocking session on postgres-primary",
                "command": "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE query = 'UPDATE orders ...' AND state = 'active';",
                "risk_level": RiskLevel.CRITICAL,
                "reason": "DESTRUCTIVE: forcibly kills an active database session and rolls back its transaction. Requires human approval.",
            },
            {
                "description": "Set statement_timeout to prevent recurrence",
                "command": "ALTER DATABASE orders_db SET statement_timeout = '30s';",
                "risk_level": RiskLevel.LOW,
                "reason": "Non-destructive configuration change; safe to auto-execute.",
            },
        ],
    ),
    RunbookTemplate(
        match_keywords=["disk usage", "logrotate", "eviction", "audit"],
        steps=[
            {
                "description": "Cordon worker-3 to prevent further pod scheduling",
                "command": "kubectl cordon worker-3",
                "risk_level": RiskLevel.HIGH,
                "reason": "DESTRUCTIVE-adjacent: removes node from scheduling pool, impacts cluster capacity. Requires human approval.",
            },
            {
                "description": "Drain worker-3 to safely evict remaining workloads before cleanup",
                "command": "kubectl drain worker-3 --ignore-daemonsets --delete-emptydir-data",
                "risk_level": RiskLevel.CRITICAL,
                "reason": "DESTRUCTIVE: forcibly evicts all pods from the node. Requires human approval.",
            },
            {
                "description": "Apply logrotate configuration to prevent recurrence",
                "command": "kubectl apply -f configs/logrotate-daemonset.yaml",
                "risk_level": RiskLevel.LOW,
                "reason": "Non-destructive configuration rollout; safe to auto-execute.",
            },
        ],
    ),
]

_DEFAULT_TEMPLATE = RunbookTemplate(
    match_keywords=[],
    steps=[
        {
            "description": "Escalate to on-call human SRE for manual investigation",
            "command": "N/A - manual investigation required",
            "risk_level": RiskLevel.MEDIUM,
            "reason": "No automated runbook matched this diagnosis with sufficient confidence.",
        }
    ],
)


def _select_template(cause_text: str) -> RunbookTemplate:
    """Pick the template with the MOST matching keywords (not just the first
    template with any match) so that a keyword shared across two templates
    can never accidentally shadow the more specific, better-matching one."""
    low = cause_text.lower()
    best_tmpl, best_score = None, 0
    for tmpl in _RUNBOOK_LIBRARY:
        score = sum(1 for kw in tmpl.match_keywords if kw in low)
        if score > best_score:
            best_tmpl, best_score = tmpl, score
    return best_tmpl or _DEFAULT_TEMPLATE


def _build_actions(template: RunbookTemplate) -> List[RemediationAction]:
    actions = []
    for i, step in enumerate(template.steps, start=1):
        destructive = is_destructive_command(step["command"])
        actions.append(
            RemediationAction(
                step=i,
                description=step["description"],
                command=step["command"],
                is_destructive=destructive,
                risk_level=step["risk_level"],
                reason=step["reason"],
                executed=False,
                hitl_required=destructive,
            )
        )
    return actions


def _simulate(diagnosis: DiagnosisHypothesis) -> Dict:
    template = _select_template(diagnosis.cause)
    actions = _build_actions(template)
    auto_executed = [a.step for a in actions if not a.is_destructive]
    blocked = [a.step for a in actions if a.is_destructive]
    return {
        "incident_id": diagnosis.incident_id,
        "actions": [a.model_dump() for a in actions],
        "auto_executed_steps": auto_executed,
        "blocked_steps": blocked,
    }


def run_remediation(diagnosis: DiagnosisHypothesis) -> Tuple[RunbookProposal, int, float]:
    try:
        user_message = json.dumps(
            {
                "incident_id": diagnosis.incident_id,
                "cause": diagnosis.cause,
                "affected_components": diagnosis.affected_components,
                "confidence": diagnosis.confidence,
            }
        )

        result = client.run_inference(
            agent_key="remediation",
            agent_name=AGENT_NAME,
            system_prompt=config.REMEDIATION_SYSTEM_PROMPT,
            user_message=user_message,
            session_id=diagnosis.incident_id,
            simulate_fn=lambda: _simulate(diagnosis),
        )

        try:
            payload = json.loads(result.text)
            proposal = RunbookProposal(**payload)
        except Exception:
            proposal = RunbookProposal(**_simulate(diagnosis))

        # Independent re-verification pass (defense in depth #1): re-derive
        # is_destructive / hitl_required for every action regardless of what
        # the model claimed, from the same centrally-sourced keyword list.
        auto_executed, blocked = [], []
        for action in proposal.actions:
            action.is_destructive = is_destructive_command(action.command)
            action.hitl_required = action.is_destructive
            if action.is_destructive:
                blocked.append(action.step)
            else:
                auto_executed.append(action.step)
        proposal.auto_executed_steps = auto_executed
        proposal.blocked_steps = blocked

        return proposal, result.tokens_used, result.latency_ms

    except Exception as exc:  # pragma: no cover
        fallback = RunbookProposal(
            incident_id=diagnosis.incident_id,
            actions=[
                RemediationAction(
                    step=1,
                    description="Escalate to human on-call (remediation agent failure)",
                    command="N/A",
                    is_destructive=False,
                    risk_level=RiskLevel.MEDIUM,
                    reason=f"Remediation agent failed: {exc}",
                    executed=False,
                    hitl_required=False,
                )
            ],
            auto_executed_steps=[],
            blocked_steps=[],
        )
        return fallback, 0, 0.0
