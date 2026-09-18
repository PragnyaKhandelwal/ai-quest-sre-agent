"""
Dynamic Configuration Hot-Reload
==================================
Allows updating configuration values without restarting the backend
service.

Dr Agent recommendation: "Consider integrating a mechanism for dynamic
configuration updates without requiring a full service restart."

Supports updating: confidence_threshold, hitl_timeout_seconds, and the
enable_* feature flags (see backend/main.py's ALLOWED_KEYS for the exact,
API-exposed subset -- this class itself will accept any real field on
backend/settings.py's SREAgentSettings).

Changes take effect immediately on the next read of `live_config.get(...)`.
Config history is maintained in-memory for audit purposes (GET /config/live/history).
"""
import logging
from datetime import datetime, timezone
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


class HotReloadConfig:
    """Thread-safe dynamic configuration store. Wraps the Pydantic
    settings singleton (backend/settings.py) with live-update capability."""

    def __init__(self):
        from backend.settings import settings

        self._base = settings
        self._overrides: dict[str, Any] = {}
        self._history: list[dict] = []
        self._lock = Lock()

    def get(self, key: str, default=None) -> Any:
        """Get a config value -- an override takes precedence over the base setting."""
        with self._lock:
            if key in self._overrides:
                return self._overrides[key]
            return getattr(self._base, key, default)

    def set(self, key: str, value: Any, reason: str = "") -> dict:
        """Update a config value at runtime. Returns the update record for the audit log."""
        if key not in self._base.model_fields:
            raise ValueError(f"Unknown config key: '{key}'. Valid keys: {list(self._base.model_fields)}")

        old_value = self.get(key)

        with self._lock:
            self._overrides[key] = value
            record = {
                "key": key,
                "old_value": old_value,
                "new_value": value,
                "reason": reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "effective": True,
            }
            self._history.append(record)

        logger.info(f"Config hot-reload: {key} = {old_value!r} -> {value!r}" + (f" (reason: {reason})" if reason else ""))
        return record

    def reset(self, key: str) -> dict:
        """Reset a key to its base settings value."""
        base_value = getattr(self._base, key, None)
        with self._lock:
            self._overrides.pop(key, None)
            record = {
                "key": key,
                "action": "reset",
                "value": base_value,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._history.append(record)
        logger.info(f"Config reset: {key} -> base value {base_value!r}")
        return record

    def reset_all(self) -> int:
        """Reset all overrides to base settings."""
        with self._lock:
            count = len(self._overrides)
            self._overrides.clear()
        logger.info(f"Config reset: cleared {count} override(s)")
        return count

    def snapshot(self) -> dict:
        """Current effective config (base + overrides)."""
        base = self._base.summary()
        base.update(
            {
                "overrides": dict(self._overrides),
                "override_count": len(self._overrides),
                "last_updated": (self._history[-1]["timestamp"] if self._history else None),
            }
        )
        return base

    def history(self, limit: int = 20) -> list[dict]:
        """Config change history (most recent first)."""
        with self._lock:
            return list(reversed(self._history[-limit:]))


# Singleton
live_config = HotReloadConfig()
