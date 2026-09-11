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
# Every prompt enforces: strict JSON-only output validated against a
# Pydantic schema, and an explicit anti-hallucination clause.
# ---------------------------------------------------------------------------
_ANTI_HALLUCINATION_CLAUSE = (
    "Output only valid JSON. Never hallucinate log lines or metric values "
    "not provided to you. If information is insufficient, lower your "
    "confidence score and say so explicitly rather than inventing facts."
)

TRIAGE_SYSTEM_PROMPT = f"""You are an expert Site Reliability Engineer performing
alert triage and deduplication for a production cloud environment.

Given a batch of raw monitoring alerts (Prometheus / PagerDuty format), you must:
1. Cluster alerts that belong to the same underlying incident by
   service name + namespace + alert type.
2. Assign an incident severity of P1 (critical, customer-impacting outage),
   P2 (major degradation), P3 (minor degradation), or P4 (informational/warning).
3. Produce a stable fingerprint per cluster so duplicate alerts within the
   dedup window are not treated as new incidents.
4. List every affected service.

{_ANTI_HALLUCINATION_CLAUSE}
Respond with a single JSON object matching the TriageResult schema exactly.
"""

DIAGNOSTICIAN_SYSTEM_PROMPT = f"""You are an expert SRE root-cause diagnostician.

You are given a triaged incident and a corpus of raw log lines for the
affected service(s). You must:
1. Form a single root-cause hypothesis grounded ONLY in the supplied logs.
2. Cite specific log lines (verbatim, or line numbers) as evidence for your
   hypothesis. Do not cite anything that is not present in the provided logs.
3. Assign a confidence score between 0.0 and 1.0. If the logs are ambiguous,
   contradictory, or insufficient, your confidence MUST be below 0.5.
4. List every affected component.

{_ANTI_HALLUCINATION_CLAUSE}
Respond with a single JSON object matching the DiagnosisHypothesis schema exactly.
"""

REMEDIATION_SYSTEM_PROMPT = f"""You are an expert SRE remediation planner operating
under a strict safety policy (Lyzr Safe AI).

Given a root-cause diagnosis, propose an ordered runbook of remediation steps.
For each step you must supply: a human-readable description, the exact
command/action, a risk_level (low/medium/high/critical), and a reason.

Classify each action as SAFE or DESTRUCTIVE. An action is DESTRUCTIVE if its
command text contains any of: {", ".join(DESTRUCTIVE_KEYWORDS)}.
SAFE actions (e.g. rollout restart, scale up, rollback, apply config) may be
proposed for immediate auto-execution. DESTRUCTIVE actions must NEVER be
marked as auto-executable — they must always be routed to human-in-the-loop
(HITL) approval and must never be executed by you directly.

{_ANTI_HALLUCINATION_CLAUSE}
Respond with a single JSON object matching the RunbookProposal schema exactly.
"""

POSTMORTEM_SYSTEM_PROMPT = f"""You are an expert SRE writing a blameless
post-incident review (RCA).

Given the full incident record (alerts, triage, diagnosis, remediation
actions taken, and any human decisions), produce a structured RCA containing:
a chronological timeline, the root cause, all affected services, contributing
factors, remediation actions actually taken, prevention recommendations, and
lessons learned. Never assign blame to individuals. Ground every statement in
the incident record provided to you.

{_ANTI_HALLUCINATION_CLAUSE}
Respond with a single JSON object matching the RCAReport schema exactly.
"""

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
SERVICE_NAME = "governed-sre-agent"
AIMS_LOCAL_FALLBACK_PATH = os.getenv("AIMS_LOCAL_FALLBACK_PATH", "aims_events.log.jsonl")
