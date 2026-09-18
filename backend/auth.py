"""
API Key Authentication -- Production Security Layer
===================================================
Implements scoped API key authentication for the SRE Agent API.
Dr Agent recommendation: "Implement OAuth2 or scoped API keys for securing
the system in a multi-user environment."

Three access levels:
  - READ scope: GET endpoints (incidents, metrics, AIMS events)
  - WRITE scope: POST endpoints (simulate, alerts ingest/webhook, HITL
    approve/reject, incident reset, direct tool calls)
  - ADMIN scope: All endpoints including /config, /lyzr/status

Off by default (backend/settings.py: `auth_enabled`, default False) -- every
route stays open for demo/judging until AUTH_ENABLED=true is set. Keys are
sourced through backend/settings.py, whose model_validator routes them
through backend/secrets.py's layered Vault -> cloud -> env resolver, so a
configured Vault is authoritative for these keys exactly like it is for
LYZR_API_KEY/OPENAI_API_KEY/GROQ_API_KEY.
"""
import hashlib
import logging
import secrets as pysecrets
from enum import Enum
from typing import Optional

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader, APIKeyQuery

from backend.settings import settings

logger = logging.getLogger(__name__)


# -- Scopes ------------------------------------------------------------------
class APIScope(str, Enum):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


_SCOPE_ORDER = {APIScope.READ: 1, APIScope.WRITE: 2, APIScope.ADMIN: 3}

# -- API key header/query schemes ---------------------------------------------
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="API key in header: X-API-Key: <key>")
api_key_query = APIKeyQuery(name="api_key", auto_error=False, description="API key in query: ?api_key=<key>")


def _hash_key(key: str) -> str:
    """Hash an API key for safe storage/comparison (never compare raw keys)."""
    return hashlib.sha256(key.encode()).hexdigest()


def _build_key_registry(settings_obj=None) -> dict:
    """Build the API key registry from settings. `settings_obj` is injectable
    for tests that need a registry built from a non-singleton settings
    instance rather than the process-wide one."""
    s = settings_obj or settings
    registry = {}

    # Demo key -- always available for judges/testing
    registry[_hash_key(s.demo_api_key)] = {
        "name": "Demo Key",
        "scope": APIScope.ADMIN,
        "description": "Demo access for judges and testing",
    }

    if s.read_api_key:
        registry[_hash_key(s.read_api_key)] = {
            "name": "Read Key",
            "scope": APIScope.READ,
            "description": "Read-only access to incidents and metrics",
        }
    if s.write_api_key:
        registry[_hash_key(s.write_api_key)] = {
            "name": "Write Key",
            "scope": APIScope.WRITE,
            "description": "Read + simulate + HITL actions",
        }
    if s.admin_api_key:
        registry[_hash_key(s.admin_api_key)] = {
            "name": "Admin Key",
            "scope": APIScope.ADMIN,
            "description": "Full admin access",
        }

    logger.info(f"API key registry: {len(registry)} keys loaded")
    return registry


# Built once at import time, matching how `settings` itself is a
# process-wide singleton -- API keys don't change without a redeploy.
_KEY_REGISTRY = _build_key_registry()


def get_api_key(
    header_key: Optional[str] = Security(api_key_header),
    query_key: Optional[str] = Security(api_key_query),
) -> Optional[dict]:
    """Resolve an API key from the header or query parameter. Returns key
    metadata, or a synthetic ADMIN-scope entry when auth is disabled."""
    if not settings.auth_enabled:
        return {"name": "demo", "scope": APIScope.ADMIN, "auth_disabled": True}

    raw_key = header_key or query_key
    if not raw_key:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "UNAUTHORIZED",
                "message": "API key required",
                "hint": "Add header: X-API-Key: <your key> (see GET /auth/info)",
                "docs": "/docs#section/Authentication",
            },
        )

    key_meta = _KEY_REGISTRY.get(_hash_key(raw_key))
    if not key_meta:
        raise HTTPException(
            status_code=403,
            detail={"error": "FORBIDDEN", "message": "Invalid API key", "hint": "See GET /auth/info"},
        )

    logger.debug(f"Auth: {key_meta['name']} ({key_meta['scope']})")
    return key_meta


def require_scope(required_scope: APIScope):
    """Dependency factory for scope-based authorization."""

    def check_scope(key_meta: dict = Depends(get_api_key)):
        if not key_meta:
            return key_meta

        user_level = _SCOPE_ORDER.get(key_meta["scope"], 0)
        required_level = _SCOPE_ORDER.get(required_scope, 99)

        if user_level < required_level:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "INSUFFICIENT_SCOPE",
                    "message": f"Requires {required_scope.value} scope, got {key_meta['scope']}",
                    "your_scope": key_meta["scope"],
                    "required_scope": required_scope.value,
                },
            )
        return key_meta

    return check_scope


def generate_api_key() -> str:
    """Generate a cryptographically secure API key (for issuing new keys
    out-of-band -- not used by any endpoint, a standalone admin utility)."""
    return f"sre-{pysecrets.token_urlsafe(32)}"
