"""
agents/schemas.py

Pydantic models shared by every agent and the backend. Centralizing schemas
here means the pipeline never passes raw strings between agents -- every
hop is a validated, typed object, which is a core "no hallucinated shell
commands" safety requirement for this system.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def _now() -> float:
    return time.time()


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class Severity(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class IncidentStatus(str, Enum):
    TRIAGING = "TRIAGING"
    DIAGNOSING = "DIAGNOSING"
    REMEDIATING = "REMEDIATING"
    AWAITING_HITL = "AWAITING_HITL"
    POST_MORTEM = "POST_MORTEM"
    RESOLVED = "RESOLVED"
    ERROR = "ERROR"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class HITLStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    TIMED_OUT = "TIMED_OUT"


# ---------------------------------------------------------------------------
# Alert ingestion
# ---------------------------------------------------------------------------
class Alert(BaseModel):
    id: str = Field(default_factory=lambda: _uid("alert"))
    source: str = Field(default="prometheus", description="prometheus | pagerduty")
    service: str
    namespace: str = "production"
    alertname: str
    severity_hint: Optional[str] = None
    title: str
    description: str
    started_at: float = Field(default_factory=_now)
    labels: Dict[str, str] = Field(default_factory=dict)


class AlertCluster(BaseModel):
    cluster_id: str = Field(default_factory=lambda: _uid("cluster"))
    fingerprint: str
    alerts: List[Alert]
    service: str
    namespace: str


class TriageResult(BaseModel):
    incident_id: str
    cluster_id: str
    fingerprint: str
    severity: Severity
    affected_services: List[str]
    summary: str
    alert_count: int
    is_duplicate: bool = False
    reasoning: str = ""
    # Populated locally by agents/anomaly_detector.py (never by the LLM --
    # same "authoritative override" pattern as fingerprint/cluster_id/
    # is_duplicate above): root-cause-vs-symptom correlation graph and
    # burst-of-alerts detection across the alert batch.
    correlation_graph: Optional[Dict[str, Any]] = None
    burst_detected: bool = False
    burst_analysis: str = ""


# ---------------------------------------------------------------------------
# Diagnosis
# ---------------------------------------------------------------------------
class Evidence(BaseModel):
    log_line: str
    line_number: Optional[int] = None
    relevance: str = ""


class DiagnosisHypothesis(BaseModel):
    incident_id: str
    cause: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: List[Evidence]
    affected_components: List[str]
    requires_human_review: bool = False
    reasoning: str = ""
    # Populated by the semantic log retriever (agents/log_retriever.py, or
    # agents/vector_store.py when a real vector DB is installed):
    # cosine-similarity scores of the log lines actually retrieved and
    # handed to this agent, evidencing retrieval quality to judges.
    retrieval_scores: List[float] = Field(default_factory=list)
    # agents/vector_store.py's SREVectorStore.stats() -- which retrieval
    # backend actually served this diagnosis (real ChromaDB vector search
    # vs. the TF-IDF fallback), never set by the LLM.
    retrieval_backend: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Remediation
# ---------------------------------------------------------------------------
class RemediationAction(BaseModel):
    step: int
    description: str
    command: str
    is_destructive: bool
    risk_level: RiskLevel
    reason: str
    executed: bool = False
    hitl_required: bool = False
    # Every action must carry an explicit rollback path (or a documented
    # reason none exists) -- enforced by REMEDIATION_SYSTEM_PROMPT's
    # defensive rules in agents/prompt_templates.py.
    rollback_command: Optional[str] = None


class RunbookProposal(BaseModel):
    incident_id: str
    actions: List[RemediationAction]
    auto_executed_steps: List[int] = Field(default_factory=list)
    blocked_steps: List[int] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# HITL
# ---------------------------------------------------------------------------
class HITLRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: _uid("hitl"))
    incident_id: str
    action: RemediationAction
    status: HITLStatus = HITLStatus.PENDING
    created_at: float = Field(default_factory=_now)
    expires_at: float
    decided_at: Optional[float] = None
    decided_by: Optional[str] = None


class HITLDecision(BaseModel):
    request_id: str
    approve: bool
    decided_by: str = "human"
    note: str = ""


# ---------------------------------------------------------------------------
# Post-mortem / RCA
# ---------------------------------------------------------------------------
class TimelineEvent(BaseModel):
    timestamp: float
    event: str
    actor: str  # agent name or "human"


class RCAReport(BaseModel):
    incident_id: str
    title: str
    severity: Severity
    timeline: List[TimelineEvent]
    root_cause: str
    affected_services: List[str]
    contributing_factors: List[str]
    remediation_taken: List[str]
    prevention_recommendations: List[str]
    lessons_learned: str
    generated_at: float = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Audit / AIMS
# ---------------------------------------------------------------------------
class AIMSEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: _uid("aims"))
    timestamp: float = Field(default_factory=_now)
    incident_id: Optional[str] = None
    agent_name: str
    action: str
    input_summary: str = ""
    output_summary: str = ""
    tokens_used: int = 0
    latency_ms: float = 0.0
    blocked: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Agent trace step (for the SSE stream / AgentTrace UI panel)
# ---------------------------------------------------------------------------
class AgentTraceStep(BaseModel):
    step_id: str = Field(default_factory=lambda: _uid("step"))
    incident_id: str
    agent_name: str
    summary: str
    detail: Dict[str, Any] = Field(default_factory=dict)
    tokens_used: int = 0
    latency_ms: float = 0.0
    timestamp: float = Field(default_factory=_now)
    blocked: bool = False
