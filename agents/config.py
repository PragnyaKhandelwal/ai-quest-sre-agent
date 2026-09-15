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

# backend.secrets.secrets is a layered resolver (HashiCorp Vault -> cloud
# provider secrets -> environment variables -> .env, loaded above) that
# replaces direct os.getenv() calls for actual credentials -- everything
# else below (URLs, thresholds) is non-secret config and stays on os.getenv.
from backend.secrets import secrets

# ---------------------------------------------------------------------------
# Lyzr platform configuration
# ---------------------------------------------------------------------------
LYZR_API_KEY = secrets.get("LYZR_API_KEY", "")
LYZR_BASE_URL = os.getenv("LYZR_BASE_URL", "https://agent.api.lyzr.ai/v2")
LYZR_AIMS_URL = os.getenv("LYZR_AIMS_URL", "https://aims.api.lyzr.ai/v1")
OPENAI_API_KEY = secrets.get("OPENAI_API_KEY", "")
GROQ_API_KEY = secrets.get("GROQ_API_KEY", "")

# If no Lyzr key is configured, the system runs fully in "local simulation"
# mode: every agent falls back to deterministic, schema-validated local
# reasoning instead of calling out to the Lyzr Agent API. This keeps the
# demo 100% functional with zero external dependencies / zero API cost,
# while the exact same code path is used the moment real keys are supplied.
#
# NOTE: this specifically gates the Lyzr Studio *connection*
# (agents/lyzr_environment.py's `Studio(api_key=LYZR_API_KEY)`), which is
# independent of LLM_PROVIDER/LLM_MODEL below -- LYZR_API_KEY controls
# whether real inference happens at all; LLM_PROVIDER controls which
# underlying model a real call would be routed to.
LYZR_ENABLED = bool(LYZR_API_KEY)

# ---------------------------------------------------------------------------
# Model / inference configuration
#
# LLM provider fallback chain for real (non-simulated) inference calls:
# OpenAI first, then Groq (OpenAI-API-compatible; fast LPU-hosted inference,
# and free vs. OpenAI's paid API), else local simulation only.
#
# GROQ MODEL NAMING (two different conventions -- confirmed by hitting both
# APIs directly with a real key, not assumed):
#   - LLM_MODEL is what agents/lyzr_agents.py puts into the Lyzr Agent
#     Studio `provider` string ("groq/<LLM_MODEL>"). Lyzr Studio validates
#     this against its OWN accepted-model list (surfaced in a live
#     create_agent() error), which includes "llama-3.1-8b-instant" --
#     Lyzr evidently maintains its own mapping independent of Groq's live
#     catalog.
#   - GROQ_DIRECT_MODEL is what agents/automata_pipeline.py's direct
#     OpenAI-compatible client sends straight to Groq's own REST API
#     (https://api.groq.com/openai/v1). Verified live against
#     https://api.groq.com/openai/v1/models: "llama-3.1-8b-instant" is
#     NOT in that catalog (404 model_not_found) -- Groq's own namespaced
#     "openai/gpt-oss-20b" is. Recheck that endpoint with your own key if
#     this ever starts failing again; Groq's catalog changes over time.
# ---------------------------------------------------------------------------
MODEL = os.getenv("MODEL", "gpt-4o-mini")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "1000"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.2"))

if OPENAI_API_KEY:
    LLM_PROVIDER = "openai"
    LLM_MODEL = "gpt-4o-mini"
    GROQ_DIRECT_MODEL = None  # unused -- OpenAI is active
elif GROQ_API_KEY:
    LLM_PROVIDER = "groq"
    LLM_MODEL = "llama-3.1-8b-instant"
    GROQ_DIRECT_MODEL = "openai/gpt-oss-20b"
else:
    # No underlying LLM key at all -- this label is inert unless
    # LYZR_API_KEY is also set (see note above), but keep a sane default
    # rather than "none" so a real create_agent() call never gets a
    # malformed provider string.
    LLM_PROVIDER = "openai"
    LLM_MODEL = "gpt-4o-mini"
    GROQ_DIRECT_MODEL = None

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
