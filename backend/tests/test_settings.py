"""
backend/tests/test_settings.py

Unit + HTTP tests for the centralized Pydantic Settings object
(backend/settings.py), which superseded the earlier plain-class
backend/config.py -- Dr Agent's "centralize configuration" recommendation.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.secrets import secrets as _secrets_singleton
from backend.settings import SREAgentSettings, settings


def _reset_secrets_cache():
    """backend/secrets.py's SecretManager caches resolved values in-memory
    (by design, to avoid repeated Vault round-trips) -- tests that flip an
    API-key env var between calls must clear it first, or they'll read a
    stale value cached by an earlier test."""
    _secrets_singleton._secret_cache.clear()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_settings_loads():
    s = SREAgentSettings()
    assert s.environment in ["development", "staging", "production", "test"]


def test_settings_defaults():
    s = SREAgentSettings(_env_file=None)
    assert s.confidence_threshold == 0.70
    assert s.max_tokens == 1000
    assert s.model_temperature == 0.2
    assert s.hitl_timeout_seconds == 300


def test_settings_cors_list():
    s = SREAgentSettings(cors_origins="http://localhost:5173,https://example.com")
    assert "http://localhost:5173" in s.cors_origins_list
    assert "https://example.com" in s.cors_origins_list


def test_settings_active_provider_simulation():
    s = SREAgentSettings(openai_api_key="", groq_api_key="", lyzr_api_key="")
    assert s.active_llm_provider == "simulation"


def test_settings_active_provider_prefers_openai_over_groq(monkeypatch):
    # Constructor kwargs alone aren't enough here: SREAgentSettings's
    # model_validator deliberately makes backend/secrets.py's resolver
    # (Vault -> cloud -> env vars) authoritative for these 3 fields, so it
    # overwrites whatever the constructor was given with os.getenv(...) --
    # by design, so a configured Vault always wins over a stray constructor
    # value. Set the underlying env vars instead to exercise that path.
    _reset_secrets_cache()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    s = SREAgentSettings()
    assert s.active_llm_provider == "openai"
    assert s.use_real_openai is True


def test_settings_active_provider_groq_when_no_openai(monkeypatch):
    _reset_secrets_cache()
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    s = SREAgentSettings()
    assert s.active_llm_provider == "groq"


def test_settings_summary_no_secrets():
    summary = settings.summary()
    assert "lyzr_api_key" not in summary
    assert "openai_api_key" not in summary
    assert "groq_api_key" not in summary
    assert "vault_token" not in summary
    assert "environment" in summary
    assert "llm_provider" in summary
    assert "features" in summary


def test_config_endpoint(client: TestClient):
    r = client.get("/config")
    assert r.status_code == 200
    data = r.json()
    assert "environment" in data
    assert "llm_provider" in data
    assert "features" in data
    assert "lyzr_api_key" not in data


def test_system_info_includes_settings_summary(client: TestClient):
    r = client.get("/system/info")
    assert r.status_code == 200
    data = r.json()
    assert "settings" in data
    assert "environment" in data["settings"]
