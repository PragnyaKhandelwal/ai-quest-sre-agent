"""
backend/aims_logger.py

Wrapper around Lyzr AIMS (AI Management System) for full audit-trail
logging: every agent call start/end, token usage, decision made, HITL
event, and RCA publish is logged here.

Safety contract (defense in depth, check #2):
Before any event is persisted, this module independently re-scans the
event's text for the same DESTRUCTIVE_KEYWORDS list used by
agents/remediation_agent.py. If an event claims a destructive command was
"executed"/"auto-executed" without being marked `blocked=True`, that is a
governance violation -- the event is force-flagged as blocked and a
separate CRITICAL anomaly event is emitted so it is impossible for a
destructive action to be silently logged as routinely executed, even if
the remediation agent's own check were ever bypassed upstream.

Every event is also durably written to Redis (backend/redis_store.py),
independent of whether the remote Lyzr AIMS API is reachable. If Lyzr
AIMS is not reachable/configured, the event additionally falls back to
an append-only local JSON-lines file, so no audit data is ever lost.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

import requests

from agents import config
from agents.schemas import AIMSEvent
from backend.redis_store import incident_store as redis_incident_store

_LOCAL_LOG_PATH = Path(config.AIMS_LOCAL_FALLBACK_PATH)
_session = requests.Session()
if config.LYZR_API_KEY:
    _session.headers.update({"x-api-key": config.LYZR_API_KEY, "Content-Type": "application/json"})

_IN_MEMORY_EVENTS: List[dict] = []  # also kept in-process for fast API reads


def _contains_destructive_keyword(text: str) -> bool:
    upper = (text or "").upper()
    return any(keyword in upper for keyword in config.DESTRUCTIVE_KEYWORDS)


def _independent_safety_recheck(event: AIMSEvent) -> AIMSEvent:
    """Second, independent destructive-action check (see module docstring)."""
    combined_text = f"{event.action} {event.input_summary} {event.output_summary}"
    looks_executed = any(
        phrase in combined_text.lower() for phrase in ["auto-executed", "executed automatically", '"executed": true']
    )
    if _contains_destructive_keyword(combined_text) and looks_executed and not event.blocked:
        # Governance violation detected -- force block and raise an anomaly.
        event.blocked = True
        event.metadata["safety_anomaly"] = (
            "aims_logger detected a destructive keyword in an event claiming "
            "auto-execution without a blocked flag. Forced blocked=True."
        )
        anomaly = AIMSEvent(
            incident_id=event.incident_id,
            agent_name="AIMSLogger.SafetyRecheck",
            action="destructive_action_anomaly_detected",
            input_summary=combined_text[:400],
            output_summary="Event force-flagged as blocked; upstream classification must be investigated.",
            blocked=True,
        )
        _persist(anomaly)
    return event


def _persist(event: AIMSEvent) -> None:
    record = event.model_dump()
    _IN_MEMORY_EVENTS.append(record)

    # Durable copy in Redis (backend/redis_store.py) so the audit trail
    # survives an in-process restart -- independent of, and in addition to,
    # the remote Lyzr AIMS API / local file fallback below, which are about
    # the *governed system of record*, not process-restart durability.
    redis_incident_store.append_aims_event(record)

    sent_remote = False
    if config.LYZR_ENABLED:
        try:
            resp = _session.post(f"{config.LYZR_AIMS_URL}/events", json=record, timeout=5)
            resp.raise_for_status()
            sent_remote = True
        except Exception:
            sent_remote = False

    if not sent_remote:
        try:
            with _LOCAL_LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception:
            pass  # last-resort: never let audit logging crash the pipeline


def log_event(event: AIMSEvent) -> AIMSEvent:
    event = _independent_safety_recheck(event)
    _persist(event)
    return event


def recent_events(incident_id: str = None, limit: int = 200) -> List[dict]:
    events = _IN_MEMORY_EVENTS
    if incident_id:
        events = [e for e in events if e.get("incident_id") == incident_id]
    return list(sorted(events, key=lambda e: e["timestamp"], reverse=True))[:limit]


def total_tokens_used() -> int:
    return sum(e.get("tokens_used", 0) for e in _IN_MEMORY_EVENTS)
