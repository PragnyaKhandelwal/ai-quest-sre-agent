"""
LAYER 1: ENVIRONMENT
====================
Lyzr Agent Studio -- Environment Setup
studio.lyzr.ai | Lyzr Agent API: POST /environment

The Environment defines the operational space for all SRE agents:
- Which tools they can call
- Which features are enabled (SHORT_TERM_MEMORY, AIMS logging)
- Safety policies (Safe AI guardrails)
- Shared context across all agents in the pipeline

This is the first of three layers in the Lyzr Agent API architecture:
  Environment (this file) -> Agent (lyzr_agents.py) -> Inference (lyzr_inference.py)
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

LYZR_API_KEY = os.getenv("LYZR_API_KEY", "")
LYZR_BASE_URL = "https://agent.api.lyzr.ai/v2"

# -- Lyzr Agent Studio SDK ----------------------------------------------------
# Lyzr Agent Studio (studio.lyzr.ai) is the visual and SDK interface
# for creating governed agents. The Studio SDK (lyzr-adk package, `import
# lyzr`) wraps the Agent API.
try:
    from lyzr import Studio

    _studio = Studio(api_key=LYZR_API_KEY) if LYZR_API_KEY else None
    STUDIO_AVAILABLE = _studio is not None
    if STUDIO_AVAILABLE:
        logger.info("Lyzr Agent Studio initialized via SDK (studio.lyzr.ai)")
    else:
        logger.info("Lyzr Agent Studio: no API key — simulation mode")
except Exception as e:
    _studio = None
    STUDIO_AVAILABLE = False
    logger.warning(f"Lyzr SDK not available: {e}")


class SREEnvironment:
    """
    Lyzr Environment for the SRE Incident Response mesh.

    Encapsulates:
    - Tool access (log_query, metrics_query, kubectl_safe, alert_correlation)
    - Feature flags (SHORT_TERM_MEMORY for cross-agent state)
    - Safe AI policy (blocks destructive mutations without HITL)
    - AIMS logging (all commands/queries/outputs logged chronologically)

    One environment shared by all 4 SRE agents. This ensures consistent
    tool access, safety policies, and audit trails across the pipeline.
    """

    _instance: Optional["SREEnvironment"] = None

    def __init__(self):
        self.environment_id: Optional[str] = None
        self.environment_name = "SRE-Incident-Response-Environment"
        self.studio = _studio
        self.is_real = STUDIO_AVAILABLE
        self._setup()

    def _setup(self):
        """Initialize the Lyzr Environment via Agent Studio SDK or API."""
        if self.is_real and self.studio:
            try:
                logger.info(f"Creating Lyzr Environment: {self.environment_name}")
                # Studio SDK handles environment implicitly per agent;
                # environment config is passed at agent creation time.
                self.environment_id = f"env-sre-{os.getenv('RENDER_SERVICE_ID', 'local')}"
                logger.info(f"Lyzr Environment ready: {self.environment_id}")
            except Exception as e:
                logger.error(f"Environment setup failed: {e}")
                self.is_real = False
                self.environment_id = "env-sre-simulation"
        else:
            self.environment_id = "env-sre-simulation"
            logger.info(f"Simulation environment: {self.environment_id}")

    @property
    def config(self) -> dict:
        """Environment configuration -- shared across all agents."""
        return {
            "environment_id": self.environment_id,
            "environment_name": self.environment_name,
            "features": [
                "SHORT_TERM_MEMORY",  # Cross-agent state persistence
                "AIMS_LOGGING",  # Chronological audit trail
                "SAFE_AI_GUARDRAILS",  # Block unsafe infra mutations
                "HALLUCINATION_CONTROL",  # Validate outputs before use
            ],
            "tools": [
                "query_logs",
                "query_metrics",
                "kubectl_safe",
                "correlate_alerts",
            ],
            "safety_policy": {
                "block_keywords": [
                    "DELETE", "DROP", "REBOOT", "DRAIN",
                    "TERMINATE", "KILL", "TRUNCATE", "PG_TERMINATE_BACKEND",
                ],
                "require_hitl_for_destructive": True,
                "max_confidence_to_auto_execute": 0.95,
            },
            "model_config": {
                "model": "gpt-4o-mini",
                "temperature": 0.1,
                "max_tokens": 800,
            },
            "mode": "real" if self.is_real else "simulation",
        }

    def get_studio(self):
        """Return the Agent Studio SDK instance."""
        return self.studio

    @classmethod
    def get_instance(cls) -> "SREEnvironment":
        """Singleton -- one environment per deployment."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


# Module-level singleton
sre_environment = SREEnvironment.get_instance()
