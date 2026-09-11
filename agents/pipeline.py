"""
agents/pipeline.py

Lyzr Automata-style orchestration of the four-agent pipeline:

    Triage -> Diagnose -> Remediate -> (HITL gate if needed) -> Post-Mortem

A single typed IncidentContext object (never a raw string) is threaded
through every stage. Each stage's input is the previous stage's validated
Pydantic output. Token usage and latency are measured and reported for
every single agent call via the injected `hooks.log_aims` callback.

This module has ZERO dependency on the backend package (no circular
imports): all persistence, SSE broadcasting, and HITL-resolution waiting is
delegated to a `PipelineHooks` object supplied by the caller (backend/main.py
and backend/store.py). This keeps `agents/` a pure, independently testable
orchestration layer -- the "Agent" tier -- decoupled from the "Environment"
(FastAPI/backend) tier, per the Lyzr separation-of-concerns requirement.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Protocol

from agents import config
from agents.diagnostician_agent import AGENT_NAME as DIAGNOSTICIAN_NAME
from agents.diagnostician_agent import run_diagnosis
from agents.postmortem_agent import AGENT_NAME as POSTMORTEM_NAME
from agents.postmortem_agent import run_postmortem
from agents.remediation_agent import AGENT_NAME as REMEDIATION_NAME
from agents.remediation_agent import run_remediation
from agents.schemas import (
    AgentTraceStep,
    AIMSEvent,
    Alert,
    DiagnosisHypothesis,
    HITLRequest,
    HITLStatus,
    IncidentStatus,
    RCAReport,
    RunbookProposal,
    TimelineEvent,
    TriageResult,
)
from agents.triage_agent import AGENT_NAME as TRIAGE_NAME
from agents.triage_agent import run_triage


class PipelineHooks(Protocol):
    """Everything the pipeline needs from its host environment. Implemented
    by backend/store.py and wired up in backend/main.py."""

    def on_status(self, incident_id: str, status: IncidentStatus) -> None: ...

    def on_trace(self, step: AgentTraceStep) -> None: ...

    def log_aims(self, event: AIMSEvent) -> None: ...

    def set_result(self, incident_id: str, key: str, value) -> None:
        """Persist a stage's validated output (`key` in triage/diagnosis/
        runbook/rca) onto the incident's stored state."""
        ...

    def add_hallucination_report(self, incident_id: str, report: dict) -> None:
        """Append an agents/hallucination_guard.py validation report to the
        incident's audit trail, so groundedness is visible to judges via
        GET /incidents/{id}."""
        ...

    async def await_hitl(
        self, incident_id: str, requests: List[HITLRequest]
    ) -> List[HITLRequest]:
        """Wait until every request is resolved (approved/rejected by a
        human via the API) or times out after config.HITL_TIMEOUT_SECONDS.
        Returns the same requests with `.status`, `.decided_by`, and
        `.decided_at` populated."""
        ...


@dataclass
class IncidentContext:
    """The single state object threaded through every pipeline stage."""

    incident_id: str
    alerts: List[Alert]
    log_corpus: List[str]
    triage: Optional[TriageResult] = None
    diagnosis: Optional[DiagnosisHypothesis] = None
    runbook: Optional[RunbookProposal] = None
    rca: Optional[RCAReport] = None
    timeline: List[TimelineEvent] = field(default_factory=list)


def _trace(
    hooks: PipelineHooks,
    incident_id: str,
    agent_name: str,
    summary: str,
    detail: dict,
    tokens_used: int,
    latency_ms: float,
    blocked: bool = False,
) -> None:
    step = AgentTraceStep(
        incident_id=incident_id,
        agent_name=agent_name,
        summary=summary,
        detail=detail,
        tokens_used=tokens_used,
        latency_ms=latency_ms,
        blocked=blocked,
    )
    hooks.on_trace(step)
    hooks.log_aims(
        AIMSEvent(
            incident_id=incident_id,
            agent_name=agent_name,
            action=summary,
            input_summary=str(detail)[:400],
            output_summary=summary,
            tokens_used=tokens_used,
            latency_ms=latency_ms,
            blocked=blocked,
        )
    )


