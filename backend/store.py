"""
backend/store.py

Live incident state (the `INCIDENTS` dict below) stays in-process: it
holds asyncio.Event/Queue objects for HITL synchronization and SSE
fan-out that can't be serialized or shared across processes. Durability
across restarts is layered on top via backend/redis_store.py -- every
state change also writes a JSON-safe snapshot to Redis (or fakeredis in
dev), which is what makes a completed incident survive an in-process
restart. Also implements the `PipelineHooks` protocol expected by
agents/pipeline.py, bridging agent orchestration to persistence, SSE
broadcasting, and the HITL approval gate.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from agents import config
from agents.schemas import (
    AgentTraceStep,
    Alert,
    DiagnosisHypothesis,
    HITLRequest,
    HITLStatus,
    IncidentStatus,
    RCAReport,
    RunbookProposal,
    TriageResult,
)
from backend.aims_logger import log_event as aims_log_event
from backend.prometheus_metrics import (
    hitl_decisions_total,
    incidents_total,
    pipeline_duration,
    pipeline_errors_total,
)
from backend.redis_store import incident_store as redis_incident_store


@dataclass
class IncidentState:
    incident_id: str
    alerts: List[Alert]
    log_corpus: List[str] = field(default_factory=list)
    status: IncidentStatus = IncidentStatus.TRIAGING
    triage: Optional[TriageResult] = None
    diagnosis: Optional[DiagnosisHypothesis] = None
    runbook: Optional[RunbookProposal] = None
    rca: Optional[RCAReport] = None
    trace: List[AgentTraceStep] = field(default_factory=list)
    # agents/hallucination_guard.py validation reports (one per diagnosis),
    # surfaced via GET /incidents/{id} so groundedness is visible to judges.
    hallucination_reports: List[dict] = field(default_factory=list)
    # agents/automata_pipeline.py's run_automata_pipeline() result -- shows
    # whether this incident ran through the real lyzr-automata
    # LinearSyncPipeline or its simulation branch.
    pipeline_metadata: Optional[dict] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    scenario: Optional[str] = None

    # HITL bookkeeping: request_id -> (HITLRequest, asyncio.Event set on decision)
    hitl_pending: Dict[str, "tuple[HITLRequest, asyncio.Event]"] = field(default_factory=dict)
    hitl_history: List[HITLRequest] = field(default_factory=list)

    # Per-incident SSE subscriber queues
    _subscribers: List["asyncio.Queue[AgentTraceStep]"] = field(default_factory=list)

    def to_public_dict(self) -> dict:
        return {
            "incident_id": self.incident_id,
            "status": self.status.value,
            "scenario": self.scenario,
            "alerts": [a.model_dump() for a in self.alerts],
            "triage": self.triage.model_dump() if self.triage else None,
            "diagnosis": self.diagnosis.model_dump() if self.diagnosis else None,
            "runbook": self.runbook.model_dump() if self.runbook else None,
            "rca": self.rca.model_dump() if self.rca else None,
            "trace": [t.model_dump() for t in self.trace],
            "hallucination_reports": self.hallucination_reports,
            "pipeline_metadata": self.pipeline_metadata,
            "pending_hitl": [
                req.model_dump() for req, _ in self.hitl_pending.values() if req.status == HITLStatus.PENDING
            ],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def summary_dict(self) -> dict:
        """Lightweight summary for the alert-stream list view."""
        service = self.triage.affected_services[0] if self.triage else (
            self.alerts[0].service if self.alerts else "unknown"
        )
        severity = self.triage.severity.value if self.triage else "P4"
        return {
            "incident_id": self.incident_id,
            "status": self.status.value,
            "severity": severity,
            "service": service,
            "scenario": self.scenario,
            "title": self.alerts[0].title if self.alerts else "",
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "has_pending_hitl": any(
                req.status == HITLStatus.PENDING for req, _ in self.hitl_pending.values()
            ),
        }


# ---------------------------------------------------------------------------
# Global in-memory store
# ---------------------------------------------------------------------------
INCIDENTS: Dict[str, IncidentState] = {}
TOTAL_TOKENS_USED = {"count": 0}

# Populated once at startup by restore_persisted_incidents() (called from
# backend/main.py's startup event). Holds read-only historical records for
# incidents that existed in a previous process (backend/redis_store.py) --
# already in to_public_dict() shape, but NOT live IncidentState objects:
# an in-process restart loses the asyncio.Event/Queue state a live incident
# needs for HITL actions or further pipeline progress, so a restored
# incident is viewable but not resumable or actionable.
RESTORED_INCIDENTS: Dict[str, dict] = {}

# Global subscriber queues for the incident-list SSE feed (AlertStream panel)
_GLOBAL_SUBSCRIBERS: List["asyncio.Queue[dict]"] = []


def _persist(incident: IncidentState) -> None:
    """Best-effort Redis snapshot of one incident's public view -- never
    raises (backend/redis_store.py already swallows its own I/O errors)."""
    redis_incident_store.set_incident(incident.incident_id, incident.to_public_dict())


def restore_persisted_incidents() -> int:
    """Load any incidents persisted by a previous process (backend/redis_store.py)
    into RESTORED_INCIDENTS. Called once at FastAPI startup. Returns the
    count restored, for the startup log line."""
    restored = {d["incident_id"]: d for d in redis_incident_store.list_incidents() if d.get("incident_id")}
    RESTORED_INCIDENTS.update(restored)
    return len(restored)


def create_incident(incident_id: str, alerts: List[Alert], log_corpus: List[str], scenario: Optional[str] = None) -> IncidentState:
    incident = IncidentState(incident_id=incident_id, alerts=alerts, log_corpus=log_corpus, scenario=scenario)
    INCIDENTS[incident_id] = incident
    _broadcast_global({"type": "incident_created", "incident": incident.summary_dict()})
    _persist(incident)
    return incident


def get_incident(incident_id: str) -> Optional[IncidentState]:
    return INCIDENTS.get(incident_id)


def get_incident_view(incident_id: str) -> Optional[dict]:
    """JSON-safe incident view regardless of whether it's live or was
    restored from a previous process's persisted state."""
    incident = INCIDENTS.get(incident_id)
    if incident:
        return incident.to_public_dict()
    return RESTORED_INCIDENTS.get(incident_id)


