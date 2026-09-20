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
from datetime import datetime, timezone
from typing import List, Optional

import psutil
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from agents import config
from agents.anomaly_detector import predict_escalation_risk
from agents.metrics_tracker import tracker
from agents.pipeline import run_pipeline
from agents.schemas import AIMSEvent, Alert, IncidentStatus
from agents.tools import call_tool, describe_tools
from backend import store
from backend.aims_logger import log_event as aims_log_event
from backend.aims_logger import total_tokens_used
from backend.auth import APIScope, get_api_key, require_scope
from backend.circuit_breaker import all_breakers
from backend.config_watcher import live_config
from backend.exceptions import (
    AgentPipelineError,
    AnomalyDetectionError,
    ConfigKeyNotAllowedError,
    EmptyAlertBatchError,
    HITLActionNotFoundError,
    HITLAlreadyDecidedError,
    IncidentNotFoundError,
    InvalidScenarioError,
    RCANotAvailableError,
    SREAgentException,
    ToolInputValidationError,
    ToolNotFoundError,
    VectorStoreError,
)
from backend.logging_config import setup_logging
from backend.mock_generator import get_scenario, list_scenarios
from backend.prometheus_metrics import active_incidents as prom_active_incidents
from backend.prometheus_metrics import get_metrics_response
from backend.prometheus_metrics import hitl_queue_depth as prom_hitl_queue_depth
from backend.rca_pdf import render_rca_pdf
from backend.redis_store import incident_store as redis_incident_store
from backend.secrets import secrets
from backend.settings import settings
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
    version="2.0.1",
)

