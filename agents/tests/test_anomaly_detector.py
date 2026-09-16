"""
agents/tests/test_anomaly_detector.py

Unit tests for the statistical anomaly-detection suite (agents/anomaly_detector.py)
added for Dr Agent's "Problem Complexity" recommendation: z-score anomaly
detection, alert-burst detection, correlation graph, and escalation-risk
prediction.
"""
from datetime import datetime, timedelta, timezone

from agents.anomaly_detector import (
    analyze_incident_metrics,
    correlation_graph,
    detect_alert_burst,
    predict_escalation_risk,
    z_score_anomaly,
)


def test_z_score_detects_spike():
    values = [10, 10, 10, 10, 10, 10, 10, 95]
    score, is_anomaly, z = z_score_anomaly(values)
    assert is_anomaly is True
    assert z > 2.5
    assert score > 0.5


def test_z_score_normal_values():
    values = [10, 11, 10, 12, 10, 11, 10, 11]
    _, is_anomaly, _ = z_score_anomaly(values)
    assert is_anomaly is False


def test_z_score_insufficient_data():
    values = [10, 20]
    _, is_anomaly, _ = z_score_anomaly(values)
    assert is_anomaly is False


def test_z_score_constant_series_is_never_anomalous():
    # A constant series has zero std -- scipy.stats.zscore would divide by
    # zero (all-NaN) here, so this must be handled explicitly rather than
    # propagate a NaN as "anomalous".
    score, is_anomaly, z = z_score_anomaly([5, 5, 5, 5, 5])
    assert is_anomaly is False
    assert score == 0.0
    assert z == 0.0


def test_burst_detection_detects_burst():
    now = datetime.now(timezone.utc)
    times = [(now + timedelta(seconds=i * 10)).isoformat() for i in range(5)]
    is_burst, count, _ = detect_alert_burst(times, window_minutes=5, burst_threshold=3)
    assert is_burst is True
    assert count >= 3


def test_burst_detection_no_burst():
    now = datetime.now(timezone.utc)
    times = [
        now.isoformat(),
        (now + timedelta(hours=1)).isoformat(),
        (now + timedelta(hours=2)).isoformat(),
    ]
    is_burst, _, _ = detect_alert_burst(times, burst_threshold=3)
    assert is_burst is False


def test_burst_detection_empty_timestamps():
    is_burst, count, analysis = detect_alert_burst([])
    assert is_burst is False
    assert count == 0
    assert "No timestamps" in analysis


def test_correlation_graph_finds_root():
    alerts = [
        {"id": "a1", "service": "payment-service", "severity": "P1", "namespace": "production"},
        {"id": "a2", "service": "api-gateway", "severity": "P2", "namespace": "production"},
        {"id": "a3", "service": "order-service", "severity": "P3", "namespace": "production"},
    ]
    graph = correlation_graph(alerts)
    assert "nodes" in graph
    assert "edges" in graph
    assert "root_cause_alert_id" in graph
    assert graph["root_cause_alert_id"] == "a1"
    assert len(graph["nodes"]) == 3
    # 3 nodes, all in the same namespace -> 3 pairwise edges (a1-a2, a1-a3, a2-a3)
    assert len(graph["edges"]) == 3


def test_correlation_graph_empty():
    graph = correlation_graph([])
    assert graph["nodes"] == []
    assert graph["edges"] == []
    assert graph["root_cause_alert_id"] is None


def test_correlation_graph_different_namespaces_uncorrelated():
    alerts = [
        {"id": "a1", "service": "payment-service", "severity": "P1", "namespace": "production"},
        {"id": "a2", "service": "test-service", "severity": "P4", "namespace": "staging"},
    ]
    graph = correlation_graph(alerts)
    assert graph["edges"] == []


def test_escalation_risk_p1_high():
    # P1 + volume + HITL pending alone only reaches ~0.44 (MEDIUM) under the
    # weighting formula -- a real HIGH/CRITICAL reading also needs anomaly
    # signal, which carries the largest single weight (0.4).
    from agents.anomaly_detector import AnomalyScore

    high_anomaly = [AnomalyScore(metric="cpu", score=1.0, is_anomaly=True, z_score=5.0, method="z_score")]
    risk = predict_escalation_risk("P1", high_anomaly, 5, True)
    assert risk["escalation_risk"] > 0.6
    assert risk["risk_level"] in ["HIGH", "CRITICAL"]


def test_escalation_risk_p4_low():
    risk = predict_escalation_risk("P4", [], 1, False)
    assert risk["escalation_risk"] < 0.5
    assert risk["risk_level"] in ["LOW", "MEDIUM"]


def test_escalation_risk_has_factors():
    risk = predict_escalation_risk("P2", [], 3, False)
    assert "factors" in risk
    assert "severity_contribution" in risk["factors"]


def test_analyze_incident_metrics():
    # A single outlier against only 3 baseline points pulls the mean/std
    # toward itself enough that its own z-score can land under the 2.5
    # threshold (the well-known small-sample "masking" effect) -- 7
    # baseline points (matching test_z_score_detects_spike) gives the
    # outlier a clean, unambiguous z-score instead.
    metrics = [{"metric_name": "memory_usage", "current_value": 10} for _ in range(7)]
    metrics.append({"metric_name": "memory_usage", "current_value": 95})
    results = analyze_incident_metrics(metrics)
    assert len(results) == 1
    assert results[0].metric == "memory_usage"
    assert results[0].is_anomaly is True
    assert len(results[0].evidence) == 1


def test_analyze_incident_metrics_multiple_series():
    metrics = [
        {"metric_name": "cpu", "current_value": 50},
        {"metric_name": "cpu", "current_value": 52},
        {"metric_name": "cpu", "current_value": 51},
        {"metric_name": "memory", "current_value": 60},
        {"metric_name": "memory", "current_value": 61},
        {"metric_name": "memory", "current_value": 59},
    ]
    results = analyze_incident_metrics(metrics)
    assert {r.metric for r in results} == {"cpu", "memory"}
    assert all(not r.is_anomaly for r in results)
