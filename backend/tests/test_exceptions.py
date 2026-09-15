"""
backend/tests/test_exceptions.py

HTTP-level tests for the granular exception hierarchy (backend/exceptions.py)
and its registered handlers in backend/main.py -- Dr Agent's "replace the
catch-all handler with specific handlers" recommendation.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

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
