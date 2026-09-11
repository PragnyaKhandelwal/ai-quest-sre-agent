"""
backend/main.py

FastAPI "Environment" layer: ingests alerts, launches the Lyzr agent
pipeline, exposes incident state + SSE streams, the HITL approval gate,
and RCA export (JSON + PDF).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from agents import config
from agents.pipeline import run_pipeline
from agents.schemas import Alert

from backend import store
from backend.aims_logger import recent_events, total_tokens_used
from backend.mock_generator import get_scenario, list_scenarios
from backend.rca_pdf import render_rca_pdf
from backend.store import StoreHooks

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


# ---------------------------------------------------------------------------
# Alert ingestion
# ---------------------------------------------------------------------------
@app.post("/alerts/ingest")
async def ingest_alerts(payload: IngestPayload):
    if not payload.alerts:
        raise HTTPException(status_code=400, detail="At least one alert is required.")
    incident_id = _new_incident_id()
    log_corpus = payload.logs or []
    store.create_incident(incident_id, payload.alerts, log_corpus)
    asyncio.create_task(_launch_pipeline(incident_id, payload.alerts, log_corpus))
    return {"incident_id": incident_id, "status": "pipeline_started"}


@app.post("/simulate/{scenario}")
async def simulate(scenario: int):
    try:
        name, alerts, log_corpus = get_scenario(scenario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    incident_id = _new_incident_id()
    store.create_incident(incident_id, alerts, log_corpus, scenario=name)
    asyncio.create_task(_launch_pipeline(incident_id, alerts, log_corpus))
    return {"incident_id": incident_id, "scenario": name, "status": "pipeline_started"}


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
        raise HTTPException(status_code=404, detail="Incident not found.")
    return incident.to_public_dict()


@app.get("/incidents/{incident_id}/stream")
async def stream_incident(incident_id: str):
    incident = store.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found.")

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
    req = store.resolve_hitl(incident_id, payload.request_id, approve=True, decided_by=payload.decided_by, note=payload.note)
    if not req:
        raise HTTPException(status_code=404, detail="HITL request not found or already resolved.")
    return {"request_id": req.request_id, "status": req.status.value}


@app.post("/hitl/{incident_id}/reject")
def hitl_reject(incident_id: str, payload: HITLDecisionPayload):
    req = store.resolve_hitl(incident_id, payload.request_id, approve=False, decided_by=payload.decided_by, note=payload.note)
    if not req:
        raise HTTPException(status_code=404, detail="HITL request not found or already resolved.")
    return {"request_id": req.request_id, "status": req.status.value}


# ---------------------------------------------------------------------------
# RCA export
# ---------------------------------------------------------------------------
@app.get("/incidents/{incident_id}/rca")
def get_rca(incident_id: str):
    incident = store.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found.")
    if not incident.rca:
        raise HTTPException(status_code=404, detail="RCA not yet generated for this incident.")
    return incident.rca.model_dump()


@app.get("/incidents/{incident_id}/rca/pdf")
def get_rca_pdf(incident_id: str):
    incident = store.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found.")
    if not incident.rca:
        raise HTTPException(status_code=404, detail="RCA not yet generated for this incident.")
    pdf_bytes = render_rca_pdf(incident.rca)
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
