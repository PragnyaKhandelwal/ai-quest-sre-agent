"""
backend/tests/test_production_readiness.py

Production Readiness Tests
===========================
Verifies the system is production-ready:
- Deep health check returns all dependency statuses
- Prometheus metrics endpoint works
- Circuit breakers are initialized
- Security headers present on all responses
- /config never leaks secrets
- /system/info is a comprehensive, self-documenting snapshot
"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_deep_health_check_structure(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert "status" in data
    assert "checks" in data
    assert "version" in data
    assert "timestamp" in data
    assert data["status"] in ["healthy", "degraded", "unhealthy"]


def test_health_check_has_all_dependencies(client: TestClient):
    r = client.get("/health")
    checks = r.json().get("checks", {})
    assert "redis" in checks
    assert "agents" in checks
    assert "secrets" in checks
    assert "llm" in checks
    assert "system" in checks


def test_health_check_agents_reports_all_four(client: TestClient):
    r = client.get("/health")
    agents_check = r.json()["checks"]["agents"]
    assert agents_check["status"] == "healthy"
    assert agents_check["count"] == 4


def test_prometheus_metrics_endpoint(client: TestClient):
    r = client.get("/metrics/prometheus")
    assert r.status_code == 200
    assert "text/plain" in r.headers.get("content-type", "")
    content = r.text
    assert "sre_" in content or "python_" in content


def test_prometheus_metrics_include_sre_gauges(client: TestClient):
    r = client.get("/metrics/prometheus")
    assert "sre_active_incidents" in r.text
    assert "sre_hitl_queue_depth" in r.text


def test_circuit_breakers_initialized(client: TestClient):
    r = client.get("/circuit-breakers")
    assert r.status_code == 200
    data = r.json()
    assert "breakers" in data
    assert len(data["breakers"]) == 4
    for breaker in data["breakers"]:
        assert "name" in breaker
        assert "state" in breaker
        assert breaker["state"] in ["closed", "open", "half_open"]


def test_security_headers_present(client: TestClient):
    r = client.get("/health")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-powered-by") is not None


def test_config_endpoint_no_secrets(client: TestClient):
    r = client.get("/config")
    assert r.status_code == 200
    data = r.json()
    # Must not expose raw secret values
    assert "lyzr_api_key" not in data
    assert "openai_api_key" not in data
    assert "groq_api_key" not in data
    assert "redis_url" not in data
    # Must show a safe summary
    assert "environment" in data
    assert "llm_provider" in data


def test_system_info_comprehensive(client: TestClient):
    r = client.get("/system/info")
    assert r.status_code == 200
    data = r.json()
    assert "version" in data
    assert "features" in data
    assert "live_endpoints" in data
    assert "stretch_goals_completed" in data
    assert len(data["stretch_goals_completed"]) == 3


def test_root_endpoint_returns_api_info(client: TestClient):
    r = client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert "version" in data
    assert "docs" in data


def test_redis_store_health(client: TestClient):
    r = client.get("/system/info")
    data = r.json()
    sm = data["state_management"]
    assert "status" in sm
    assert sm["status"] in ["connected", "error"]


def test_agent_count_is_four(client: TestClient):
    r = client.get("/lyzr/status")
    assert r.status_code == 200
    data = r.json()
    agents = data.get("layer_2_agents", {})
    assert len(agents) == 4


def test_tools_registry_complete(client: TestClient):
    r = client.get("/tools")
    assert r.status_code == 200
    data = r.json()
    assert "query_logs" in data
    assert "query_metrics" in data
    assert "kubectl_safe" in data
    assert "correlate_alerts" in data
