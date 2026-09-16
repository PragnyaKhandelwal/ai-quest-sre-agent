"""
ML-based Anomaly Detection for Alert Correlation
=================================================
Statistical methods for detecting anomalous alert patterns that simple
threshold-based rules miss.

Techniques:
  1. Z-score anomaly detection on metric time series (numpy/scipy.stats)
  2. Temporal pattern detection (burst alerts in a time window)
  3. Alert correlation graph (finds root-cause vs. symptom alerts)
  4. Escalation-risk scoring (logistic-style weighted combination of
     severity, anomaly intensity, alert volume, and HITL backlog)

This module has zero dependency on backend/ (same rule as agents/pipeline.py):
it is pure signal-processing over plain dicts/lists, independently testable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from scipy import stats

logger = logging.getLogger(__name__)


@dataclass
class AnomalyScore:
    """Result of anomaly detection for one metric/alert stream."""

    metric: str
    score: float  # 0.0 (normal) -> 1.0 (highly anomalous)
    is_anomaly: bool
    z_score: float
    method: str
    evidence: list[str] = field(default_factory=list)


def z_score_anomaly(values: list[float], threshold: float = 2.5) -> tuple[float, bool, float]:
    """
    Z-score anomaly detection on the most recent value in a series, via
    scipy.stats.zscore (population std, ddof=0 -- matches the manual
    mean/variance formula this replaces).
    Returns (normalized_score, is_anomaly, raw_z_score).
    """
    if len(values) < 3:
        return 0.0, False, 0.0

    arr = np.asarray(values, dtype=float)
    std = float(np.std(arr))
    if std == 0:
        # scipy.stats.zscore divides by std and returns all-NaN for a
        # constant series -- treat a constant history as never anomalous.
        return 0.0, False, 0.0

    z_all = stats.zscore(arr)
    z = abs(float(z_all[-1]))

    score = min(1.0, z / (threshold * 2))
    is_anomaly = z > threshold

    return score, is_anomaly, z


def detect_alert_burst(
    timestamps: list[str], window_minutes: int = 5, burst_threshold: int = 3
) -> tuple[bool, int, str]:
    """
    Detect alert bursts -- many alerts in a short time window.
    Burst = more than burst_threshold alerts within window_minutes of
    each other. Returns (is_burst, count_in_window, analysis).
    """
    if not timestamps:
        return False, 0, "No timestamps provided"

    try:
        times = sorted(datetime.fromisoformat(t.replace("Z", "+00:00")) for t in timestamps)
        window_seconds = window_minutes * 60

        # Vectorized pairwise time deltas (seconds) via numpy, rather than
        # a manual O(n^2) Python double loop.
        epochs = np.array([t.timestamp() for t in times])
        deltas = np.abs(epochs[:, None] - epochs[None, :])
        max_in_window = int(np.max(np.sum(deltas <= window_seconds, axis=1)))

        is_burst = max_in_window >= burst_threshold
        analysis = (
            f"Burst detected: {max_in_window} alerts in {window_minutes}min window"
            if is_burst
            else f"Normal rate: {max_in_window} alerts in {window_minutes}min window"
        )
        return is_burst, max_in_window, analysis
    except Exception as e:
        logger.warning(f"Burst detection failed: {e}")
        return False, 0, f"Detection failed: {e}"


def correlation_graph(alerts: list[dict]) -> dict:
    """
    Build an alert correlation graph to identify root cause vs. symptoms.

    Algorithm:
    1. Group alerts by service and namespace
    2. Score each alert by severity
    3. Alert with the highest score = most likely root cause
    4. Others in the same namespace = correlated (likely symptoms/cascades)

    Returns a graph with nodes (alerts) and edges (correlations).
    """
    if not alerts:
        return {"nodes": [], "edges": [], "root_cause_alert_id": None}

    nodes = []
    edges = []
    severity_scores = {"P1": 4, "P2": 3, "P3": 2, "P4": 1}

    for alert in alerts:
        service = alert.get("service", "unknown")
        severity = alert.get("severity", "P3")
        score = severity_scores.get(severity, 1)

        nodes.append(
            {
                "id": alert.get("id", service),
                "service": service,
                "severity": severity,
                "score": score,
                "namespace": alert.get("namespace", "production"),
            }
        )

    # Find correlations (same namespace)
    for i, n1 in enumerate(nodes):
        for j, n2 in enumerate(nodes):
            if i >= j:
                continue
            if n1["namespace"] == n2["namespace"]:
                edges.append(
                    {
                        "source": n1["id"],
                        "target": n2["id"],
                        "relationship": "correlated",
                        "reason": f"Same namespace: {n1['namespace']}",
                    }
                )

    root = max(nodes, key=lambda x: x["score"]) if nodes else None

    logger.info(f"Correlation graph: {len(nodes)} nodes, {len(edges)} edges, root={root['id'] if root else 'none'}")

    return {
        "nodes": nodes,
        "edges": edges,
        "root_cause_alert_id": root["id"] if root else None,
        "root_cause_service": root["service"] if root else None,
        "confidence": min(1.0, 0.5 + (len(edges) * 0.1)),
    }


def analyze_incident_metrics(metrics_history: list[dict]) -> list[AnomalyScore]:
    """Run the full anomaly detection suite on incident metrics. Returns
    one AnomalyScore per distinct metric name in the history."""
    results = []

    metric_series: dict[str, list[float]] = {}
    for reading in metrics_history:
        metric = reading.get("metric_name", "unknown")
        value = reading.get("current_value", 0)
        metric_series.setdefault(metric, []).append(value)

    for metric, values in metric_series.items():
        score, is_anomaly, z = z_score_anomaly(values)
        evidence = []
        if is_anomaly:
            mean = float(np.mean(values))
            evidence.append(
                f"Z-score {z:.2f} exceeds threshold 2.5 -- latest value {values[-1]:.1f} vs mean {mean:.1f}"
            )
        results.append(
            AnomalyScore(metric=metric, score=score, is_anomaly=is_anomaly, z_score=z, method="z_score", evidence=evidence)
        )

    anomaly_count = sum(1 for r in results if r.is_anomaly)
    logger.info(f"Anomaly detection: {anomaly_count}/{len(results)} metrics anomalous")
    return results


def predict_escalation_risk(
    current_severity: str,
    anomaly_scores: list[AnomalyScore],
    alert_count: int,
    has_hitl_pending: bool,
) -> dict:
    """Predict the probability of an incident escalating to a higher
    severity -- a simple logistic-style weighted sum of key risk factors."""
    severity_base = {"P1": 0.8, "P2": 0.5, "P3": 0.3, "P4": 0.1}
    severity_contribution = severity_base.get(current_severity, 0.3) * 0.3

    avg_anomaly = float(np.mean([a.score for a in anomaly_scores])) if anomaly_scores else 0.0
    anomaly_contribution = avg_anomaly * 0.4

    volume_contribution = min(1.0, alert_count / 10) * 0.2
    hitl_contribution = 0.1 if has_hitl_pending else 0.0

    risk_score = min(1.0, severity_contribution + anomaly_contribution + volume_contribution + hitl_contribution)

    return {
        "escalation_risk": round(risk_score, 3),
        "risk_level": (
            "CRITICAL" if risk_score > 0.8 else "HIGH" if risk_score > 0.6 else "MEDIUM" if risk_score > 0.4 else "LOW"
        ),
        "factors": {
            "severity_contribution": round(severity_contribution, 3),
            "anomaly_contribution": round(anomaly_contribution, 3),
            "volume_contribution": round(volume_contribution, 3),
            "hitl_contribution": hitl_contribution,
        },
    }
