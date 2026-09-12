"""
agents/config.py

Single source of truth for the entire governed multi-agent SRE system.
Every agent, the pipeline, the backend, and the safety gate read their
configuration from this file. Nothing here should be hardcoded elsewhere.

All secrets are read from environment variables (see .env.example).
"""
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Load .env if python-dotenv is available (optional convenience for local dev)
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # pragma: no cover - dotenv is optional
    pass

# ---------------------------------------------------------------------------
# Lyzr platform configuration
# ---------------------------------------------------------------------------
LYZR_API_KEY = os.getenv("LYZR_API_KEY", "")
LYZR_BASE_URL = os.getenv("LYZR_BASE_URL", "https://agent.api.lyzr.ai/v2")
LYZR_AIMS_URL = os.getenv("LYZR_AIMS_URL", "https://aims.api.lyzr.ai/v1")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# If no Lyzr key is configured, the system runs fully in "local simulation"
# mode: every agent falls back to deterministic, schema-validated local
# reasoning instead of calling out to the Lyzr Agent API. This keeps the
# demo 100% functional with zero external dependencies / zero API cost,
# while the exact same code path is used the moment real keys are supplied.
LYZR_ENABLED = bool(LYZR_API_KEY)

# ---------------------------------------------------------------------------
# Model / inference configuration
# ---------------------------------------------------------------------------
MODEL = os.getenv("MODEL", "gpt-4o-mini")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "1000"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.2"))

# ---------------------------------------------------------------------------
# Governance thresholds
# ---------------------------------------------------------------------------
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.70"))
HITL_TIMEOUT_SECONDS = int(os.getenv("HITL_TIMEOUT_SECONDS", "300"))
DEDUP_WINDOW_SECONDS = int(os.getenv("DEDUP_WINDOW_SECONDS", "300"))  # 5 minutes

# ---------------------------------------------------------------------------
# Severity thresholds (used by the Triage agent to map signal -> P1..P4)
# ---------------------------------------------------------------------------
SEVERITY_LEVELS = ["P1", "P2", "P3", "P4"]

SEVERITY_RULES = {
    # keyword found in alert title/description -> minimum severity
    "oomkilled": "P1",
    "deadlock": "P1",
    "data loss": "P1",
    "outage": "P1",
    "down": "P1",
    "timeout cascade": "P1",
    "exhausted": "P1",
    "lock_timeout": "P1",
    "error rate": "P2",
    "disk": "P2",
    "eviction": "P2",
    "throttl": "P2",
    "latency": "P3",
    "warning": "P4",
}

# ---------------------------------------------------------------------------
# Safe vs Destructive action classification
#
# This list is intentionally centralized and imported by BOTH the
# remediation agent (to classify actions at proposal time) AND the AIMS
# logger (to independently re-verify before anything is logged as
# "executed"). Defense in depth: two independent checks, same source list.
# ---------------------------------------------------------------------------
DESTRUCTIVE_KEYWORDS = [
    "DELETE",
    "DROP",
    "REBOOT",
    "DRAIN",
    "TERMINATE",
    "KILL",
    "TRUNCATE",
    "SCALE-DOWN-TO-ZERO",
    "CORDON",
    "PG_TERMINATE_BACKEND",
    "SHUTDOWN",
    "FORCE",
]

SAFE_ACTION_KEYWORDS = [
    "ROLLOUT RESTART",
    "SCALE UP",
    "ROLLBACK",
    "ROLLING RESTART",
    "APPLY CONFIG",
    "ADD REPLICA",
]

# ---------------------------------------------------------------------------
# Agent system prompts
#
# Moved to agents/prompt_templates.py (the single source of truth for
# prompt content, per the "Prompt Architecture" evaluation checkpoint):
# TRIAGE_SYSTEM_PROMPT, DIAGNOSTICIAN_SYSTEM_PROMPT, REMEDIATION_SYSTEM_PROMPT,
# POSTMORTEM_SYSTEM_PROMPT. Import from there, not from this module.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
SERVICE_NAME = "governed-sre-agent"
AIMS_LOCAL_FALLBACK_PATH = os.getenv("AIMS_LOCAL_FALLBACK_PATH", "aims_events.log.jsonl")
