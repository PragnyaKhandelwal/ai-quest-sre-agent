"""
backend/tests/test_config_watcher.py

Tests for dynamic configuration hot-reload (backend/config_watcher.py).
Dr Agent recommendation: "Consider integrating a mechanism for dynamic
configuration updates without requiring a full service restart."

Each test constructs its own fresh HotReloadConfig() (rather than sharing
backend.config_watcher.live_config) so tests never leak overrides into
each other or into the live app's dependencies (backend/store.py and
agents/diagnostician_agent.py both read through the shared singleton).
"""
import pytest
from fastapi.testclient import TestClient

from backend.config_watcher import HotReloadConfig
from backend.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_hot_reload_set_and_get():
    config = HotReloadConfig()
    config.set("confidence_threshold", 0.85, "test")
    assert config.get("confidence_threshold") == 0.85


def test_hot_reload_override_takes_precedence():
    config = HotReloadConfig()
    base_val = config.get("confidence_threshold")
    config.set("confidence_threshold", 0.99)
    assert config.get("confidence_threshold") == 0.99
    assert config.get("confidence_threshold") != base_val


def test_hot_reload_reset():
    config = HotReloadConfig()
    original = config.get("confidence_threshold")
    config.set("confidence_threshold", 0.99)
    config.reset("confidence_threshold")
    assert config.get("confidence_threshold") == original


def test_hot_reload_reset_all():
    config = HotReloadConfig()
    config.set("confidence_threshold", 0.99)
    config.set("hitl_timeout_seconds", 600)
    count = config.reset_all()
    assert count == 2
    assert len(config._overrides) == 0


def test_hot_reload_history():
    config = HotReloadConfig()
    config.set("confidence_threshold", 0.80, "test reason")
    config.set("hitl_timeout_seconds", 120)
    history = config.history()
    assert len(history) >= 2
    assert history[0]["key"] == "hitl_timeout_seconds"


def test_hot_reload_invalid_key():
    config = HotReloadConfig()
    with pytest.raises(ValueError, match="Unknown config key"):
        config.set("nonexistent_key_xyz", "value")


def test_hot_reload_snapshot():
    config = HotReloadConfig()
    config.set("confidence_threshold", 0.88)
    snap = config.snapshot()
    assert "overrides" in snap
    assert "confidence_threshold" in snap["overrides"]
    assert snap["overrides"]["confidence_threshold"] == 0.88


def test_hot_reload_get_default_for_unknown_attr():
    config = HotReloadConfig()
    assert config.get("truly_nonexistent_field", "fallback") == "fallback"


def test_config_live_endpoint(client: TestClient):
    r = client.get("/config/live")
    assert r.status_code == 200
    data = r.json()
    assert "overrides" in data


def test_config_history_endpoint(client: TestClient):
    r = client.get("/config/live/history")
    assert r.status_code == 200
    data = r.json()
    assert "history" in data
    assert "total_changes" in data


def test_config_live_patch_updates_and_resets(client: TestClient):
    r = client.patch("/config/live", json={"confidence_threshold": 0.85, "reason": "test"})
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] == 1
    assert body["current_config"]["overrides"]["confidence_threshold"] == 0.85

    r2 = client.get("/config/live")
    assert r2.json()["overrides"]["confidence_threshold"] == 0.85

    r3 = client.delete("/config/live/confidence_threshold")
    assert r3.status_code == 200
    assert "confidence_threshold" not in client.get("/config/live").json()["overrides"]


def test_config_live_patch_rejects_disallowed_key(client: TestClient):
    r = client.patch("/config/live", json={"lyzr_api_key": "sneaky"})
    assert r.status_code == 422
    data = r.json()
    assert data["error"] == "CONFIG_KEY_NOT_ALLOWED"
    assert data["detail"]["key"] == "lyzr_api_key"