def list_incidents() -> List[IncidentState]:
    return sorted(INCIDENTS.values(), key=lambda i: i.created_at, reverse=True)


def list_incident_summaries() -> List[dict]:
    """Summaries for GET /incidents: live incidents first (newest first),
    then restored-but-not-live ones so a prior process's completed
    incidents remain visible after a restart."""
    live = [i.summary_dict() for i in list_incidents()]
    live_ids = {i.incident_id for i in INCIDENTS.values()}
    restored = [
        {
            "incident_id": data.get("incident_id", incident_id),
            "status": data.get("status", "RESOLVED"),
            "severity": (data.get("triage") or {}).get("severity", "P4"),
            "service": ((data.get("triage") or {}).get("affected_services") or ["unknown"])[0],
            "scenario": data.get("scenario"),
            "title": (data.get("alerts") or [{}])[0].get("title", ""),
            "created_at": data.get("created_at", 0),
            "updated_at": data.get("updated_at", 0),
            "has_pending_hitl": False,
            "restored": True,
        }
        for incident_id, data in RESTORED_INCIDENTS.items()
        if incident_id not in live_ids
    ]
    return live + sorted(restored, key=lambda i: i["created_at"], reverse=True)


def list_pending_hitl() -> List[dict]:
    out = []
    for incident in INCIDENTS.values():
        for req, _ in incident.hitl_pending.values():
            if req.status == HITLStatus.PENDING:
                out.append({"incident_id": incident.incident_id, "request": req.model_dump()})
    return out


# ---------------------------------------------------------------------------
# SSE plumbing
# ---------------------------------------------------------------------------
def subscribe(incident_id: str) -> "asyncio.Queue[AgentTraceStep]":
    incident = INCIDENTS[incident_id]
    q: "asyncio.Queue[AgentTraceStep]" = asyncio.Queue()
    incident._subscribers.append(q)
    return q


def unsubscribe(incident_id: str, q: "asyncio.Queue[AgentTraceStep]") -> None:
    incident = INCIDENTS.get(incident_id)
    if incident and q in incident._subscribers:
        incident._subscribers.remove(q)


def subscribe_global() -> "asyncio.Queue[dict]":
    q: "asyncio.Queue[dict]" = asyncio.Queue()
    _GLOBAL_SUBSCRIBERS.append(q)
    return q


def unsubscribe_global(q: "asyncio.Queue[dict]") -> None:
    if q in _GLOBAL_SUBSCRIBERS:
        _GLOBAL_SUBSCRIBERS.remove(q)


def _broadcast_global(payload: dict) -> None:
    for q in list(_GLOBAL_SUBSCRIBERS):
        q.put_nowait(payload)


