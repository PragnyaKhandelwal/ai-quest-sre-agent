"""
Environment-aware configuration (dev/staging/prod separation).
==================================================================
Loads config/{ENVIRONMENT}.env and exposes it as `settings`, so
log verbosity, worker count, rate limits, and allowed CORS origins can
differ per deployment target without changing code.

Note: this module is intentionally NOT auto-wired into backend/main.py's
CORS middleware or rate limiter today -- main.py's CORS is deliberately
wide open (`allow_origins=["*"]`) for the hackathon demo/judging deploy,
and Render currently has no ENVIRONMENT var set (so this would silently
default to "development", i.e. localhost-only CORS_ORIGINS, and break the
live Vercel frontend). To adopt `settings.cors_origins` in production, set
ENVIRONMENT=production in Render's dashboard first, then switch main.py's
CORSMiddleware to read from `settings.cors_origins`.
"""
import os
from pathlib import Path

from dotenv import dotenv_values

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

_config_path = Path(__file__).resolve().parent.parent / "config" / f"{ENVIRONMENT}.env"
_env_config = dotenv_values(_config_path) if _config_path.exists() else {}

LOG_LEVEL = _env_config.get("LOG_LEVEL", os.getenv("LOG_LEVEL", "INFO"))
WORKERS = int(_env_config.get("WORKERS", os.getenv("WORKERS", "1")))
RATE_LIMIT = _env_config.get("RATE_LIMIT", "10/minute")
CORS_ORIGINS = _env_config.get(
    "CORS_ORIGINS",
    "https://ai-quest-sre-agent.vercel.app,http://localhost:5173",
).split(",")


class Settings:
    environment: str = ENVIRONMENT
    log_level: str = LOG_LEVEL
    workers: int = WORKERS
    rate_limit: str = RATE_LIMIT
    cors_origins: list = CORS_ORIGINS
    debug: bool = ENVIRONMENT == "development"


settings = Settings()
