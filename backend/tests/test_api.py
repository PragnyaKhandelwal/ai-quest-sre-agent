"""
backend/tests/test_api.py

HTTP-level tests for the FastAPI backend (backend/main.py). Runs the real
in-memory pipeline end-to-end (no LYZR_API_KEY needed in CI -- every agent
runs in local-simulation mode) via Starlette's TestClient.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture(scope="module")
def client():
    # Using the app as a context manager keeps a single persistent event
    # loop alive for the whole test module, so the fire-and-forget
    # `asyncio.create_task(...)` pipeline launched by /simulate and
    # /alerts/ingest actually gets to run to completion between requests.
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


def test_health_returns_200(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "confidence_threshold" in body


def test_simulate_creates_incident_and_returns_incident_id(client: TestClient):
    resp = client.post("/simulate/1")
    assert resp.status_code == 200
    body = resp.json()
    assert "incident_id" in body
    assert body["incident_id"].startswith("inc_")
    assert body["status"] == "pipeline_started"


def test_simulate_invalid_scenario_returns_422(client: TestClient):
    resp = client.post("/simulate/99")
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "INVALID_SCENARIO"
    assert body["detail"]["provided"] == 99


def test_get_incident_returns_correct_structure(client: TestClient):
    created = client.post("/simulate/1").json()
    incident_id = created["incident_id"]

    detail = _wait_for_status(client, incident_id, {"RESOLVED", "ERROR"})
    assert detail is not None
    for key in ("incident_id", "status", "alerts", "triage", "diagnosis", "runbook", "rca", "trace"):
        assert key in detail
    assert detail["incident_id"] == incident_id
    assert detail["status"] == "RESOLVED"
    assert detail["triage"]["severity"] == "P1"


def test_get_incident_missing_returns_404(client: TestClient):
    resp = client.get("/incidents/inc_does_not_exist")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"] == "INCIDENT_NOT_FOUND"
    assert body["detail"]["incident_id"] == "inc_does_not_exist"


def test_hitl_approve_changes_status(client: TestClient):
    # Scenario 3 (DB deadlock) always proposes a destructive action, which
    # must be blocked behind the HITL gate before the pipeline can resolve.
    created = client.post("/simulate/3").json()
    incident_id = created["incident_id"]

    detail = _wait_for_status(client, incident_id, {"AWAITING_HITL", "RESOLVED", "ERROR"})
    assert detail["status"] == "AWAITING_HITL"

    pending = client.get("/hitl/pending").json()
    matching = [p for p in pending if p["incident_id"] == incident_id]
    assert len(matching) >= 1
    request_id = matching[0]["request"]["request_id"]

    approve_resp = client.post(
        f"/hitl/{incident_id}/approve",
        json={"request_id": request_id, "decided_by": "test-suite"},
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "APPROVED"

    # Approving the same request again must now be rejected as a conflict.
    conflict_resp = client.post(
        f"/hitl/{incident_id}/approve",
        json={"request_id": request_id, "decided_by": "test-suite"},
    )
    assert conflict_resp.status_code == 409
    conflict_body = conflict_resp.json()
    assert conflict_body["error"] == "HITL_ALREADY_DECIDED"
    assert conflict_body["detail"]["decision"] == "APPROVED"

    final = _wait_for_status(client, incident_id, {"RESOLVED", "ERROR"})
    assert final["status"] == "RESOLVED"


def test_hitl_approve_unknown_request_returns_404(client: TestClient):
    created = client.post("/simulate/1").json()
    incident_id = created["incident_id"]
    resp = client.post(
        f"/hitl/{incident_id}/approve",
        json={"request_id": "hitl_does_not_exist", "decided_by": "test-suite"},
    )
    assert resp.status_code == 404


def test_get_rca_returns_valid_json(client: TestClient):
    created = client.post("/simulate/2").json()
    incident_id = created["incident_id"]

    detail = _wait_for_status(client, incident_id, {"RESOLVED", "ERROR"})
    assert detail["status"] == "RESOLVED"

    rca_resp = client.get(f"/incidents/{incident_id}/rca")
    assert rca_resp.status_code == 200
    rca = rca_resp.json()
    for key in ("incident_id", "title", "severity", "timeline", "root_cause", "affected_services"):
        assert key in rca
    assert rca["incident_id"] == incident_id
