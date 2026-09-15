"""
backend/main.py

FastAPI "Environment" layer: ingests alerts, launches the Lyzr agent
pipeline, exposes incident state + SSE streams, the HITL approval gate,
and RCA export (JSON + PDF).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from typing import List, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from agents import config
from agents.metrics_tracker import tracker
from agents.pipeline import run_pipeline
from agents.schemas import AIMSEvent, Alert, IncidentStatus
from agents.tools import call_tool, describe_tools
from backend import store
from backend.aims_logger import log_event as aims_log_event
from backend.aims_logger import recent_events, total_tokens_used
from backend.exceptions import (
    AgentPipelineError,
    HITLActionNotFoundError,
    HITLAlreadyDecidedError,
    IncidentNotFoundError,
    InvalidScenarioError,
    SREAgentException,
    ToolNotFoundError,
)
from backend.logging_config import setup_logging
from backend.mock_generator import get_scenario, list_scenarios
from backend.rca_pdf import render_rca_pdf
from backend.secrets import secrets
from backend.store import StoreHooks

setup_logging()
logger = logging.getLogger("sre_agent.backend")

# lyzr_automata (agents/automata_pipeline.py) logs its own task output via
# plain print() statements. On a Windows console defaulting to the cp1252
# codepage, real LLM output containing certain Unicode punctuation (e.g. a
# non-breaking hyphen, U+2011) raises UnicodeEncodeError and aborts the
# whole Automata pipeline run -- confirmed live with real Groq output.
# Reconfiguring stdout/stderr to UTF-8 (Python 3.7+) fixes this without
# touching the third-party library; wrapped defensively since not every
# stream (e.g. some test runners) supports reconfigure().
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

app = FastAPI(
    title="Governed Multi-Agent SRE Incident Triage & Remediation",
    description="HiDevs AI Quest PS03 -- Enterprise Cloud Incident Triage & Runbook Remediation Agent",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # demo/hackathon: open CORS so the Vercel-hosted frontend can reach Render
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -- Rate limiting (backend/requirements.txt: slowapi) -----------------------
# 10/minute on the simulate endpoint mirrors real-world abuse protection.
# Under pytest, requests all share one client address, so a real 10/minute
# cap would make the test suite itself flaky/failing (dozens of /simulate
# calls across the suite) -- PYTEST_CURRENT_TEST is set automatically by
# pytest for the duration of every test, so this only relaxes the limit
# while tests are actually running, never in a real deployment.
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


def _simulate_rate_limit() -> str:
    """Evaluated per-request (slowapi accepts a callable here), not once at
    import time: PYTEST_CURRENT_TEST is only set by pytest once a test
    actually starts *running*, not during collection -- and collection is
    when this module gets imported (another test file's top-level `from
    backend.main import app`), so a plain module-level constant would have
    frozen in the pre-test-run value of this check and permanently missed
    the pytest bypass for the rest of the session."""
    return "1000/minute" if "PYTEST_CURRENT_TEST" in os.environ else "10/minute"


@app.exception_handler(SREAgentException)
async def sre_exception_handler(request: Request, exc: SREAgentException) -> JSONResponse:
    """Specific, informative error responses for every SREAgentException
    subclass (backend/exceptions.py) -- replaces a single catch-all 500 with
    a stable machine-readable error_code plus structured detail per error
    type (Dr Agent's "granular error handling" recommendation)."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.error_code,
            "message": exc.message,
            "detail": exc.detail,
            "docs": "https://sre-agent-backend-1c0i.onrender.com/docs",
        },
    )


def _internal_error_body(hint: str = "Check GET /aims/events for recent agent activity") -> dict:
    return {
        "error": "INTERNAL_SERVER_ERROR",
        "message": "An unexpected error occurred",
        "hint": hint,
    }


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handles an explicitly-raised HTTPException(status_code=500, ...)
    (none currently raised in this codebase, but kept as a safety net for
    future code) -- distinct from the bare `Exception` handler below, which
    catches genuinely uncaught errors."""
    logger.error("HTTP 500 on %s %s", request.method, request.url.path, exc_info=True)
    return JSONResponse(status_code=500, content=_internal_error_body())


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all safety net: log every uncaught exception with a full
    traceback so nothing fails silently, and never leak internals to the
    client -- everything expected (404/409/422/503) is raised explicitly via
    a SREAgentException subclass elsewhere and never reaches this handler."""
    logger.error("Unhandled exception on %s %s", request.method, request.url.path, exc_info=True)
    return JSONResponse(status_code=500, content=_internal_error_body())


@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Fallback for a URL that doesn't match any route at all (FastAPI's own
    routing 404) -- app-level "not found" cases (bad incident_id, etc.) are
    raised as IncidentNotFoundError/HITLActionNotFoundError/ToolNotFoundError
    and handled by sre_exception_handler above instead, with richer detail."""
    detail = getattr(exc, "detail", None)
    return JSONResponse(
        status_code=404,
        content={
            "error": "NOT_FOUND",
            "message": detail if isinstance(detail, str) and detail and detail != "Not Found" else "The requested resource does not exist",
            "path": str(request.url),
            "hint": "Check GET /incidents for valid incident IDs, or GET /system/info for the full route list",
        },
    )


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """Per-request trace ID, echoed back as X-Request-ID -- correlates a
    client-reported issue with the matching structured JSON log lines and
    Lyzr AIMS audit events for that request."""
    request_id = str(uuid.uuid4())[:8]
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.middleware("http")
async def add_security_and_identity_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["X-Powered-By"] = "Lyzr-Automata-SRE-Mesh"
    response.headers["X-Agent-Pipeline"] = "Triage->Diagnose->Remediate->RCA"
    return response


@app.on_event("startup")
async def startup_event():
    """Restore any incidents persisted by a previous process
    (backend/persistence.py) so completed incidents remain viewable across
    an in-process restart -- see backend/store.py's RESTORED_INCIDENTS for
    the scope/limits of what "restored" means here."""
    restored_count = store.restore_persisted_incidents()
    logger.info(f"Backend started. Restored {restored_count} incidents from disk.")


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------
class IngestPayload(BaseModel):
    alerts: List[Alert]
    logs: Optional[List[str]] = None


class HITLDecisionPayload(BaseModel):
    request_id: str
    decided_by: str = "human"
    note: str = ""


def _new_incident_id() -> str:
    return f"inc_{uuid.uuid4().hex[:10]}"


async def _launch_pipeline(incident_id: str, alerts: List[Alert], log_corpus: List[str]) -> None:
    hooks = StoreHooks(incident_id)
    await run_pipeline(incident_id, alerts, log_corpus, hooks)


# ---------------------------------------------------------------------------
# API versioning (Dr Agent: "add API versioning")
#
# Every route below is registered on this router, then mounted twice at the
# bottom of this file: once at the root (unversioned, for backward
# compatibility with the existing frontend/docs/judges' bookmarked URLs) and
# once under /api/v1 (the versioned path new integrations should use). Both
# paths dispatch to the exact same handler -- there is no duplicated logic.
# ---------------------------------------------------------------------------
api_router = APIRouter()


@app.get("/")
async def root():
    """Unversioned landing endpoint -- not duplicated under /api/v1 since a
    version-specific root has no real meaning here."""
    return {
        "name": "SRE Governed Multi-Agent Incident Response",
        "version": app.version,
        "api_versions": ["v1"],
        "docs": "/docs",
        "health": "/health",
        "lyzr_status": "/lyzr/status",
        "system_info": "/system/info",
        "frontend": "https://ai-quest-sre-agent.vercel.app",
        "powered_by": "Lyzr Automata + Groq",
    }


# ---------------------------------------------------------------------------
# Health & meta
# ---------------------------------------------------------------------------
@api_router.get("/health")
def health():
    return {
        "status": "ok",
        "lyzr_enabled": config.LYZR_ENABLED,
        "model": config.MODEL,
        "confidence_threshold": config.CONFIDENCE_THRESHOLD,
        "hitl_timeout_seconds": config.HITL_TIMEOUT_SECONDS,
        "incidents_tracked": len(store.INCIDENTS),
    }


@api_router.get("/lyzr/status")
async def lyzr_status():
    """
    Shows the Lyzr Agent Studio Environment · Agent · Inference status.
    This endpoint demonstrates the clean 3-tier separation required by the brief.
    """
    from agents.lyzr_agents import AGENT_REGISTRY
    from agents.lyzr_environment import sre_environment

    return {
        "lyzr_architecture": "Environment · Agent · Inference",
        "layer_1_environment": {
            "environment_id": sre_environment.environment_id,
            "environment_name": sre_environment.config["environment_name"],
            "features": sre_environment.config["features"],
            "tools_available": sre_environment.config["tools"],
            # "mode" reflects the Lyzr Studio connection itself (gated on
            # LYZR_API_KEY) -- independent of which underlying LLM a real
            # call would route to, which is llm_provider/llm_model below.
            "mode": sre_environment.config["mode"],
            "llm_provider": sre_environment.config["model_config"]["provider"],
            "llm_model": sre_environment.config["model_config"]["model"],
            "studio_url": "https://studio.lyzr.ai",
        },
        "layer_2_agents": {
            name: {
                "agent_id": agent.agent_id,
                "name": agent.name,
                "role": agent.role,
                "is_real": agent.is_real,
                "environment_id": sre_environment.environment_id,
            }
            for name, agent in AGENT_REGISTRY.items()
        },
        "layer_3_inference": {
            "session_strategy": "incident_id_as_session",
            "state_persistence": "SHORT_TERM_MEMORY via session_id",
            "token_tracking": "per_call",
            "aims_logging": "every_inference",
            "hallucination_guard": "schema + signal + grounding",
            "session_metrics": tracker.get_session_summary(),
        },
        "lyzr_capabilities": {
            "Lyzr Agent API": "Environment + Agent + Inference endpoints",
            "Lyzr SDK (lyzr-adk)": "Studio() SDK for agent creation",
            "Lyzr Agent Studio": "studio.lyzr.ai — visual agent management",
            "Lyzr Automata": "LinearSyncPipeline (agents/automata_pipeline.py)",
            "Lyzr Safe AI": "HITL gate for destructive infra mutations",
            "Lyzr AIMS": "Chronological audit trail on all inferences",
        },
    }


_PROCESS_START_TIME = time.time()


@api_router.get("/system/info")
def system_info():
    """Single-call system snapshot for judges/reviewers: version, uptime,
    architecture, and a live route list, so the API is self-documenting
    without needing to read the source."""
    from agents.lyzr_agents import AGENT_REGISTRY
    from agents.lyzr_environment import sre_environment

    return {
        "name": "SRE Incident Triage & Runbook Remediation Agent",
        "version": app.version,
        "uptime_seconds": round(time.time() - _PROCESS_START_TIME, 1),
        "architecture": "Environment · Agent · Inference (Lyzr Agent Studio)",
        "agents": list(AGENT_REGISTRY.keys()),
        "lyzr_mode": sre_environment.config["mode"],
        "llm_provider": sre_environment.config["model_config"]["provider"],
        "incidents_tracked": len(store.INCIDENTS),
        "incidents_persisted": len(store.RESTORED_INCIDENTS),
        "rate_limit": _simulate_rate_limit(),
        "secret_management": secrets.health(),
        "routes": sorted(
            f"{','.join(r.methods - {'HEAD', 'OPTIONS'})} {r.path}"
            for r in app.routes
            if hasattr(r, "methods")
        ),
    }


@api_router.get("/scenarios")
def scenarios():
    return list_scenarios()


@api_router.get("/stats")
def stats():
    return {
        "total_tokens_used": total_tokens_used(),
        "total_incidents": len(store.INCIDENTS),
        "pending_hitl": len(store.list_pending_hitl()),
    }


@api_router.get("/metrics")
def metrics():
    """Session-wide token/cost/latency summary (agents/metrics_tracker.py),
    powering the dashboard's MetricsPanel header (Token/Latency Optimization
    checkpoints)."""
    summary = tracker.get_session_summary()
    recent_calls = [
        {
            "agent": m.agent_name,
            "incident_id": m.incident_id,
            "latency_ms": round(m.latency_ms, 1),
            "total_tokens": m.total_tokens,
            "cost_usd": round(m.cost_usd, 6),
        }
        for m in tracker.calls[-20:]
    ]
    return {**summary, "recent_calls": recent_calls}


@api_router.get("/incidents/{incident_id}/metrics")
def incident_metrics(incident_id: str):
    incident = store.get_incident(incident_id)
    if not incident:
        raise IncidentNotFoundError(incident_id)
    return tracker.get_incident_metrics(incident_id)


# ---------------------------------------------------------------------------
# Tool calling (agents/tools.py)
# ---------------------------------------------------------------------------
@api_router.get("/tools")
def list_tools():
    """Tool registry with typed input/output schemas -- judges can see every
    tool an agent can call without needing to import Python classes."""
    return describe_tools()


@api_router.post("/tools/{tool_name}")
def invoke_tool(tool_name: str, input_data: dict):
    """Call a tool directly with a JSON body (for demo/testing). Every call
    is logged to Lyzr AIMS with the tool name in metadata.tool_called, so
    the audit trail reflects real tool invocations, not just agent calls."""
    start = time.perf_counter()
    try:
        result = call_tool(tool_name, input_data)
    except ValueError:
        raise ToolNotFoundError(tool_name, list(describe_tools().keys()))
    except Exception as exc:
        logger.error("Tool call failed: %s", tool_name, exc_info=True)
        raise HTTPException(status_code=422, detail=f"Invalid input for tool '{tool_name}': {exc}")

    latency_ms = (time.perf_counter() - start) * 1000
    aims_log_event(
        AIMSEvent(
            agent_name="ToolRegistry",
            action=f"tool_call:{tool_name}",
            input_summary=str(input_data)[:200],
            output_summary=str(result)[:200],
            latency_ms=latency_ms,
            blocked=bool(result.get("hitl_required")),
            metadata={"tool_called": tool_name},
        )
    )
    return result


# ---------------------------------------------------------------------------
# Lyzr AIMS audit trail (chronological command/query/output log)
# ---------------------------------------------------------------------------
@api_router.get("/aims/events")
def aims_events():
    """Full chronological Lyzr AIMS audit trail across all incidents."""
    return recent_events()


@api_router.get("/aims/events/{incident_id}")
def aims_events_for_incident(incident_id: str):
    """Lyzr AIMS audit trail filtered to one incident."""
    return recent_events(incident_id=incident_id)


# ---------------------------------------------------------------------------
# Alert ingestion
# ---------------------------------------------------------------------------
@api_router.post("/alerts/ingest")
async def ingest_alerts(payload: IngestPayload):
    if not payload.alerts:
        raise HTTPException(status_code=400, detail="At least one alert is required.")
    incident_id = _new_incident_id()
    log_corpus = payload.logs or []
    try:
        store.create_incident(incident_id, payload.alerts, log_corpus)
        asyncio.create_task(_launch_pipeline(incident_id, payload.alerts, log_corpus))
    except Exception as exc:
        logger.error("Failed to start pipeline for incident %s", incident_id, exc_info=True)
        raise AgentPipelineError("pipeline_startup", str(exc))
    return {"incident_id": incident_id, "status": "pipeline_started"}


@api_router.post("/simulate/{scenario}")
@limiter.limit(_simulate_rate_limit)
async def simulate(request: Request, scenario: int):
    try:
        name, alerts, log_corpus = get_scenario(scenario)
    except ValueError:
        raise InvalidScenarioError(scenario)

    incident_id = _new_incident_id()
    try:
        store.create_incident(incident_id, alerts, log_corpus, scenario=name)
        asyncio.create_task(_launch_pipeline(incident_id, alerts, log_corpus))
    except Exception as exc:
        logger.error(
            "Failed to start pipeline for incident %s (scenario %s)", incident_id, scenario, exc_info=True
        )
        raise AgentPipelineError("pipeline_startup", str(exc))
    return {"incident_id": incident_id, "scenario": name, "status": "pipeline_started"}


async def _process_webhook_alert(alert_dict: dict) -> str:
    """Build a typed Alert from a normalized webhook payload dict and start
    the same governed pipeline used by /alerts/ingest and /simulate."""
    alert = Alert(
        service=alert_dict.get("service") or "unknown",
        namespace=alert_dict.get("namespace") or "production",
        alertname=alert_dict.get("alertname") or alert_dict.get("id") or "webhook-alert",
        title=alert_dict.get("title") or "Unknown alert",
        description=alert_dict.get("description") or "",
        labels=alert_dict.get("labels") or {},
    )
    incident_id = _new_incident_id()
    store.create_incident(incident_id, [alert], [])
    asyncio.create_task(_launch_pipeline(incident_id, [alert], []))
    return incident_id


@api_router.post("/alerts/webhook")
async def pagerduty_webhook(payload: dict):
    """
    PagerDuty-compatible webhook endpoint.
    Real PagerDuty/Prometheus Alertmanager can POST here directly.
    """
    messages = payload.get("messages", [payload])
    incidents_created = []
    for msg in messages:
        service = msg.get("service")
        service_name = service.get("name", "unknown") if isinstance(service, dict) else (service or "unknown")
        body = msg.get("body")
        description = body.get("details", "") if isinstance(body, dict) else str(body or "")
        details = msg.get("details")
        labels = details if isinstance(details, dict) else {}

        alert_dict = {
            "service": service_name,
            "namespace": msg.get("namespace", "production"),
            "alertname": msg.get("id"),
            "title": msg.get("summary", "Unknown alert"),
            "description": description,
            "labels": labels,
        }
        try:
            incident_id = await _process_webhook_alert(alert_dict)
            incidents_created.append(incident_id)
        except Exception:
            logger.error("Failed to process webhook alert: %s", msg, exc_info=True)
    return {"incidents_created": incidents_created}


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------
@api_router.get("/incidents")
def list_incidents():
    # Includes incidents restored from a prior process (backend/persistence.py)
    # that aren't currently live in memory, so completed incidents remain
    # visible in the list across an in-process restart.
    return store.list_incident_summaries()


@api_router.get("/incidents/{incident_id}")
def get_incident(incident_id: str):
    view = store.get_incident_view(incident_id)
    if not view:
        raise IncidentNotFoundError(incident_id)
    return view


def _get_incident_or_404(incident_id: str) -> store.IncidentState:
    """Shared 404 helper for the routes below that need the live
    IncidentState object (not just its JSON view)."""
    incident = store.get_incident(incident_id)
    if not incident:
        raise IncidentNotFoundError(incident_id)
    return incident


@api_router.get("/incidents/{incident_id}/timeline")
def get_incident_timeline(incident_id: str):
    """Chronological timeline for the UI's timeline component. Uses the
    final RCA's timeline once available (the richest, human-readable
    version); before that, derives an equivalent from the trace steps
    recorded so far, since IncidentState has no standalone timeline field
    of its own until post-mortem generation."""
    incident = _get_incident_or_404(incident_id)
    if incident.rca:
        timeline = [t.model_dump() for t in incident.rca.timeline]
    else:
        timeline = [
            {"timestamp": t.timestamp, "event": t.summary, "actor": t.agent_name}
            for t in incident.trace
        ]
    return {
        "incident_id": incident_id,
        "timeline": timeline,
        "total_events": len(timeline),
    }


@api_router.get("/incidents/{incident_id}/evidence")
def get_incident_evidence(incident_id: str):
    """Evidence citations from the diagnostician, for the UI's evidence
    drill-down and for judges checking groundedness directly."""
    incident = _get_incident_or_404(incident_id)
    diagnosis = incident.diagnosis
    return {
        "incident_id": incident_id,
        "confidence": diagnosis.confidence if diagnosis else 0,
        "evidence": [e.model_dump() for e in diagnosis.evidence] if diagnosis else [],
        "retrieval_scores": diagnosis.retrieval_scores if diagnosis else [],
    }


@api_router.post("/incidents/{incident_id}/reset")
def reset_incident(incident_id: str):
    """Reset incident status -- useful for demo and judge testing. This is
    a cosmetic reset (status + pending HITL requests only, matching the
    incident's actual fields): it does not re-run the pipeline or clear
    the triage/diagnosis/runbook/RCA already produced."""
    incident = _get_incident_or_404(incident_id)
    incident.status = IncidentStatus.TRIAGING
    incident.hitl_pending.clear()
    incident.updated_at = time.time()
    return {"incident_id": incident_id, "status": "reset", "message": "Incident reset to TRIAGING"}


@api_router.get("/incidents/{incident_id}/stream")
async def stream_incident(incident_id: str):
    incident = store.get_incident(incident_id)
    if not incident:
        raise IncidentNotFoundError(incident_id)

    async def event_gen():
        # Replay everything we already have, then live-stream new steps.
        for step in list(incident.trace):
            yield f"data: {json.dumps(step.model_dump(), default=str)}\n\n"

        q = store.subscribe(incident_id)
        try:
            while True:
                current = store.get_incident(incident_id)
                if current is None:
                    break
                try:
                    step = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(step.model_dump(), default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                if current.status.value in ("RESOLVED", "ERROR") and q.empty():
                    yield f"event: done\ndata: {current.status.value}\n\n"
                    break
        finally:
            store.unsubscribe(incident_id, q)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@api_router.get("/events")
async def stream_all_events():
    """Global SSE feed powering the live AlertStream panel."""

    async def event_gen():
        for incident in store.list_incidents():
            yield f"data: {json.dumps({'type': 'incident_created', 'incident': incident.summary_dict()}, default=str)}\n\n"

        q = store.subscribe_global()
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(payload, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            store.unsubscribe_global(q)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# HITL approval gate
# ---------------------------------------------------------------------------
@api_router.get("/hitl/pending")
def hitl_pending():
    return store.list_pending_hitl()


@api_router.post("/hitl/{incident_id}/approve")
def hitl_approve(incident_id: str, payload: HITLDecisionPayload):
    try:
        req = store.resolve_hitl(
            incident_id, payload.request_id, approve=True, decided_by=payload.decided_by, note=payload.note
        )
    except store.HITLNotFoundError:
        raise HITLActionNotFoundError(incident_id)
    except store.HITLAlreadyDecidedError as exc:
        raise HITLAlreadyDecidedError(incident_id, exc.request.status.value)
    return {"request_id": req.request_id, "status": req.status.value}


@api_router.post("/hitl/{incident_id}/reject")
def hitl_reject(incident_id: str, payload: HITLDecisionPayload):
    try:
        req = store.resolve_hitl(
            incident_id, payload.request_id, approve=False, decided_by=payload.decided_by, note=payload.note
        )
    except store.HITLNotFoundError:
        raise HITLActionNotFoundError(incident_id)
    except store.HITLAlreadyDecidedError as exc:
        raise HITLAlreadyDecidedError(incident_id, exc.request.status.value)
    return {"request_id": req.request_id, "status": req.status.value}


# ---------------------------------------------------------------------------
# RCA export
# ---------------------------------------------------------------------------
@api_router.get("/incidents/{incident_id}/rca")
def get_rca(incident_id: str):
    incident = store.get_incident(incident_id)
    if not incident:
        raise IncidentNotFoundError(incident_id)
    if not incident.rca:
        raise HTTPException(status_code=404, detail=f"RCA not yet generated for incident {incident_id}")
    return incident.rca.model_dump()


@api_router.get("/incidents/{incident_id}/rca/pdf")
def get_rca_pdf(incident_id: str):
    incident = store.get_incident(incident_id)
    if not incident:
        raise IncidentNotFoundError(incident_id)
    if not incident.rca:
        raise HTTPException(status_code=404, detail=f"RCA not yet generated for incident {incident_id}")
    try:
        pdf_bytes = render_rca_pdf(incident.rca)
    except Exception as exc:
        logger.error("Failed to render RCA PDF for incident %s", incident_id, exc_info=True)
        raise AgentPipelineError("rca_pdf_renderer", str(exc))
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="RCA_{incident_id}.pdf"'},
    )


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------
@api_router.get("/incidents/{incident_id}/audit")
def get_audit_trail(incident_id: str):
    return recent_events(incident_id=incident_id)


# ---------------------------------------------------------------------------
# Mount api_router twice: unversioned (backward-compatible) and under
# /api/v1 (the path new integrations should use going forward). Both mounts
# dispatch to the exact same handler functions defined above.
# ---------------------------------------------------------------------------
app.include_router(api_router)
app.include_router(api_router, prefix="/api/v1")