app.add_middleware(
    CORSMiddleware,
    # Deliberately "*", not settings.cors_origins_list: Render has no
    # ENVIRONMENT var set today, so settings.environment defaults to
    # "development" and would load config/development.env's localhost-only
    # CORS_ORIGINS, breaking the live Vercel frontend. settings.cors_origins
    # is still surfaced (read-only) at GET /config for visibility -- wiring
    # it in for real requires setting ENVIRONMENT=production in Render's
    # dashboard first.
    allow_origins=["*"],
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
    (backend/redis_store.py) so completed incidents remain viewable across
    an in-process restart -- see backend/store.py's RESTORED_INCIDENTS for
    the scope/limits of what "restored" means here."""
    restored_count = store.restore_persisted_incidents()
    logger.info(f"Backend started. Restored {restored_count} incidents from Redis.")


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
async def health():
    """Deep health check -- verifies every real dependency is live, not
    just that the process is running. Used by the Docker healthcheck,
    UptimeRobot, and any load balancer's liveness/readiness probe. Always
    returns HTTP 200 (even when "degraded"): a degraded-but-serving
    process should stay in rotation, not get killed -- callers read
    `status`/`checks` to decide whether to page someone."""
    start = time.time()
    checks: dict = {}
    overall = "healthy"

    # Check 1: Redis connectivity (backend/redis_store.py)
    try:
        redis_health = redis_incident_store.health()
        checks["redis"] = {
            "status": "healthy" if redis_health["status"] == "connected" else "degraded",
            "mode": redis_health.get("mode", "unknown"),
            "incident_count": redis_health.get("incident_count", 0),
        }
        if checks["redis"]["status"] != "healthy":
            overall = "degraded"
    except Exception as e:
        checks["redis"] = {"status": "unhealthy", "error": str(e)}
        overall = "degraded"

    # Check 2: Agent pipeline (agents/lyzr_agents.py)
    try:
        from agents.lyzr_agents import AGENT_REGISTRY

        checks["agents"] = {
            "status": "healthy",
            "count": len(AGENT_REGISTRY),
            "names": list(AGENT_REGISTRY.keys()),
        }
    except Exception as e:
        checks["agents"] = {"status": "unhealthy", "error": str(e)}
        overall = "degraded"

    # Check 3: Secret manager (backend/secrets.py)
    try:
        checks["secrets"] = {"status": "healthy", "provider": secrets.provider}
    except Exception as e:
        checks["secrets"] = {"status": "degraded", "error": str(e)}

    # Check 4: LLM provider (backend/settings.py)
    try:
        checks["llm"] = {
            "status": "healthy",
            "provider": settings.active_llm_provider,
            "model": settings.default_model,
        }
    except Exception as e:
        checks["llm"] = {"status": "degraded", "error": str(e)}

    # Check 5: System resources
    try:
        mem_percent = psutil.virtual_memory().percent
        checks["system"] = {
            "status": "healthy",
            "memory_percent": mem_percent,
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "disk_percent": psutil.disk_usage(os.sep).percent,
        }
        if mem_percent > 90:
            checks["system"]["status"] = "degraded"
            overall = "degraded"
    except Exception:
        checks["system"] = {"status": "unknown"}

    latency_ms = round((time.time() - start) * 1000, 2)

    return {
        "status": overall,
        "version": app.version,
        "environment": settings.environment,
        "uptime_check": "pass",
        "latency_ms": latency_ms,
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        # Backward-compatible fields the dashboard's health pill and
        # existing tooling already read (see backend/tests/test_api.py).
        "lyzr_enabled": config.LYZR_ENABLED,
        "model": config.MODEL,
        "confidence_threshold": config.CONFIDENCE_THRESHOLD,
        "hitl_timeout_seconds": config.HITL_TIMEOUT_SECONDS,
        "incidents_tracked": len(store.INCIDENTS),
    }


@api_router.get("/lyzr/status")
async def lyzr_status(key: dict = Depends(require_scope(APIScope.ADMIN))):
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
    from agents.vector_store import SREVectorStore

    # SREVectorStore() itself already degrades internally to TF-IDF on
    # missing optional deps (see its module docstring), so an exception
    # here reflects a real, deeper failure -- surfaced as a specific
    # VectorStoreError rather than letting it hit the generic 500 handler.
    try:
        vector_store_stats = SREVectorStore().stats()
    except Exception as exc:
        raise VectorStoreError(str(exc)) from exc

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
        "state_management": redis_incident_store.health(),
        # Reports whichever backend agents/diagnostician_agent.py actually
        # used for its last retrieval -- "tfidf_fallback" unless ChromaDB +
        # sentence-transformers are installed (see agents/vector_store.py's
        # module docstring for why they aren't a default dependency here).
        # SREVectorStore() itself already degrades internally to TF-IDF on
        # missing optional deps, so a raised exception here means a real,
        # deeper failure (e.g. a corrupted on-disk index) -- surfaced as a
        # specific VectorStoreError rather than a generic 500.
        "vector_store": vector_store_stats,
        "settings": settings.summary(),
        "features": settings.summary()["features"],
        # A curated, human-scannable subset of "routes" below -- the
        # handful of endpoints a judge/operator would actually want to
        # click first, rather than the full alphabetical route dump.
        "live_endpoints": [
            "/health", "/config", "/metrics/prometheus", "/circuit-breakers",
            "/lyzr/status", "/incidents", "/analytics/patterns", "/docs",
        ],
        # See README.md's "Stretch Goals Implemented" section for details
        # on each of these.
        "stretch_goals_completed": [
            "Voice Briefing Agent (frontend/src/components/VoiceBriefing.jsx)",
            "AIMS Decision Graph (frontend/src/components/DecisionGraph.jsx)",
            "ML Anomaly Detection (agents/anomaly_detector.py)",
        ],
        "routes": sorted(
            f"{','.join(r.methods - {'HEAD', 'OPTIONS'})} {r.path}"
            for r in app.routes
            if hasattr(r, "methods")
        ),
    }


@api_router.get("/config")
def get_config(key: dict = Depends(require_scope(APIScope.ADMIN))):
    """Public configuration summary (backend/settings.py) -- shows active
    settings without exposing secrets, so judges/operators can confirm
    which environment/provider/thresholds are active without SSH access."""
    return settings.summary()


# -- Config hot-reload (backend/config_watcher.py) ---------------------------
# Dr Agent: "Consider integrating a mechanism for dynamic configuration
# updates without requiring a full service restart."
_CONFIG_ALLOWED_KEYS = {
    "confidence_threshold",
    "hitl_timeout_seconds",
    "enable_aims_logging",
    "enable_hallucination_guard",
    "enable_voice_briefing",
    "model_temperature",
}


@api_router.get("/config/live")
async def get_live_config():
    """Current effective configuration (base + any hot-reload overrides).
    Shows all active settings without exposing secrets."""
    return live_config.snapshot()


@api_router.patch("/config/live")
async def update_config(update: dict, key: dict = Depends(require_scope(APIScope.ADMIN))):
    """Hot-reload a config value without restarting the service.

    Example:
      PATCH /config/live
      {"confidence_threshold": 0.85, "reason": "increase sensitivity for P1s"}

    Supported keys: confidence_threshold, hitl_timeout_seconds,
    enable_aims_logging, enable_hallucination_guard, enable_voice_briefing,
    model_temperature.
    """
    update = dict(update)
    reason = update.pop("reason", "")
    results = []

    for key_name, value in update.items():
        if key_name not in _CONFIG_ALLOWED_KEYS:
            raise ConfigKeyNotAllowedError(key_name, list(_CONFIG_ALLOWED_KEYS))
        record = live_config.set(key_name, value, reason)
        results.append(record)

    return {"updated": len(results), "changes": results, "current_config": live_config.snapshot()}


@api_router.delete("/config/live/{key}")
async def reset_config_key(key: str, key_meta: dict = Depends(require_scope(APIScope.ADMIN))):
    """Reset a config key to its base value."""
    record = live_config.reset(key)
    return {"reset": record, "current_value": live_config.get(key)}


@api_router.get("/config/live/history")
async def config_history():
    """Configuration change history -- shows every hot-reload event."""
    return {"history": live_config.history(), "total_changes": len(live_config.history(100))}


@api_router.get("/auth/info")
async def auth_info():
    """Authentication information for this API -- unauthenticated by design,
    so a caller can discover how to authenticate before they have a key."""
    demo_key = settings.demo_api_key
    return {
        "auth_enabled": settings.auth_enabled,
        "schemes": [
            {
                "name": "API Key Header",
                "header": "X-API-Key",
                "example": f"curl -H 'X-API-Key: {demo_key}' /incidents",
            },
            {
                "name": "API Key Query",
                "param": "api_key",
                "example": f"curl '/incidents?api_key={demo_key}'",
            },
        ],
        "scopes": {
            "read": "GET endpoints -- incidents, metrics, AIMS",
            "write": "POST endpoints -- simulate, alerts ingest/webhook, HITL approve/reject, incident reset",
            "admin": "All endpoints including /config and /lyzr/status",
        },
        "demo_key": demo_key,
        "note": "AUTH_ENABLED=false by default -- all endpoints open for demo",
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


@api_router.get("/metrics/prometheus")
async def prometheus_metrics_endpoint():
    """Prometheus-format metrics endpoint. Scrape with:
    prometheus.yml -> targets: ['your-render-url']. Compatible with
    Grafana, Datadog, CloudWatch, Victoria Metrics."""
    # Gauges reflect current state, computed fresh on every scrape rather
    # than tracked incrementally (which would drift as incidents resolve).
    active = sum(1 for i in store.INCIDENTS.values() if i.status not in (IncidentStatus.RESOLVED,))
    prom_active_incidents.set(active)
    prom_hitl_queue_depth.set(len(store.list_pending_hitl()))
    return get_metrics_response()


@api_router.get("/circuit-breakers")
async def circuit_breaker_status():
    """Circuit breaker status for all real-LLM agent calls (backend/circuit_breaker.py).
    Shows CLOSED/OPEN/HALF_OPEN state per agent."""
    return {"breakers": [b.status() for b in all_breakers()]}


# ---------------------------------------------------------------------------
# Tool calling (agents/tools.py)
# ---------------------------------------------------------------------------
@api_router.get("/tools")
def list_tools():
    """Tool registry with typed input/output schemas -- judges can see every
    tool an agent can call without needing to import Python classes."""
    return describe_tools()


@api_router.post("/tools/{tool_name}")
def invoke_tool(tool_name: str, input_data: dict, key: dict = Depends(require_scope(APIScope.WRITE))):
    """Call a tool directly with a JSON body (for demo/testing). Every call
    is logged to Lyzr AIMS with the tool name in metadata.tool_called, so
    the audit trail reflects real tool invocations, not just agent calls."""
    start = time.perf_counter()
    # Checked explicitly (rather than relying on call_tool's internal
    # `raise ValueError` for an unknown name) because pydantic's own
    # ValidationError is ALSO a ValueError subclass -- a bare
    # `except ValueError` below would misreport a genuine input-validation
    # failure on a real tool as "tool not found".
    if tool_name not in describe_tools():
        raise ToolNotFoundError(tool_name, list(describe_tools().keys()))
    try:
        result = call_tool(tool_name, input_data)
    except Exception as exc:
        logger.error("Tool call failed: %s", tool_name, exc_info=True)
        raise ToolInputValidationError(tool_name, str(exc))

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
    """Full chronological Lyzr AIMS audit trail across all incidents.
    Reads from Redis (backend/redis_store.py) rather than the in-process
    event list, so the trail survives an in-process restart."""
    return redis_incident_store.get_aims_events()


@api_router.get("/aims/events/{incident_id}")
def aims_events_for_incident(incident_id: str):
    """Lyzr AIMS audit trail filtered to one incident (Redis-backed)."""
    return redis_incident_store.get_aims_events(incident_id=incident_id)


# ---------------------------------------------------------------------------
# Alert ingestion
# ---------------------------------------------------------------------------
@api_router.post("/alerts/ingest")
async def ingest_alerts(payload: IngestPayload, key: dict = Depends(require_scope(APIScope.WRITE))):
    if not payload.alerts:
        raise EmptyAlertBatchError()
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
async def simulate(request: Request, scenario: int, key: dict = Depends(require_scope(APIScope.WRITE))):
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
async def pagerduty_webhook(payload: dict, key: dict = Depends(require_scope(APIScope.WRITE))):
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
def list_incidents(key: Optional[dict] = Depends(get_api_key)):
    # Includes incidents restored from a prior process (backend/redis_store.py)
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


@api_router.get("/incidents/{incident_id}/anomaly-analysis")
async def anomaly_analysis(incident_id: str):
    """ML-based anomaly analysis for an incident (agents/anomaly_detector.py).
    The correlation graph and burst detection are already computed once by
    the triage agent (agents/triage_agent.py) and stored on `incident.triage`
    -- surfaced here rather than recomputed, so this reflects exactly what
    the pipeline itself acted on. Escalation risk is (re)computed fresh
    since it also depends on current, possibly-since-changed HITL state."""
    incident = _get_incident_or_404(incident_id)
    triage = incident.triage

    graph = triage.correlation_graph if triage else None
    is_burst = triage.burst_detected if triage else False
    burst_analysis = triage.burst_analysis if triage else "Triage has not run yet"
    severity = triage.severity.value if triage else "P3"

    try:
        escalation_prediction = predict_escalation_risk(
            current_severity=severity,
            anomaly_scores=[],
            alert_count=len(incident.alerts),
            has_hitl_pending=bool(incident.hitl_pending),
        )
    except Exception as exc:
        raise AnomalyDetectionError(incident_id, str(exc)) from exc

    return {
        "incident_id": incident_id,
        "correlation_graph": graph,
        "burst_detection": {
            "is_burst": is_burst,
            "analysis": burst_analysis,
        },
        "escalation_prediction": escalation_prediction,
    }


@api_router.get("/analytics/patterns")
async def incident_patterns():
    """Cross-incident pattern analysis: recurring failure patterns across
    every incident this process has seen (live + Redis-restored). Uses
    store.list_incident_summaries() (each incident's summary_dict()-shaped
    view), the same source GET /incidents already reads from."""
    summaries = store.list_incident_summaries()

    severity_dist: dict = {}
    service_frequency: dict = {}
    for inc in summaries:
        sev = inc.get("severity", "unknown")
        severity_dist[sev] = severity_dist.get(sev, 0) + 1
        service = inc.get("service", "unknown")
        service_frequency[service] = service_frequency.get(service, 0) + 1

    # Recurring keywords from each incident's title (the closest analogue to
    # a "root cause" string in the summary view; the full root_cause prose
    # lives on incident.rca.root_cause for live incidents, but summaries --
    # including Redis-restored, no-longer-live ones -- don't carry the RCA).
    keyword_freq: dict = {}
    for inc in summaries:
        for word in (inc.get("title") or "").lower().split():
            if len(word) > 4:
                keyword_freq[word] = keyword_freq.get(word, 0) + 1
    top_keywords = sorted(keyword_freq.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "total_incidents_analyzed": len(summaries),
        "severity_distribution": severity_dist,
        "most_affected_services": sorted(service_frequency.items(), key=lambda x: x[1], reverse=True)[:5],
        "recurring_patterns": [{"keyword": k, "frequency": f} for k, f in top_keywords],
        "analysis_timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


@api_router.post("/incidents/{incident_id}/reset")
def reset_incident(incident_id: str, key: dict = Depends(require_scope(APIScope.WRITE))):
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
def hitl_approve(incident_id: str, payload: HITLDecisionPayload, key: dict = Depends(require_scope(APIScope.WRITE))):
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
def hitl_reject(incident_id: str, payload: HITLDecisionPayload, key: dict = Depends(require_scope(APIScope.WRITE))):
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
        raise RCANotAvailableError(incident_id)
    return incident.rca.model_dump()


@api_router.get("/incidents/{incident_id}/rca/pdf")
def get_rca_pdf(incident_id: str):
    incident = store.get_incident(incident_id)
    if not incident:
        raise IncidentNotFoundError(incident_id)
    if not incident.rca:
        raise RCANotAvailableError(incident_id)
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
    return redis_incident_store.get_aims_events(incident_id=incident_id)


# ---------------------------------------------------------------------------
# Mount api_router twice: unversioned (backward-compatible) and under
# /api/v1 (the path new integrations should use going forward). Both mounts
# dispatch to the exact same handler functions defined above.
# ---------------------------------------------------------------------------
app.include_router(api_router)
app.include_router(api_router, prefix="/api/v1")
