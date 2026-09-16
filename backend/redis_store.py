"""
Redis-backed durability layer for incidents and AIMS events
=============================================================
Replaces backend/persistence.py's file-based JSON snapshots. The live,
actionable incident state (backend/store.py's `INCIDENTS` dict) still has
to stay in-process -- it holds asyncio.Event/Queue objects for HITL
synchronization and SSE fan-out that cannot be serialized into Redis or
shared across processes -- but the durable *snapshot* of each incident
(and the AIMS audit trail) now lives here instead of on local disk.

Dr Agent recommendation: "Replace the current ephemeral file-based
incident store with a robust, production-grade database like PostgreSQL
or a key-value store like Redis."

Configuration:
  - Production: set REDIS_URL (Redis Cloud, Upstash, Render Redis, etc.)
  - Development/demo: falls back to fakeredis automatically -- honest
    scope note: fakeredis is pure in-process memory, so without a real
    REDIS_URL this is not actually more durable than the file-based
    store it replaces across a full process restart. What it *does* give
    even in fakeredis mode is a single, consistent read/write API that
    becomes genuinely durable the moment a real REDIS_URL is supplied,
    with zero code changes.

Redis key structure:
  incident:{incident_id}     -> JSON blob of the incident's public view
  incidents:index            -> Redis Set of all incident IDs
  aims:events                -> Redis List of AIMS events (chronological)
  aims:events:{incident_id}  -> Redis List of per-incident AIMS events
"""

import json
import logging
import os
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "")


def _create_redis_client():
    """Create a Redis client -- real if REDIS_URL is set, fakeredis otherwise."""
    if REDIS_URL:
        try:
            import redis

            client = redis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
            )
            client.ping()
            logger.info(f"Redis connected: {REDIS_URL[:30]}...")
            return client, "redis"
        except Exception as e:
            logger.warning(f"Redis connection failed: {e} -- using fakeredis")

    try:
        import fakeredis

        client = fakeredis.FakeRedis(decode_responses=True)
        mode = "fakeredis (development)"
        logger.info("Using fakeredis -- set REDIS_URL for production Redis")
        return client, mode
    except ImportError:
        logger.error("Neither redis nor fakeredis available")
        raise


_redis_client, _redis_mode = _create_redis_client()

# Key prefixes
INCIDENT_KEY = "incident:{}"
INDEX_KEY = "incidents:index"
AIMS_KEY = "aims:events"
AIMS_INCIDENT_KEY = "aims:events:{}"
TTL_SECONDS = 86400 * 7  # 7 days


class RedisIncidentStore:
    """
    Durability layer for incident snapshots and AIMS events, backed by
    Redis (or fakeredis when no REDIS_URL is configured).
    """

    def __init__(self, client, mode: str):
        self._r = client
        self.mode = mode
        logger.info(f"RedisIncidentStore initialized: {mode}")

    def set_incident(self, incident_id: str, state: dict) -> bool:
        """Save or update an incident snapshot. Returns True on success."""
        try:
            key = INCIDENT_KEY.format(incident_id)
            state = dict(state)
            state["_updated_at"] = datetime.utcnow().isoformat()
            self._r.setex(key, TTL_SECONDS, json.dumps(state, default=str))
            self._r.sadd(INDEX_KEY, incident_id)
            logger.debug(f"Saved incident {incident_id} to Redis")
            return True
        except Exception as e:
            logger.warning(f"Redis set failed for {incident_id}: {e}")
            return False

    def get_incident(self, incident_id: str) -> Optional[dict]:
        """Get an incident snapshot by ID. Returns None if not found."""
        try:
            key = INCIDENT_KEY.format(incident_id)
            data = self._r.get(key)
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            logger.warning(f"Redis get failed for {incident_id}: {e}")
            return None

    def list_incidents(self) -> list[dict]:
        """List all incident snapshots, newest first."""
        try:
            ids = self._r.smembers(INDEX_KEY)
            incidents = []
            for inc_id in ids:
                data = self.get_incident(inc_id)
                if data:
                    incidents.append(data)
            return sorted(incidents, key=lambda x: x.get("created_at", 0) or 0, reverse=True)
        except Exception as e:
            logger.warning(f"Redis list failed: {e}")
            return []

    def delete_incident(self, incident_id: str) -> bool:
        """Delete an incident snapshot."""
        try:
            key = INCIDENT_KEY.format(incident_id)
            self._r.delete(key)
            self._r.srem(INDEX_KEY, incident_id)
            return True
        except Exception as e:
            logger.warning(f"Redis delete failed for {incident_id}: {e}")
            return False

    def append_aims_event(self, event: dict) -> bool:
        """Append an event to the durable AIMS audit trail (global + per-incident)."""
        try:
            event_json = json.dumps(event, default=str)
            self._r.lpush(AIMS_KEY, event_json)
            self._r.ltrim(AIMS_KEY, 0, 999)  # keep the last 1000 events
            incident_id = event.get("incident_id") or "unknown"
            inc_key = AIMS_INCIDENT_KEY.format(incident_id)
            self._r.lpush(inc_key, event_json)
            self._r.expire(inc_key, TTL_SECONDS)
            return True
        except Exception as e:
            logger.warning(f"Redis AIMS append failed: {e}")
            return False

    def get_aims_events(self, incident_id: Optional[str] = None) -> list[dict]:
        """Get AIMS events -- all, or filtered to one incident."""
        try:
            key = AIMS_INCIDENT_KEY.format(incident_id) if incident_id else AIMS_KEY
            events_json = self._r.lrange(key, 0, -1)
            events = []
            for e in events_json:
                try:
                    events.append(json.loads(e))
                except json.JSONDecodeError:
                    pass
            return events
        except Exception as e:
            logger.warning(f"Redis AIMS get failed: {e}")
            return []

    def flush_all(self) -> bool:
        """Flush all incidents and AIMS events -- for testing only."""
        try:
            ids = self._r.smembers(INDEX_KEY)
            for inc_id in ids:
                self._r.delete(INCIDENT_KEY.format(inc_id))
            self._r.delete(INDEX_KEY)
            self._r.delete(AIMS_KEY)
            return True
        except Exception as e:
            logger.warning(f"Redis flush failed: {e}")
            return False

    def health(self) -> dict:
        """Redis connection health, surfaced at GET /system/info."""
        try:
            self._r.ping()
            return {
                "status": "connected",
                "mode": self.mode,
                "redis_url": (REDIS_URL[:20] + "...") if REDIS_URL else "fakeredis",
                "incident_count": self._r.scard(INDEX_KEY),
                "aims_event_count": self._r.llen(AIMS_KEY),
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "mode": self.mode}


# Singleton store
incident_store = RedisIncidentStore(_redis_client, _redis_mode)
