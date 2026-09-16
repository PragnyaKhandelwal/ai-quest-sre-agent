"""
backend/tests/test_redis_store.py

Unit tests for the Redis-backed durability layer (backend/redis_store.py)
that replaced the file-based persistence.py. Uses fakeredis so these run
with no real Redis server, matching the same fallback the app itself uses
when REDIS_URL is unset.
"""
from __future__ import annotations

import pytest

from backend.redis_store import RedisIncidentStore

try:
    import fakeredis

    FAKEREDIS_AVAILABLE = True
except ImportError:
    FAKEREDIS_AVAILABLE = False


@pytest.fixture
def store():
    """Fresh RedisIncidentStore (backed by a fresh fakeredis instance) per test."""
    if not FAKEREDIS_AVAILABLE:
        pytest.skip("fakeredis not available")
    client = fakeredis.FakeRedis(decode_responses=True)
    return RedisIncidentStore(client, "fakeredis-test")


def test_set_and_get_incident(store):
    state = {
        "incident_id": "inc_test001",
        "status": "TRIAGING",
        "severity": "P1",
        "service": "payment-service",
        "created_at": 1234567890.0,
    }
    assert store.set_incident("inc_test001", state) is True
    retrieved = store.get_incident("inc_test001")
    assert retrieved is not None
    assert retrieved["incident_id"] == "inc_test001"
    assert retrieved["status"] == "TRIAGING"


def test_get_nonexistent_returns_none(store):
    assert store.get_incident("nonexistent_xyz") is None


def test_update_incident(store):
    store.set_incident("inc_test002", {"status": "TRIAGING", "incident_id": "inc_test002"})
    store.set_incident("inc_test002", {"status": "RESOLVED", "incident_id": "inc_test002"})
    result = store.get_incident("inc_test002")
    assert result["status"] == "RESOLVED"


def test_list_incidents(store):
    store.set_incident("inc_a", {"incident_id": "inc_a", "created_at": 100.0})
    store.set_incident("inc_b", {"incident_id": "inc_b", "created_at": 200.0})
    incidents = store.list_incidents()
    assert len(incidents) >= 2
    ids = [i["incident_id"] for i in incidents]
    assert "inc_a" in ids
    assert "inc_b" in ids
    # Newest first
    assert incidents[0]["incident_id"] == "inc_b"


def test_delete_incident(store):
    store.set_incident("inc_del", {"incident_id": "inc_del", "status": "RESOLVED"})
    assert store.get_incident("inc_del") is not None
    store.delete_incident("inc_del")
    assert store.get_incident("inc_del") is None


def test_aims_append_and_get(store):
    event = {
        "incident_id": "inc_aims001",
        "agent_name": "TriageAgent",
        "action": "INFERENCE",
        "timestamp": 1234567890.0,
    }
    store.append_aims_event(event)
    events = store.get_aims_events("inc_aims001")
    assert len(events) >= 1
    assert events[0]["agent_name"] == "TriageAgent"


def test_aims_global_log(store):
    for i in range(3):
        store.append_aims_event({
            "incident_id": f"inc_{i}",
            "agent_name": "TestAgent",
            "timestamp": float(i),
        })
    events = store.get_aims_events()
    assert len(events) >= 3


def test_store_health(store):
    health = store.health()
    assert health["status"] == "connected"
    assert health["mode"] == "fakeredis-test"
    assert "incident_count" in health
    assert "aims_event_count" in health


def test_flush_all(store):
    store.set_incident("inc_flush1", {"incident_id": "inc_flush1"})
    store.set_incident("inc_flush2", {"incident_id": "inc_flush2"})
    store.flush_all()
    assert store.list_incidents() == []
