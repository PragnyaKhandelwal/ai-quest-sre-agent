"""
agents/postmortem_agent.py

Agent 4: Post-Mortem / RCA Agent.

Consumes the full incident record (alerts + triage + diagnosis +
remediation + HITL decisions + timeline) and produces a blameless RCA
report. The report is grounded entirely in the structured objects produced
by the earlier pipeline stages -- no new facts are introduced.
"""
from __future__ import annotations

import json
from typing import List, Tuple

from agents import config
from agents.lyzr_client import client
from agents.schemas import (
    Alert,
    DiagnosisHypothesis,
    RCAReport,
    RunbookProposal,
    TimelineEvent,
    TriageResult,
)

AGENT_NAME = "PostMortemRCAAgent"


def _simulate(
    incident_id: str,
    alerts: List[Alert],
    triage: TriageResult,
    diagnosis: DiagnosisHypothesis,
    runbook: RunbookProposal,
    timeline: List[TimelineEvent],
) -> dict:
    remediation_taken = [
        f"Step {a.step}: {a.description} ({'executed automatically' if a.executed else 'pending/blocked'})"
        for a in runbook.actions
    ]
    contributing_factors = [
        f"Low diagnostic confidence ({diagnosis.confidence:.2f}); escalated for human review."
        if diagnosis.requires_human_review
        else f"Diagnosis confidence {diagnosis.confidence:.2f} exceeded auto-remediation threshold.",
    ]
    if any(a.is_destructive for a in runbook.actions):
        contributing_factors.append(
            "Remediation required at least one destructive action, gated by human-in-the-loop approval."
        )

    prevention = []
    cause_low = diagnosis.cause.lower()
    if "memory leak" in cause_low or "connection pool" in cause_low:
        prevention = [
            "Add explicit connection pool timeout/max-lifetime configuration.",
            "Add memory-growth alerting at 80% threshold with automatic heap dump capture.",
        ]
    elif "bad container image" in cause_low or "environment" in cause_low:
        prevention = [
            "Add a pre-deploy CI validation step that checks required environment variables.",
            "Add automated canary analysis before promoting a new image to 100% traffic.",
        ]
    elif "deadlock" in cause_low:
        prevention = [
            "Set a default statement_timeout on all database connections.",
            "Add query-duration alerting to catch long-running transactions before they cascade.",
        ]
    elif "disk usage" in cause_low or "logrotate" in cause_low:
        prevention = [
            "Add logrotate configuration to all logging-agent daemonsets.",
            "Add disk-usage alerting at 80% threshold, well before eviction thresholds.",
        ]
    else:
        prevention = ["Conduct a deeper investigation; automated pattern library found no exact match."]

    return {
        "incident_id": incident_id,
        "title": f"[{triage.severity.value}] {triage.summary}",
        "severity": triage.severity.value,
        "timeline": [t.model_dump() for t in timeline],
        "root_cause": diagnosis.cause,
        "affected_services": triage.affected_services,
        "contributing_factors": contributing_factors,
        "remediation_taken": remediation_taken,
        "prevention_recommendations": prevention,
        "lessons_learned": (
            "This incident was detected and triaged automatically. "
            + (
                "Destructive remediation steps were correctly withheld pending human "
                "approval, demonstrating the governance gate functioning as designed. "
                if any(a.is_destructive for a in runbook.actions)
                else "All remediation steps were safe to auto-execute. "
            )
            + "No individual is blamed; focus is on systemic prevention."
        ),
    }


def run_postmortem(
    incident_id: str,
    alerts: List[Alert],
    triage: TriageResult,
    diagnosis: DiagnosisHypothesis,
    runbook: RunbookProposal,
    timeline: List[TimelineEvent],
) -> Tuple[RCAReport, int, float]:
    try:
        user_message = json.dumps(
            {
                "incident_id": incident_id,
                "triage": triage.model_dump(),
                "diagnosis": diagnosis.model_dump(),
                "runbook": runbook.model_dump(),
                "timeline": [t.model_dump() for t in timeline],
            },
            default=str,
        )

        result = client.run_inference(
            agent_key="postmortem",
            agent_name=AGENT_NAME,
            system_prompt=config.POSTMORTEM_SYSTEM_PROMPT,
            user_message=user_message,
            session_id=incident_id,
            simulate_fn=lambda: _simulate(incident_id, alerts, triage, diagnosis, runbook, timeline),
        )

        try:
            payload = json.loads(result.text)
            rca = RCAReport(**payload)
        except Exception:
            rca = RCAReport(**_simulate(incident_id, alerts, triage, diagnosis, runbook, timeline))

        return rca, result.tokens_used, result.latency_ms

    except Exception as exc:  # pragma: no cover
        fallback = RCAReport(
            incident_id=incident_id,
            title=f"RCA generation failed for {incident_id}",
            severity=triage.severity,
            timeline=timeline,
            root_cause=f"Post-mortem agent failed: {exc}",
            affected_services=triage.affected_services,
            contributing_factors=[],
            remediation_taken=[],
            prevention_recommendations=[],
            lessons_learned="RCA generation encountered an internal error; manual write-up required.",
        )
        return fallback, 0, 0.0
