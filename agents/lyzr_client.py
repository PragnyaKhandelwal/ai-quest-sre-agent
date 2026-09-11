"""
agents/lyzr_client.py

Real Lyzr ADK (lyzr-adk PyPI package, `import lyzr`) integration point.

# Lyzr ADK: Environment/Agent/Inference pattern
- Environment: the `Studio` instance is this process's Lyzr environment --
  it owns the connection and every agent registered against it.
- Agent: each pipeline stage (triage / diagnostician / remediation /
  postmortem) registers its own `Studio.create_agent(...)` with its own
  role/goal/instructions -- see the module-level `_*_lyzr_agent` handles
  created in each agents/*_agent.py file.
- Inference: `agent.run(prompt)` is the inference call. Every call site in
  this codebase goes through `run_lyzr_agent()` below, which always falls
  back to a deterministic local-simulation function on any failure (no key
  configured, SDK not installed, network error, malformed response) so the
  pipeline never crashes and never blocks on an external dependency.

If LYZR_API_KEY is unset, or the `lyzr` package/API call fails for any
reason, USE_REAL_LYZR is False and every agent call transparently uses its
local-simulation fallback instead -- the demo remains 100% functional with
zero external API keys, and the exact same code path activates the moment
a real key is supplied.
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

LYZR_API_KEY = os.getenv("LYZR_API_KEY", "")
USE_REAL_LYZR = bool(LYZR_API_KEY)

studio: Optional[object] = None

if USE_REAL_LYZR:
    try:
        from lyzr import Studio

        studio = Studio(api_key=LYZR_API_KEY)
        logger.info("Lyzr SDK initialized — real agent mode active")
    except Exception as e:
        USE_REAL_LYZR = False
        logger.warning(f"Lyzr SDK init failed, falling back to simulation: {e}")
else:
    logger.info("No LYZR_API_KEY — running in simulation mode")


def create_lyzr_agent(name: str, role: str, goal: str, instructions: str):
    """Create a Lyzr agent or return None (simulation-only handle).

    Wrapped in try/except: this runs at *module import time* in every
    agents/*_agent.py file (one module-level agent per pipeline stage), so
    a transient SDK/network failure here must never crash app startup.
    """
    if not USE_REAL_LYZR or studio is None:
        return None
    try:
        return studio.create_agent(
            name=name,
            provider="openai/gpt-4o-mini",
            role=role,
            goal=goal,
            instructions=instructions,
            temperature=0.1,  # Low temp for deterministic SRE decisions
        )
    except Exception as e:
        logger.warning(f"Lyzr create_agent('{name}') failed, will use simulation fallback: {e}")
        return None


def run_lyzr_agent(agent, fallback_fn, *args, **kwargs):
    """Run a real Lyzr agent if available, else fall back to a local function.

    `fallback_fn` is called with the same *args/**kwargs used to derive the
    prompt below, so callers typically pass a single positional prompt
    string and give fallback_fn a signature that accepts (and ignores) it,
    e.g. `lambda _prompt=None: json.dumps(_simulate(...))`.
    """
    if USE_REAL_LYZR and agent is not None:
        try:
            prompt = kwargs.get("prompt", args[0] if args else "")
            response = agent.run(prompt)
            return response.response
        except Exception as e:
            logger.error(f"Lyzr agent call failed: {e}, falling back")
    return fallback_fn(*args, **kwargs)