async def run_pipeline(
    incident_id: str,
    alerts: List[Alert],
    log_corpus: List[str],
    hooks: PipelineHooks,
) -> Optional[RCAReport]:
    ctx = IncidentContext(incident_id=incident_id, alerts=alerts, log_corpus=log_corpus)

    try:
        # ------------------------------------------------------------ #
        # Stage 1: Triage & Dedup
        # ------------------------------------------------------------ #
        hooks.on_status(incident_id, IncidentStatus.TRIAGING)
        triage, tokens, latency = run_triage(alerts, incident_id)
        ctx.triage = triage
        hooks.set_result(incident_id, "triage", triage)
        ctx.timeline.append(
            TimelineEvent(timestamp=time.time(), event=f"Triaged as {triage.severity.value}: {triage.summary}", actor=TRIAGE_NAME)
        )
        _trace(
            hooks, incident_id, TRIAGE_NAME,
            f"Severity {triage.severity.value} assigned ({triage.alert_count} alert(s) clustered)"
            + (" [DUPLICATE within dedup window]" if triage.is_duplicate else ""),
            triage.model_dump(), tokens, latency,
        )

        # ------------------------------------------------------------ #
        # Stage 2: Root Cause Diagnosis
        # ------------------------------------------------------------ #
        hooks.on_status(incident_id, IncidentStatus.DIAGNOSING)
        diagnosis, tokens, latency, validation_report = run_diagnosis(triage, log_corpus, alerts)
        ctx.diagnosis = diagnosis
        hooks.set_result(incident_id, "diagnosis", diagnosis)
        hooks.add_hallucination_report(incident_id, validation_report)
        ctx.timeline.append(
            TimelineEvent(timestamp=time.time(), event=f"Root cause hypothesis (confidence {diagnosis.confidence:.2f}): {diagnosis.cause}", actor=DIAGNOSTICIAN_NAME)
        )
        _trace(
            hooks, incident_id, DIAGNOSTICIAN_NAME,
            f"Confidence {diagnosis.confidence:.2f} -- {diagnosis.cause}",
            diagnosis.model_dump(), tokens, latency,
            blocked=diagnosis.requires_human_review,
        )
        if diagnosis.requires_human_review:
            ctx.timeline.append(
                TimelineEvent(
                    timestamp=time.time(),
                    event=f"Confidence {diagnosis.confidence:.2f} below threshold {config.CONFIDENCE_THRESHOLD}: escalated for human review",
                    actor=DIAGNOSTICIAN_NAME,
                )
            )

        # ------------------------------------------------------------ #
        # Stage 3: Remediation Planning
        # ------------------------------------------------------------ #
        hooks.on_status(incident_id, IncidentStatus.REMEDIATING)
        runbook, tokens, latency = run_remediation(diagnosis)
        ctx.runbook = runbook
        hooks.set_result(incident_id, "runbook", runbook)
        _trace(
            hooks, incident_id, REMEDIATION_NAME,
            f"{len(runbook.actions)} step(s) proposed -- {len(runbook.auto_executed_steps)} safe (auto), {len(runbook.blocked_steps)} destructive (HITL required)",
            runbook.model_dump(), tokens, latency,
            blocked=bool(runbook.blocked_steps),
        )

        # Auto-execute SAFE actions (mocked -- no real infra is touched)
        for action in runbook.actions:
            if not action.is_destructive:
                action.executed = True
                ctx.timeline.append(
                    TimelineEvent(timestamp=time.time(), event=f"Auto-executed (SAFE): {action.description}", actor=REMEDIATION_NAME)
                )

        # Route DESTRUCTIVE actions through the HITL gate
        destructive_actions = [a for a in runbook.actions if a.is_destructive]
        if destructive_actions:
            hooks.on_status(incident_id, IncidentStatus.AWAITING_HITL)
            now = time.time()
            requests = [
                HITLRequest(incident_id=incident_id, action=a, expires_at=now + config.HITL_TIMEOUT_SECONDS)
                for a in destructive_actions
            ]
            for req in requests:
                ctx.timeline.append(
                    TimelineEvent(timestamp=time.time(), event=f"BLOCKED pending human approval: {req.action.description}", actor="Lyzr Safe AI")
                )
                hooks.log_aims(
                    AIMSEvent(
                        incident_id=incident_id, agent_name="LyzrSafeAI",
                        action="destructive_action_blocked",
                        input_summary=req.action.command, output_summary="HITL approval required",
                        blocked=True,
                    )
                )

            resolved_requests = await hooks.await_hitl(incident_id, requests)

            for req in resolved_requests:
                action = req.action
                if req.status == HITLStatus.APPROVED:
                    action.executed = True
                    ctx.timeline.append(
                        TimelineEvent(timestamp=time.time(), event=f"HUMAN APPROVED and executed: {action.description}", actor=req.decided_by or "human")
                    )
                elif req.status == HITLStatus.REJECTED:
                    ctx.timeline.append(
                        TimelineEvent(timestamp=time.time(), event=f"HUMAN REJECTED (not executed): {action.description}", actor=req.decided_by or "human")
                    )
                else:  # TIMED_OUT
                    ctx.timeline.append(
                        TimelineEvent(timestamp=time.time(), event=f"HITL request TIMED OUT (not executed): {action.description}", actor="system")
                    )

        # ------------------------------------------------------------ #
        # Stage 4: Post-Mortem / RCA
        # ------------------------------------------------------------ #
        hooks.on_status(incident_id, IncidentStatus.POST_MORTEM)
        rca, tokens, latency = run_postmortem(incident_id, alerts, triage, diagnosis, runbook, ctx.timeline)
        ctx.rca = rca
        hooks.set_result(incident_id, "rca", rca)
        _trace(
            hooks, incident_id, POSTMORTEM_NAME,
            f"RCA generated: {rca.title}",
            rca.model_dump(), tokens, latency,
        )

        hooks.on_status(incident_id, IncidentStatus.RESOLVED)
        return rca

    except Exception as exc:  # pragma: no cover - top-level safety net
        hooks.log_aims(
            AIMSEvent(
                incident_id=incident_id, agent_name="pipeline",
                action="pipeline_error", output_summary=str(exc), blocked=True,
            )
        )
        hooks.on_trace(
            AgentTraceStep(
                incident_id=incident_id, agent_name="pipeline",
                summary=f"Pipeline error, escalated to human: {exc}", blocked=True,
            )
        )
        hooks.on_status(incident_id, IncidentStatus.ERROR)
        return None
