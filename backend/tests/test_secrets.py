"""
backend/tests/test_secrets.py

Unit tests for the layered secret resolver (backend/secrets.py) added for
Dr Agent's "replace .env with a real secret manager" recommendation.
"""
from __future__ import annotations

from backend.secrets import SecretManager, secrets


def test_secrets_resolves_from_env(monkeypatch):
    monkeypatch.setenv("TEST_SECRET_XYZ", "test_value_123")
    sm = SecretManager()
    assert sm.get("TEST_SECRET_XYZ") == "test_value_123"


def test_secrets_default_when_missing():
    sm = SecretManager()
    assert sm.get("NONEXISTENT_KEY_XYZ", "default") == "default"


def test_secrets_caches_resolved_values(monkeypatch):
    monkeypatch.setenv("TEST_SECRET_CACHE", "first_value")
    sm = SecretManager()
    assert sm.get("TEST_SECRET_CACHE") == "first_value"
    # Even if the underlying env var changes, the cached value should stick --
    # this is the in-memory cache the docstring promises, avoiding a repeat
    # round-trip to a real secret store on every call.
    monkeypatch.setenv("TEST_SECRET_CACHE", "second_value")
    assert sm.get("TEST_SECRET_CACHE") == "first_value"


def test_secrets_provider_is_one_of_the_known_backends():
    assert secrets.provider in [
        "environment_variables",
        "hashicorp_vault",
        "aws_secrets_manager",
        "gcp_secret_manager",
    ]


def test_secrets_health_returns_dict():
    health = secrets.health()
    assert "provider" in health
    assert "vault_available" in health
    assert "cached_keys" in health
    assert "vault_addr" in health


def test_secrets_falls_back_to_env_without_vault_configured(monkeypatch):
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    monkeypatch.delenv("VAULT_TOKEN", raising=False)
    monkeypatch.delenv("AWS_SECRET_ARN", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    sm = SecretManager()
    assert sm.provider == "environment_variables"
    assert sm.health()["vault_available"] is False
