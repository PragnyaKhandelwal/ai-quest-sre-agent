"""
agents/triage_agent.py

Agent 1: Triage & Dedup Agent.

Clusters related raw alerts by service/namespace/alert type, deduplicates
by a stable fingerprint hash within the configured dedup window, and
assigns an incident severity P1-P4.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Dict, List, Tuple

from agents import config
from agents.lyzr_client import client
from agents.schemas import Alert, AlertCluster, Severity, TriageResult

AGENT_NAME = "TriageAndDedupAgent"

# fingerprint -> last_seen_timestamp, used for the 5-minute dedup window
_SEEN_FINGERPRINTS: Dict[str, float] = {}


def _fingerprint(service: str, alertname: str, namespace: str) -> str:
    raw = f"{service}:{alertname}:{namespace}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cluster_alerts(alerts: List[Alert]) -> List[AlertCluster]:
    buckets: Dict[Tuple[str, str, str], List[Alert]] = {}
    for a in alerts:
        key = (a.service, a.namespace, a.alertname)
        buckets.setdefault(key, []).append(a)

    clusters = []
    for (service, namespace, alertname), grouped in buckets.items():
        fp = _fingerprint(service, alertname, namespace)
        clusters.append(
            AlertCluster(
                fingerprint=fp,
                alerts=grouped,
                service=service,
                namespace=namespace,
            )
        )
    return clusters


def _is_duplicate(fingerprint: str) -> bool:
    now = time.time()
    last_seen = _SEEN_FINGERPRINTS.get(fingerprint)
    is_dup = last_seen is not None and (now - last_seen) < config.DEDUP_WINDOW_SECONDS
    _SEEN_FINGERPRINTS[fingerprint] = now
    return is_dup


_SEVERITY_ORDER = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}


def _rule_based_severity(alerts: List[Alert]) -> Severity:
    """Deterministic, explainable severity assignment grounded in
    config.SEVERITY_RULES -- used both as the local-simulation path and as
    a sanity check against whatever Lyzr returns. Scans the FULL alert
    batch (an incident is often reported by several differently-named
    alerts firing together, e.g. a query-timeout alert + a lock-timeout
    alert + a connection-pool-exhaustion alert all describing one deadlock)
    and takes the single most severe keyword match found anywhere in it."""
    text = " ".join(
        f"{a.title} {a.description} {a.alertname}".lower() for a in alerts
    )
    matches = [Severity(sev) for kw, sev in config.SEVERITY_RULES.items() if kw in text]
    if matches:
        return min(matches, key=lambda s: _SEVERITY_ORDER[s.value])
    # No keyword matched: fall back to alert volume as a severity proxy.
    if len(alerts) >= 3:
        return Severity.P2
    return Severity.P3


def _simulate(primary: AlertCluster, alerts: List[Alert], incident_id: str, is_dup: bool) -> Dict:
    severity = _rule_based_severity(alerts)
    services = sorted({a.service for a in alerts})
    return {
        "incident_id": incident_id,
        "cluster_id": primary.cluster_id,
        "fingerprint": primary.fingerprint,
        "severity": severity.value,
        "affected_services": services,
        "summary": (
            f"{len(alerts)} alert(s) across {len(services)} service(s) "
            f"({', '.join(services)}) in namespace '{primary.namespace}' "
            f"-- leading signal: {alerts[0].alertname}."
        ),
        "alert_count": len(alerts),
        "is_duplicate": is_dup,
        "reasoning": (
            f"Clustered by service+namespace+alertname; dedup fingerprint "
            f"{primary.fingerprint} taken from the largest sub-cluster. "
            f"Severity is the most severe keyword match across all alerts "
            f"in the batch, per SEVERITY_RULES."
        ),
    }


def run_triage(alerts: List[Alert], incident_id: str) -> Tuple[TriageResult, int, float]:
    """
    Returns (TriageResult, tokens_used, latency_ms). Falls back to a safe
    deterministic P3 result on any unexpected failure so the pipeline never
    crashes on malformed agent output.
    """
    try:
        clusters = _cluster_alerts(alerts)
        # The dedup fingerprint/cluster_id identity comes from the largest
        # sub-cluster, but severity/services/summary are derived from the
        # FULL alert batch -- a single ingest/simulate call is treated as
        # one incident even when its alerts span several alert types.
        primary = max(clusters, key=lambda c: len(c.alerts))
        is_dup = _is_duplicate(primary.fingerprint)

        user_message = json.dumps(
            {
                "incident_id": incident_id,
                "alerts": [a.model_dump() for a in alerts],
                "primary_cluster": primary.model_dump(),
                "dedup_window_seconds": config.DEDUP_WINDOW_SECONDS,
            },
            default=str,
        )

        result = client.run_inference(
            agent_key="triage",
            agent_name=AGENT_NAME,
            system_prompt=config.TRIAGE_SYSTEM_PROMPT,
            user_message=user_message,
            session_id=incident_id,
            simulate_fn=lambda: _simulate(primary, alerts, incident_id, is_dup),
        )

        try:
            payload = json.loads(result.text)
            triage = TriageResult(**payload)
        except Exception:
            # Lyzr returned something that doesn't validate -- fall back to
            # the deterministic local result rather than propagate garbage.
            triage = TriageResult(**_simulate(primary, alerts, incident_id, is_dup))

        return triage, result.tokens_used, result.latency_ms

    except Exception as exc:  # pragma: no cover - last-resort safety net
        fallback = TriageResult(
            incident_id=incident_id,
            cluster_id="unknown",
            fingerprint="unknown",
            severity=Severity.P3,
            affected_services=[a.service for a in alerts] or ["unknown"],
            summary=f"Triage agent failed ({exc}); defaulted to P3 for manual review.",
            alert_count=len(alerts),
            is_duplicate=False,
            reasoning="Fallback path triggered due to internal error.",
        )
        return fallback, 0, 0.0
