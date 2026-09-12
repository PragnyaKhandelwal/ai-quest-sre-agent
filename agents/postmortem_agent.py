"""
agents/postmortem_agent.py

Agent 4: Post-Mortem / RCA Agent.

Consumes the full incident record (alerts + triage + diagnosis +
remediation + HITL decisions + timeline) and produces a blameless RCA
report. The report is grounded entirely in the structured objects produced
by the earlier pipeline stages -- no new facts are introduced.

LAYER 2 -> LAYER 3: Agent -> Inference (see lyzr_agents.py + lyzr_inference.py)
"""
from __future__ import annotations

import json
from typing import List, Tuple

from agents.lyzr_agents import POSTMORTEM_AGENT
from agents.lyzr_inference import run_inference
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
    elif "cpu limit" in cause_low or "throttl" in cause_low:
        prevention = [
            "Add resource-limit validation to the CI pipeline for every model/image upgrade.",
            "Add CPU-throttle-ratio alerting at 70% threshold, well before p99 latency degrades.",
        ]
    elif "cert-manager" in cause_low or "acme" in cause_low or "certificate" in cause_low:
        prevention = [
            "Add cert-manager Prometheus alerting at a 30-day expiry threshold, not just at the 4-hour mark.",
            "Add a secondary DNS provider fallback for ACME DNS-01 challenges to avoid single-provider timeouts.",
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

        rca, meta = run_inference(
            POSTMORTEM_AGENT,
            user_message,
            incident_id,
            fallback_fn=lambda: json.dumps(_simulate(incident_id, alerts, triage, diagnosis, runbook, timeline)),
            output_schema=RCAReport,
        )

        return rca, meta["total_tokens"], meta["latency_ms"]

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
