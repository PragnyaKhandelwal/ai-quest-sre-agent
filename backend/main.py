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
import time
import uuid
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from agents import config
from agents.metrics_tracker import tracker
from agents.pipeline import run_pipeline
from agents.schemas import AIMSEvent, Alert
from agents.tools import call_tool, describe_tools
from backend import store
from backend.aims_logger import log_event as aims_log_event
from backend.aims_logger import recent_events, total_tokens_used
from backend.mock_generator import get_scenario, list_scenarios
from backend.rca_pdf import render_rca_pdf
from backend.store import StoreHooks

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sre_agent.backend")

app = FastAPI(
    title="Governed Multi-Agent SRE Incident Triage & Remediation",
    description="HiDevs AI Quest PS03 -- Enterprise Cloud Incident Triage & Runbook Remediation Agent",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # demo/hackathon: open CORS so the Vercel-hosted frontend can reach Render
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all safety net: log every uncaught exception with a full
    traceback so nothing fails silently, and never leak internals to the
    client -- everything expected (404/409/422/503) is raised explicitly as
    HTTPException elsewhere and never reaches this handler."""
    logger.error("Unhandled exception on %s %s", request.method, request.url.path, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.middleware("http")
async def add_powered_by_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Powered-By"] = "Lyzr-Automata"
    response.headers["X-Agent-Pipeline"] = "SRE-4-Agent-Mesh"
    return response


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
# Health & meta
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "lyzr_enabled": config.LYZR_ENABLED,
        "model": config.MODEL,
        "confidence_threshold": config.CONFIDENCE_THRESHOLD,
        "hitl_timeout_seconds": config.HITL_TIMEOUT_SECONDS,
        "incidents_tracked": len(store.INCIDENTS),
    }


@app.get("/lyzr/status")
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


@app.get("/scenarios")
def scenarios():
    return list_scenarios()


@app.get("/stats")
def stats():
    return {
        "total_tokens_used": total_tokens_used(),
        "total_incidents": len(store.INCIDENTS),
        "pending_hitl": len(store.list_pending_hitl()),
    }


@app.get("/metrics")
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


@app.get("/incidents/{incident_id}/metrics")
def incident_metrics(incident_id: str):
    incident = store.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    return tracker.get_incident_metrics(incident_id)


# ---------------------------------------------------------------------------
# Tool calling (agents/tools.py)
# ---------------------------------------------------------------------------
@app.get("/tools")
def list_tools():
    """Tool registry with typed input/output schemas -- judges can see every
    tool an agent can call without needing to import Python classes."""
    return describe_tools()


@app.post("/tools/{tool_name}")
def invoke_tool(tool_name: str, input_data: dict):
    """Call a tool directly with a JSON body (for demo/testing). Every call
    is logged to Lyzr AIMS with the tool name in metadata.tool_called, so
    the audit trail reflects real tool invocations, not just agent calls."""
    start = time.perf_counter()
    try:
        result = call_tool(tool_name, input_data)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
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
@app.get("/aims/events")
def aims_events():
    """Full chronological Lyzr AIMS audit trail across all incidents."""
    return recent_events()


@app.get("/aims/events/{incident_id}")
def aims_events_for_incident(incident_id: str):
    """Lyzr AIMS audit trail filtered to one incident."""
    return recent_events(incident_id=incident_id)


# ---------------------------------------------------------------------------
# Alert ingestion
# ---------------------------------------------------------------------------
@app.post("/alerts/ingest")
async def ingest_alerts(payload: IngestPayload):
    if not payload.alerts:
        raise HTTPException(status_code=400, detail="At least one alert is required.")
    incident_id = _new_incident_id()
    log_corpus = payload.logs or []
    try:
        store.create_incident(incident_id, payload.alerts, log_corpus)
        asyncio.create_task(_launch_pipeline(incident_id, payload.alerts, log_corpus))
    except Exception:
        logger.error("Failed to start pipeline for incident %s", incident_id, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Agent pipeline unavailable",
            headers={"Retry-After": "30"},
        )
    return {"incident_id": incident_id, "status": "pipeline_started"}


@app.post("/simulate/{scenario}")
async def simulate(scenario: int):
    try:
        name, alerts, log_corpus = get_scenario(scenario)
    except ValueError:
        raise HTTPException(status_code=422, detail="Scenario must be 1-6")

    incident_id = _new_incident_id()
    try:
        store.create_incident(incident_id, alerts, log_corpus, scenario=name)
        asyncio.create_task(_launch_pipeline(incident_id, alerts, log_corpus))
    except Exception:
        logger.error(
            "Failed to start pipeline for incident %s (scenario %s)", incident_id, scenario, exc_info=True
        )
        raise HTTPException(
            status_code=503,
            detail="Agent pipeline unavailable",
            headers={"Retry-After": "30"},
        )
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


@app.post("/alerts/webhook")
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
@app.get("/incidents")
def list_incidents():
    return [i.summary_dict() for i in store.list_incidents()]


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: str):
    incident = store.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    return incident.to_public_dict()


@app.get("/incidents/{incident_id}/stream")
async def stream_incident(incident_id: str):
    incident = store.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")

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


@app.get("/events")
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
@app.get("/hitl/pending")
def hitl_pending():
    return store.list_pending_hitl()


@app.post("/hitl/{incident_id}/approve")
def hitl_approve(incident_id: str, payload: HITLDecisionPayload):
    try:
        req = store.resolve_hitl(
            incident_id, payload.request_id, approve=True, decided_by=payload.decided_by, note=payload.note
        )
    except store.HITLNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except store.HITLAlreadyDecidedError:
        raise HTTPException(status_code=409, detail="Action already resolved")
    return {"request_id": req.request_id, "status": req.status.value}


@app.post("/hitl/{incident_id}/reject")
def hitl_reject(incident_id: str, payload: HITLDecisionPayload):
    try:
        req = store.resolve_hitl(
            incident_id, payload.request_id, approve=False, decided_by=payload.decided_by, note=payload.note
        )
    except store.HITLNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except store.HITLAlreadyDecidedError:
        raise HTTPException(status_code=409, detail="Action already resolved")
    return {"request_id": req.request_id, "status": req.status.value}


# ---------------------------------------------------------------------------
# RCA export
# ---------------------------------------------------------------------------
@app.get("/incidents/{incident_id}/rca")
def get_rca(incident_id: str):
    incident = store.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    if not incident.rca:
        raise HTTPException(status_code=404, detail=f"RCA not yet generated for incident {incident_id}")
    return incident.rca.model_dump()


@app.get("/incidents/{incident_id}/rca/pdf")
def get_rca_pdf(incident_id: str):
    incident = store.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    if not incident.rca:
        raise HTTPException(status_code=404, detail=f"RCA not yet generated for incident {incident_id}")
    try:
        pdf_bytes = render_rca_pdf(incident.rca)
    except Exception:
        logger.error("Failed to render RCA PDF for incident %s", incident_id, exc_info=True)
        raise HTTPException(status_code=503, detail="Agent pipeline unavailable", headers={"Retry-After": "30"})
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="RCA_{incident_id}.pdf"'},
    )


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------
@app.get("/incidents/{incident_id}/audit")
def get_audit_trail(incident_id: str):
    return recent_events(incident_id=incident_id)
