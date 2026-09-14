"""
agents/tests/test_triage_agent.py

Unit tests for Agent 1 -- Triage & Dedup (agents/triage_agent.py).

Runs entirely in local-simulation mode (no LYZR_API_KEY needed): the
triage agent's `_simulate` fallback is deterministic and rule-based, so
these assertions exercise real production logic, not a mock.
"""
from __future__ import annotations

import pytest

from agents import triage_agent
from agents.schemas import Alert, Severity


@pytest.fixture(autouse=True)
def _reset_dedup_cache():
    """Each test gets a clean dedup fingerprint cache so state from one
    test never leaks into another (the cache is module-level, shared
    across calls to mimic a real long-running process)."""
    triage_agent._SEEN_FINGERPRINTS.clear()
    yield
    triage_agent._SEEN_FINGERPRINTS.clear()


def _memory_leak_alerts() -> list[Alert]:
    """Mirrors the canonical Scenario 1 (payment-service memory leak, P1)."""
    return [
        Alert(
            source="prometheus",
            service="payment-service",
            namespace="production",
            alertname="PodOOMKilled",
            title="payment-service pod OOMKilled",
            description="Container payment-service was OOMKilled after exceeding memory limit.",
        ),
        Alert(
            source="prometheus",
            service="payment-service",
            namespace="production",
            alertname="PodOOMKilled",
            title="payment-service pod OOMKilled again",
            description="Another OOMKilled event for payment-service.",
        ),
    ]


def _bad_deploy_alerts() -> list[Alert]:
    """Mirrors the canonical Scenario 2 (api-gateway bad deploy, P2)."""
    return [
        Alert(
            source="prometheus",
            service="api-gateway",
            namespace="production",
            alertname="HighErrorRate",
            title="api-gateway error rate spike to 45%",
            description="5xx error rate for api-gateway jumped after deployment of v2.3.1.",
        )
    ]


def test_p1_severity_assigned_for_memory_leak_scenario():
    triage, tokens, latency = triage_agent.run_triage(_memory_leak_alerts(), "inc_test_memleak")
    assert triage.severity == Severity.P1
    assert "payment-service" in triage.affected_services
    assert triage.alert_count == 2
    assert tokens >= 0
    assert latency >= 0


def test_p2_severity_assigned_for_bad_deploy_scenario():
    triage, tokens, latency = triage_agent.run_triage(_bad_deploy_alerts(), "inc_test_baddeploy")
    assert triage.severity == Severity.P2
    assert "api-gateway" in triage.affected_services


def test_deduplication_same_fingerprint_twice():
    """Firing the same service+namespace+alertname twice within the dedup
    window should be recognized as a duplicate on the second occurrence."""
    alerts = _memory_leak_alerts()

    first, _, _ = triage_agent.run_triage(alerts, "inc_test_dedup_a")
    assert first.is_duplicate is False

    second, _, _ = triage_agent.run_triage(alerts, "inc_test_dedup_b")
    assert second.is_duplicate is True
    assert second.fingerprint == first.fingerprint


def test_triage_different_services_different_clusters():
    """Two distinct services firing the same alertname must get different
    fingerprints/cluster ids and neither should be flagged a duplicate of
    the other -- dedup is scoped per (service, namespace, alertname)."""
    memleak, _, _ = triage_agent.run_triage(_memory_leak_alerts(), "inc_test_svc_a")
    baddeploy, _, _ = triage_agent.run_triage(_bad_deploy_alerts(), "inc_test_svc_b")

    assert memleak.fingerprint != baddeploy.fingerprint
    assert memleak.cluster_id != baddeploy.cluster_id
    assert memleak.is_duplicate is False
    assert baddeploy.is_duplicate is False


def test_cluster_grouping_by_service_name():
    alerts = [
        Alert(
            source="prometheus", service="payment-service", namespace="production",
            alertname="PodOOMKilled", title="a", description="a",
        ),
        Alert(
            source="prometheus", service="payment-service", namespace="production",
            alertname="PodOOMKilled", title="b", description="b",
        ),
        Alert(
            source="prometheus", service="api-gateway", namespace="production",
            alertname="HighErrorRate", title="c", description="c",
        ),
    ]

    clusters = triage_agent._cluster_alerts(alerts)

    # Two distinct clusters: one per (service, namespace, alertname) key.
    assert len(clusters) == 2
    services = {c.service for c in clusters}
    assert services == {"payment-service", "api-gateway"}

    payment_cluster = next(c for c in clusters if c.service == "payment-service")
    assert len(payment_cluster.alerts) == 2

    gateway_cluster = next(c for c in clusters if c.service == "api-gateway")
    assert len(gateway_cluster.alerts) == 1
