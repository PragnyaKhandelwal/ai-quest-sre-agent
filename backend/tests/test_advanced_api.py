"""
backend/tests/test_advanced_api.py

HTTP-level tests for the newer backend routes (Lyzr Automata metadata, tool
calling, Lyzr AIMS audit trail, PagerDuty webhook, metrics) not already
covered by backend/tests/test_api.py.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _wait_for_status(client: TestClient, incident_id: str, target_statuses: set[str], timeout: float = 10.0):
    deadline = time.time() + timeout
    detail = None
    while time.time() < deadline:
        resp = client.get(f"/incidents/{incident_id}")
        assert resp.status_code == 200
        detail = resp.json()
        if detail["status"] in target_statuses:
            return detail
        time.sleep(0.1)
    return detail


def test_webhook_accepts_pagerduty_format(client: TestClient):
    payload = {
        "id": "PDALERT_TEST_1",
        "service": {"name": "checkout-service"},
        "summary": "Checkout service 5xx spike",
        "body": {"details": "Error rate exceeded 40% for checkout-service"},
        "details": {"region": "us-east-1"},
    }
    resp = client.post("/alerts/webhook", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert "incidents_created" in body
    assert len(body["incidents_created"]) == 1
    assert body["incidents_created"][0].startswith("inc_")


def test_tools_endpoint_returns_tool_registry(client: TestClient):
    resp = client.get("/tools")
    assert resp.status_code == 200
    body = resp.json()
    for tool_name in ("query_logs", "query_metrics", "kubectl_safe", "correlate_alerts"):
        assert tool_name in body
        assert "description" in body[tool_name]
        assert "input_schema" in body[tool_name]
        assert "output_schema" in body[tool_name]


def test_lyzr_status_returns_architecture_info(client: TestClient):
    resp = client.get("/lyzr/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["lyzr_architecture"] == "Environment · Agent · Inference"
    assert "layer_1_environment" in body
    assert "layer_2_agents" in body
    assert "layer_3_inference" in body
    for agent_name in ("triage", "diagnostic", "remediation", "postmortem"):
        assert agent_name in body["layer_2_agents"]


def test_aims_events_returns_list(client: TestClient):
    # Generate at least one event first so the trail isn't trivially empty.
    client.post("/simulate/1")
    resp = client.get("/aims/events")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)


def test_metrics_returns_session_summary(client: TestClient):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("total_tokens", "total_cost_usd", "total_calls", "avg_latency_ms", "cost_formatted"):
        assert key in body


def test_simulate_5_cpu_throttling_works(client: TestClient):
    resp = client.post("/simulate/5")
    assert resp.status_code == 200
    body = resp.json()
    incident_id = body["incident_id"]
    assert "CPU Throttling" in body["scenario"]

    detail = _wait_for_status(client, incident_id, {"RESOLVED", "ERROR"})
    assert detail["status"] == "RESOLVED"
    assert detail["triage"]["severity"] == "P2"


def test_simulate_6_certificate_expiry_works(client: TestClient):
    resp = client.post("/simulate/6")
    assert resp.status_code == 200
    body = resp.json()
    incident_id = body["incident_id"]
    assert "Certificate Expiry" in body["scenario"]

    detail = _wait_for_status(client, incident_id, {"RESOLVED", "ERROR"})
    assert detail["status"] == "RESOLVED"
    assert detail["triage"]["severity"] == "P3"


def test_incident_metrics_returns_per_agent_data(client: TestClient):
    created = client.post("/simulate/2").json()
    incident_id = created["incident_id"]
    _wait_for_status(client, incident_id, {"RESOLVED", "ERROR"})

    resp = client.get(f"/incidents/{incident_id}/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 4
    # Note: agents/metrics_tracker.py's per-call records are keyed by the
    # Lyzr Agent Studio SREAgent's own `.name` (agents/lyzr_agents.py,
    # e.g. "SRE-Triage-Agent"), which is a *different* naming convention
    # than the pipeline-level AGENT_NAME constants used for
    # AgentTraceStep/AIMSEvent labeling in agents/pipeline.py (e.g.
    # "TriageAndDedupAgent") -- both refer to the same underlying agent.
    agent_names = {m["agent"] for m in body}
    assert agent_names == {
        "SRE-Triage-Agent", "SRE-Diagnostic-Agent",
        "SRE-Remediation-Agent", "SRE-PostMortem-Agent",
    }
    for m in body:
        assert m["total_tokens"] > 0
        assert m["latency_ms"] >= 0


# ---------------------------------------------------------------------------
# Block 3/4: persistence-aware list/detail views + new judge-facing routes
# ---------------------------------------------------------------------------
def test_list_incidents_uses_persistence_aware_summaries(client: TestClient):
    created = client.post("/simulate/1").json()
    incident_id = created["incident_id"]
    resp = client.get("/incidents")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert any(i["incident_id"] == incident_id for i in body)


def test_get_incident_view_matches_live_incident(client: TestClient):
    created = client.post("/simulate/1").json()
    incident_id = created["incident_id"]
    resp = client.get(f"/incidents/{incident_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["incident_id"] == incident_id


def test_get_incident_view_404_for_unknown_id(client: TestClient):
    resp = client.get("/incidents/inc_does_not_exist")
    assert resp.status_code == 404


def test_incident_timeline_falls_back_to_trace_or_rca(client: TestClient):
    created = client.post("/simulate/1").json()
    incident_id = created["incident_id"]
    _wait_for_status(client, incident_id, {"RESOLVED", "ERROR"})

    resp = client.get(f"/incidents/{incident_id}/timeline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["incident_id"] == incident_id
    assert isinstance(body["timeline"], list)
    assert body["total_events"] == len(body["timeline"])
    assert body["total_events"] > 0


def test_incident_timeline_404_for_unknown_id(client: TestClient):
    resp = client.get("/incidents/inc_does_not_exist/timeline")
    assert resp.status_code == 404


def test_incident_evidence_returns_diagnosis_grounding(client: TestClient):
    created = client.post("/simulate/1").json()
    incident_id = created["incident_id"]
    _wait_for_status(client, incident_id, {"RESOLVED", "ERROR"})

    resp = client.get(f"/incidents/{incident_id}/evidence")
    assert resp.status_code == 200
    body = resp.json()
    assert body["incident_id"] == incident_id
    assert 0 <= body["confidence"] <= 1
    assert isinstance(body["evidence"], list)
    assert isinstance(body["retrieval_scores"], list)


def test_incident_evidence_404_for_unknown_id(client: TestClient):
    resp = client.get("/incidents/inc_does_not_exist/evidence")
    assert resp.status_code == 404


def test_reset_incident_clears_status_and_hitl(client: TestClient):
    created = client.post("/simulate/1").json()
    incident_id = created["incident_id"]
    _wait_for_status(client, incident_id, {"RESOLVED", "ERROR"})

    resp = client.post(f"/incidents/{incident_id}/reset")
    assert resp.status_code == 200
    body = resp.json()
    assert body["incident_id"] == incident_id
    assert body["status"] == "reset"

    detail = client.get(f"/incidents/{incident_id}").json()
    assert detail["status"] == "TRIAGING"
    assert detail["pending_hitl"] == []


def test_reset_incident_404_for_unknown_id(client: TestClient):
    resp = client.post("/incidents/inc_does_not_exist/reset")
    assert resp.status_code == 404


def test_system_info_returns_snapshot(client: TestClient):
    resp = client.get("/system/info")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == "2.0.0"
    assert body["uptime_seconds"] >= 0
    assert "agents" in body
    assert len(body["agents"]) == 4
    assert isinstance(body["routes"], list)
    assert any("/incidents" in r for r in body["routes"])


def test_security_headers_present_on_every_response(client: TestClient):
    resp = client.get("/health")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["x-powered-by"] == "Lyzr-Automata-SRE-Mesh"
    assert "x-agent-pipeline" in resp.headers
