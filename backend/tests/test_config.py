"""
backend/tests/test_config.py

Unit tests for the environment-aware settings loader (backend/config.py)
added for Block 8's dev/staging/prod separation.
"""
from __future__ import annotations

import importlib


def _reload_config():
    from backend import config
    return importlib.reload(config)


def test_defaults_to_development_environment(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    config = _reload_config()
    assert config.settings.environment == "development"
    assert config.settings.debug is True


def test_production_env_file_is_loaded(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    config = _reload_config()
    assert config.settings.environment == "production"
    assert config.settings.log_level == "WARNING"
    assert config.settings.rate_limit == "10/minute"
    assert config.settings.debug is False
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    _reload_config()  # restore module state for subsequent tests


def test_staging_env_file_is_loaded(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    config = _reload_config()
    assert config.settings.workers == 2
    assert "staging-sre-agent.vercel.app" in config.settings.cors_origins[0]
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    _reload_config()


def test_unknown_environment_falls_back_to_defaults(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "nonexistent_env_xyz")
    config = _reload_config()
    assert config.settings.rate_limit == "10/minute"
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    _reload_config()
