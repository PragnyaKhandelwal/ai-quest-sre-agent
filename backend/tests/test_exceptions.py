"""
backend/tests/test_exceptions.py

HTTP-level tests for the granular exception hierarchy (backend/exceptions.py)
and its registered handlers in backend/main.py -- Dr Agent's "replace the
catch-all handler with specific handlers" recommendation.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agents.schemas import Alert
from backend import store
from backend.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_incident_not_found_returns_404(client: TestClient):
    r = client.get("/incidents/nonexistent_xyz")
    assert r.status_code == 404
    data = r.json()
    assert data["error"] == "INCIDENT_NOT_FOUND"
    assert "incident_id" in data["detail"]


def test_invalid_scenario_returns_422(client: TestClient):
    r = client.post("/simulate/99")
    assert r.status_code == 422
    data = r.json()
    assert data["error"] == "INVALID_SCENARIO"
    assert "scenarios" in data["detail"]


def test_hitl_not_found_returns_404(client: TestClient):
    # A body is required by HITLDecisionPayload -- send one so the request
    # actually reaches the route handler (and its 404 branch) instead of
    # failing pydantic validation with a 422 first.
    r = client.post(
        "/hitl/nonexistent_xyz/approve",
        json={"request_id": "hitl_nonexistent"},
    )
    assert r.status_code == 404
    assert r.json()["error"] == "HITL_ACTION_NOT_FOUND"


def test_tool_not_found_returns_404(client: TestClient):
    r = client.post("/tools/nonexistent_tool_xyz", json={})
    assert r.status_code == 404
    data = r.json()
    assert data["error"] == "TOOL_NOT_FOUND"
    assert "available_tools" in data["detail"]


def test_error_response_has_required_fields(client: TestClient):
    r = client.get("/incidents/bad_id_xyz")
    data = r.json()
    assert "error" in data
    assert "message" in data
    assert "detail" in data


def test_unmatched_route_returns_generic_not_found(client: TestClient):
    r = client.get("/this/route/does/not/exist")
    assert r.status_code == 404
    data = r.json()
    assert data["error"] == "NOT_FOUND"
    assert "path" in data


def test_empty_alert_batch_returns_400(client: TestClient):
    r = client.post("/alerts/ingest", json={"alerts": []})
    assert r.status_code == 400
    data = r.json()
    assert data["error"] == "EMPTY_ALERT_BATCH"


def test_config_key_not_allowed_returns_422(client: TestClient):
    r = client.patch("/config/live", json={"not_a_real_key": 1})
    assert r.status_code == 422
    data = r.json()
    assert data["error"] == "CONFIG_KEY_NOT_ALLOWED"
    assert data["detail"]["key"] == "not_a_real_key"
    assert "allowed_keys" in data["detail"]


def test_tool_input_validation_error_returns_422(client: TestClient):
    tools_resp = client.get("/tools")
    tool_name = next(iter(tools_resp.json()))
    # Sending a body that fails the tool's own argument validation (rather
    # than a nonexistent tool name, which is ToolNotFoundError's case)
    # should surface as a specific ToolInputValidationError, not a bare 422.
    r = client.post(f"/tools/{tool_name}", json={"this_is_not_a_real_argument": True})
    assert r.status_code == 422
    data = r.json()
    assert data["error"] == "TOOL_INPUT_INVALID"
    assert data["detail"]["tool_name"] == tool_name


def test_rca_not_available_returns_404(client: TestClient):
    incident_id = "inc_test_no_rca_xyz"
    store.create_incident(
        incident_id,
        alerts=[
            Alert(
                service="test-service",
                alertname="TestAlert",
                title="Test alert",
                description="Synthetic alert for RCA-not-ready test",
            )
        ],
        log_corpus=[],
    )
    r = client.get(f"/incidents/{incident_id}/rca")
    assert r.status_code == 404
    assert r.json()["error"] == "RCA_NOT_AVAILABLE"
