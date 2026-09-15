"""
backend/tests/test_integration.py

Integration Tests -- Full End-to-End Pipeline
==============================================
Tests the complete incident lifecycle from alert ingestion through the
agent pipeline to RCA generation -- multiple components working together
(FastAPI routes, backend/store.py, agents/pipeline.py, the 4-agent Lyzr
pipeline, Lyzr AIMS logging), not a single unit in isolation.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture(scope="module")
def client():
    # `with` is required, not just `TestClient(app)`: FastAPI's startup
    # event only fires inside the context manager, and the fire-and-forget
    # asyncio.create_task() pipeline launched by /simulate needs the
    # context-managed event loop to actually run to completion between
    # requests in this test client.
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


class TestFullIncidentLifecycle:
    """Test the complete incident response lifecycle."""

    def test_p1_memory_leak_full_lifecycle(self, client: TestClient):
        """
        Integration test: P1 memory leak from alert to resolved RCA.
        Tests: ingest -> triage -> diagnose -> remediate -> resolve -> RCA
        """
        r = client.post("/simulate/1")
        assert r.status_code == 200
        incident_id = r.json()["incident_id"]
        assert incident_id.startswith("inc_")

        r = client.get(f"/incidents/{incident_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["incident_id"] == incident_id
        assert data["status"] in [
            "TRIAGING", "DIAGNOSING", "REMEDIATING",
            "AWAITING_HITL", "POST_MORTEM", "RESOLVED",
        ]

        detail = _wait_for_status(client, incident_id, {"RESOLVED", "ERROR"})
        assert detail["status"] == "RESOLVED"

        r = client.get(f"/incidents/{incident_id}/metrics")
        assert r.status_code == 200
        metrics = r.json()
        assert len(metrics) >= 1

        r = client.get(f"/incidents/{incident_id}/rca")
        assert r.status_code == 200
        rca = r.json()
        assert "root_cause" in rca
        assert "prevention_recommendations" in rca

    def test_p1_db_deadlock_hitl_lifecycle(self, client: TestClient):
        """
        Integration test: P1 DB deadlock requiring HITL approval.
        Tests: ingest -> triage -> diagnose -> HITL gate -> approve -> resolve
        """
        r = client.post("/simulate/3")
        assert r.status_code == 200
        incident_id = r.json()["incident_id"]

        detail = _wait_for_status(client, incident_id, {"AWAITING_HITL", "RESOLVED", "ERROR"})
        assert detail["status"] == "AWAITING_HITL"

        pending = client.get("/hitl/pending").json()
        matching = [p for p in pending if p["incident_id"] == incident_id]
        assert len(matching) >= 1
        request_id = matching[0]["request"]["request_id"]

        r = client.post(f"/hitl/{incident_id}/approve", json={"request_id": request_id})
        assert r.status_code == 200

        final = _wait_for_status(client, incident_id, {"RESOLVED", "ERROR"})
        assert final["status"] == "RESOLVED"

    def test_multiple_concurrent_incidents(self, client: TestClient):
        """
        Integration test: multiple incidents can run concurrently without
        interfering with each other's state.
        """
        ids = []
        for scenario in [1, 2, 5]:
            r = client.post(f"/simulate/{scenario}")
            assert r.status_code == 200
            ids.append(r.json()["incident_id"])

        assert len(set(ids)) == 3

        for inc_id in ids:
            r = client.get(f"/incidents/{inc_id}")
            assert r.status_code == 200

    def test_pagerduty_webhook_creates_incident(self, client: TestClient):
        """
        Integration test: PagerDuty webhook triggers the same governed
        pipeline as /simulate and /alerts/ingest.
        """
        payload = {
            "messages": [{
                "id": "pd-integration-test-001",
                "service": {"name": "integration-test-service"},
                "urgency": "high",
                "summary": "Integration test alert",
                "body": {"details": "This is an integration test"},
                "created_at": "2026-09-14T10:00:00Z",
                "details": {},
            }]
        }
        r = client.post("/alerts/webhook", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert "incidents_created" in data
        assert len(data["incidents_created"]) == 1

    def test_aims_log_populated_after_pipeline(self, client: TestClient):
        """
        Integration test: the Lyzr AIMS audit trail contains events for an
        incident after its pipeline has run.
        """
        r = client.post("/simulate/1")
        incident_id = r.json()["incident_id"]
        _wait_for_status(client, incident_id, {"RESOLVED", "ERROR"})

        r = client.get(f"/aims/events/{incident_id}")
        assert r.status_code == 200
        events = r.json()
        assert isinstance(events, list)
        assert len(events) > 0

    def test_lyzr_architecture_complete(self, client: TestClient):
        """
        Integration test: all 3 Lyzr tiers (Environment/Agent/Inference)
        are properly configured and report all 4 governed agents.
        """
        r = client.get("/lyzr/status")
        assert r.status_code == 200
        data = r.json()

        assert "layer_1_environment" in data
        assert "layer_2_agents" in data
        assert "layer_3_inference" in data

        agents = data["layer_2_agents"]
        assert len(agents) == 4
        for agent in ("triage", "diagnostic", "remediation", "postmortem"):
            assert agent in agents

    def test_secret_manager_health(self, client: TestClient):
        """
        Integration test: the secret manager (backend/secrets.py) is
        operational and its health surfaces through /system/info.
        """
        r = client.get("/system/info")
        assert r.status_code == 200
        data = r.json()
        assert "secret_management" in data
        sm = data["secret_management"]
        assert "provider" in sm
        assert sm["provider"] in [
            "environment_variables",
            "hashicorp_vault",
            "aws_secrets_manager",
            "gcp_secret_manager",
        ]

    def test_all_6_scenarios_produce_incidents(self, client: TestClient):
        """
        Integration test: all 6 mock scenarios trigger successfully end to
        end through the real FastAPI route + pipeline stack.
        """
        for scenario in range(1, 7):
            r = client.post(f"/simulate/{scenario}")
            assert r.status_code == 200, f"Scenario {scenario} failed"
            data = r.json()
            assert "incident_id" in data, f"No incident_id for scenario {scenario}"

    def test_versioned_api_v1_routes_mirror_root_routes(self, client: TestClient):
        """
        Integration test: /api/v1/* routes (Dr Agent: "add API versioning")
        dispatch to the exact same handlers as their unversioned aliases.
        """
        root_resp = client.get("/health")
        v1_resp = client.get("/api/v1/health")
        assert root_resp.status_code == v1_resp.status_code == 200
        assert root_resp.json() == v1_resp.json()

    def test_root_endpoint_reports_api_versions(self, client: TestClient):
        r = client.get("/")
        assert r.status_code == 200
        data = r.json()
        assert data["api_versions"] == ["v1"]
        assert data["version"] == app.version