# ---------------------------------------------------------------------------
# PipelineHooks implementation (consumed by agents/pipeline.py)
# ---------------------------------------------------------------------------
class StoreHooks:
    def __init__(self, incident_id: str):
        self.incident_id = incident_id

    def on_status(self, incident_id: str, status: IncidentStatus) -> None:
        incident = INCIDENTS.get(incident_id)
        if not incident:
            return
        incident.status = status
        incident.updated_at = time.time()
        _broadcast_global({"type": "status_update", "incident": incident.summary_dict()})
        _persist(incident)

        if status == IncidentStatus.RESOLVED:
            severity = incident.triage.severity.value if incident.triage else "unknown"
            pipeline_duration.labels(severity=severity).observe(incident.updated_at - incident.created_at)
        elif status == IncidentStatus.ERROR:
            pipeline_errors_total.labels(agent_name="pipeline", error_type="pipeline_error").inc()

    def on_trace(self, step: AgentTraceStep) -> None:
        incident = INCIDENTS.get(step.incident_id)
        if not incident:
            return
        incident.trace.append(step)
        incident.updated_at = time.time()
        for q in list(incident._subscribers):
            q.put_nowait(step)
        _broadcast_global({"type": "trace", "incident_id": step.incident_id, "step": step.model_dump()})
        _persist(incident)

    def log_aims(self, event) -> None:
        aims_log_event(event)
        TOTAL_TOKENS_USED["count"] += event.tokens_used

    def set_result(self, incident_id: str, key: str, value) -> None:
        incident = INCIDENTS.get(incident_id)
        if not incident:
            return
        if key not in ("triage", "diagnosis", "runbook", "rca", "pipeline_metadata"):
            raise ValueError(f"Unknown pipeline result key: {key}")
        setattr(incident, key, value)
        incident.updated_at = time.time()
        _broadcast_global({"type": "status_update", "incident": incident.summary_dict()})
        _persist(incident)

        if key == "triage":
            incidents_total.labels(severity=value.severity.value, scenario=incident.scenario or "unknown").inc()

    def add_hallucination_report(self, incident_id: str, report: dict) -> None:
        incident = INCIDENTS.get(incident_id)
        if not incident:
            return
        incident.hallucination_reports.append(report)
        incident.updated_at = time.time()
        if not report.get("passed", True):
            _broadcast_global({"type": "hallucination_warning", "incident": incident.summary_dict()})

    async def await_hitl(self, incident_id: str, requests: List[HITLRequest]) -> List[HITLRequest]:
        incident = INCIDENTS.get(incident_id)
        if not incident:
            return requests

        events: Dict[str, asyncio.Event] = {}
        for req in requests:
            ev = asyncio.Event()
            incident.hitl_pending[req.request_id] = (req, ev)
            events[req.request_id] = ev
        _broadcast_global({"type": "hitl_pending", "incident": incident.summary_dict()})

        async def _wait_one(req: HITLRequest, ev: asyncio.Event) -> HITLRequest:
            try:
                await asyncio.wait_for(ev.wait(), timeout=config.HITL_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                if req.status == HITLStatus.PENDING:
                    req.status = HITLStatus.TIMED_OUT
                    req.decided_at = time.time()
                    req.decided_by = "system (timeout)"
            incident.hitl_history.append(req)
            return req

        resolved = await asyncio.gather(*[_wait_one(r, events[r.request_id]) for r in requests])
        _broadcast_global({"type": "hitl_resolved", "incident": incident.summary_dict()})
        return list(resolved)


class HITLNotFoundError(Exception):
    """Raised when the incident or the HITL request_id does not exist."""


class HITLAlreadyDecidedError(Exception):
    """Raised when the HITL request has already been approved/rejected/timed out."""

    def __init__(self, request: HITLRequest):
        self.request = request
        super().__init__(f"HITL request {request.request_id} already {request.status.value}")


def resolve_hitl(incident_id: str, request_id: str, approve: bool, decided_by: str = "human", note: str = "") -> HITLRequest:
    """Called synchronously from the FastAPI approve/reject route handlers.

    Raises HITLNotFoundError if the incident or request_id doesn't exist, or
    HITLAlreadyDecidedError if the request has already been resolved -- the
    caller maps these to 404 / 409 respectively.
    """
    incident = INCIDENTS.get(incident_id)
    if not incident:
        raise HITLNotFoundError(f"Incident {incident_id} not found")
    pending = incident.hitl_pending.get(request_id)
    if not pending:
        raise HITLNotFoundError(f"HITL request {request_id} not found for incident {incident_id}")
    req, ev = pending
    if req.status != HITLStatus.PENDING:
        raise HITLAlreadyDecidedError(req)
    req.status = HITLStatus.APPROVED if approve else HITLStatus.REJECTED
    req.decided_at = time.time()
    req.decided_by = decided_by
    ev.set()
    hitl_decisions_total.labels(
        decision=req.status.value.lower(), risk_level=req.action.risk_level.value
    ).inc()
    return req
