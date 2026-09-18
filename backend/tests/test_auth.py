"""
backend/tests/test_auth.py

Tests for scoped API key authentication (backend/auth.py). Dr Agent
recommendation: "Implement OAuth2 or scoped API keys for securing the
system in a multi-user environment."

`settings` (backend/settings.py) is a process-wide singleton built once at
import time -- like agents/tests/test_settings.py already established,
monkeypatching an env var after that doesn't retroactively change it.
Tests that need "auth enabled" behavior build a real, fresh
`SREAgentSettings` instance and monkeypatch `backend.auth`'s module-level
reference to it (plus rebuild the key registry from it), which exercises
the exact same code paths a real deployment with AUTH_ENABLED=true would.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.auth as auth_module
from backend.auth import APIScope, _build_key_registry, _hash_key, generate_api_key
from backend.main import app
from backend.secrets import secrets as _secrets_singleton
from backend.settings import SREAgentSettings


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_enabled(monkeypatch):
    """Swaps backend.auth's settings reference for a fresh instance with
    auth enabled and known keys, then restores the original after the
    test -- the only reliable way to flip this process-wide singleton's
    effective behavior mid test-session (see module docstring).

    backend/secrets.py's SecretManager also caches resolved values (see
    agents/tests/test_settings.py's _reset_secrets_cache for the same
    issue): the real `settings` singleton already resolved DEMO_API_KEY/etc
    once at import time and cached the result, so without clearing it here
    SREAgentSettings's model_validator would silently ignore these
    constructor kwargs and return the stale cached values instead."""
    _secrets_singleton._secret_cache.clear()
    test_settings = SREAgentSettings(
        auth_enabled=True,
        demo_api_key="test-demo-key",
        read_api_key="test-read-key",
        write_api_key="test-write-key",
        admin_api_key="test-admin-key",
    )
    monkeypatch.setattr(auth_module, "settings", test_settings)
    monkeypatch.setattr(auth_module, "_KEY_REGISTRY", _build_key_registry(test_settings))
    return test_settings


def test_auth_info_endpoint(client: TestClient):
    r = client.get("/auth/info")
    assert r.status_code == 200
    data = r.json()
    assert "auth_enabled" in data
    assert "schemes" in data
    assert "scopes" in data
    assert "demo_key" in data


def test_auth_disabled_allows_all(client: TestClient):
    # Real default state of the process-wide settings singleton in this
    # test environment: AUTH_ENABLED is unset, so it defaults to False.
    r = client.get("/incidents")
    assert r.status_code == 200
    r2 = client.get("/config")
    assert r2.status_code == 200


def test_hash_key_is_deterministic():
    assert _hash_key("test-key-123") == _hash_key("test-key-123")


def test_hash_key_different_keys():
    assert _hash_key("key-a") != _hash_key("key-b")


def test_generate_api_key_unique():
    k1 = generate_api_key()
    k2 = generate_api_key()
    assert k1 != k2
    assert k1.startswith("sre-")
    assert len(k1) > 20


def test_scope_enum_values():
    assert APIScope.READ == "read"
    assert APIScope.WRITE == "write"
    assert APIScope.ADMIN == "admin"


def test_missing_key_returns_401(client: TestClient, auth_enabled):
    r = client.get("/incidents")
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "UNAUTHORIZED"


def test_invalid_key_returns_403(client: TestClient, auth_enabled):
    r = client.get("/incidents", headers={"X-API-Key": "completely-wrong-key"})
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "FORBIDDEN"


def test_demo_key_grants_admin_access(client: TestClient, auth_enabled):
    r = client.get("/config", headers={"X-API-Key": "test-demo-key"})
    assert r.status_code == 200


def test_read_key_cannot_reach_write_endpoint(client: TestClient, auth_enabled):
    r = client.post("/simulate/1", headers={"X-API-Key": "test-read-key"})
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "INSUFFICIENT_SCOPE"


def test_write_key_can_reach_write_endpoint(client: TestClient, auth_enabled):
    r = client.post("/simulate/1", headers={"X-API-Key": "test-write-key"})
    assert r.status_code == 200


def test_write_key_cannot_reach_admin_endpoint(client: TestClient, auth_enabled):
    r = client.get("/lyzr/status", headers={"X-API-Key": "test-write-key"})
    assert r.status_code == 403


def test_admin_key_reaches_every_scope(client: TestClient, auth_enabled):
    assert client.get("/incidents", headers={"X-API-Key": "test-admin-key"}).status_code == 200
    assert client.post("/simulate/1", headers={"X-API-Key": "test-admin-key"}).status_code == 200
    assert client.get("/config", headers={"X-API-Key": "test-admin-key"}).status_code == 200


def test_query_param_key_also_works(client: TestClient, auth_enabled):
    r = client.get("/incidents?api_key=test-admin-key")
    assert r.status_code == 200
