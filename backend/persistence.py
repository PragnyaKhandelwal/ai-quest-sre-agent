"""
Lightweight file-based persistence for incident state.
Persists incidents to /tmp/sre_incidents/ so state survives
backend restarts (within the same Render instance).

Scope note: Render's free-tier filesystem is ephemeral across a full
redeploy (a new container gets a fresh /tmp), so this specifically helps
with an in-process restart (e.g. a worker respawn), not a full redeploy --
still a real, honest improvement over pure in-memory state, just not a
substitute for a real database. In production: replace with Redis or
PostgreSQL.

What gets persisted is each incident's already-JSON-safe public view
(IncidentState.to_public_dict()), not the live dataclass itself -- the
live object also holds asyncio.Event/Queue instances (HITL bookkeeping,
SSE subscribers) that cannot be serialized or meaningfully restored.
Restored incidents (see backend/store.py's restore_persisted_incidents())
are therefore read-only historical records: viewable via the API, but not
resumable mid-pipeline or actionable via HITL after a restart.
"""
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PERSIST_DIR = Path("/tmp/sre_incidents")
try:
    # parents=True: on a fresh filesystem (e.g. Windows dev machines, where
    # C:\tmp doesn't exist by default) mkdir(exist_ok=True) alone raises
    # FileNotFoundError because it only creates the final path segment.
    PERSIST_DIR.mkdir(parents=True, exist_ok=True)
except Exception as e:  # pragma: no cover - defensive, filesystem-dependent
    logger.warning(f"Could not create persistence directory {PERSIST_DIR}: {e}")


def save_incident(incident_id: str, state: dict) -> None:
    """Persist incident state to disk."""
    try:
        path = PERSIST_DIR / f"{incident_id}.json"
        with open(path, "w") as f:
            json.dump(state, f, indent=2, default=str)
        logger.debug(f"Persisted incident {incident_id}")
    except Exception as e:
        logger.warning(f"Failed to persist incident {incident_id}: {e}")


def load_incident(incident_id: str) -> Optional[dict]:
    """Load incident state from disk."""
    try:
        path = PERSIST_DIR / f"{incident_id}.json"
        if path.exists():
            with open(path) as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load incident {incident_id}: {e}")
    return None


def load_all_incidents() -> dict:
    """Load all persisted incidents on startup."""
    incidents = {}
    try:
        for path in PERSIST_DIR.glob("*.json"):
            incident_id = path.stem
            data = load_incident(incident_id)
            if data:
                incidents[incident_id] = data
        logger.info(f"Loaded {len(incidents)} persisted incidents on startup")
    except Exception as e:
        logger.warning(f"Failed to load persisted incidents: {e}")
    return incidents
