"""
LAYER 2: AGENTS
===============
Lyzr Agent Studio -- Agent Configuration
studio.lyzr.ai | Lyzr Agent API: POST /agent

Each agent is configured with:
- A specific role and persona
- Links to the shared SRE Environment (tier 1)
- System prompt with defensive guardrails
- Tool access scoped to its role

Four agents in the SRE mesh:
  1. TriageAgent      -- clusters alerts, assigns P1-P4 severity
  2. DiagnosticAgent  -- queries logs, identifies root cause
  3. RemediationAgent -- plans safe runbooks, gates destructive actions
  4. PostMortemAgent  -- writes blameless RCAs, logs to AIMS

This is tier 2 of the Lyzr Agent API architecture:
  Environment (lyzr_environment.py) -> Agents (this file) -> Inference (lyzr_inference.py)
"""
import logging

from agents.lyzr_environment import sre_environment
from agents.prompt_templates import (
    DIAGNOSTICIAN_SYSTEM_PROMPT,
    POSTMORTEM_SYSTEM_PROMPT,
    REMEDIATION_SYSTEM_PROMPT,
    TRIAGE_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

studio = sre_environment.get_studio()
env_config = sre_environment.config


class SREAgent:
    """
    A governed SRE agent created via Lyzr Agent Studio.
    Wraps the Studio SDK agent with metadata for the AIMS audit trail.
    """

    def __init__(self, name: str, role: str, goal: str, instructions: str):
        self.name = name
        self.role = role
        self.goal = goal
        self.instructions = instructions
        self.agent_id = f"agent-{name.lower().replace(' ', '-')}"
        self._studio_agent = None
        self._initialize()

    def _initialize(self):
        """Create the agent via Lyzr Agent Studio SDK."""
        if studio and sre_environment.is_real:
            try:
                self._studio_agent = studio.create_agent(
                    name=self.name,
                    provider=f"openai/{env_config['model_config']['model']}",
                    role=self.role,
                    goal=self.goal,
                    instructions=self.instructions,
                    temperature=env_config["model_config"]["temperature"],
                )
                self.agent_id = getattr(self._studio_agent, "id", self.agent_id)
                logger.info(f"Lyzr Agent Studio: created agent '{self.name}' [{self.agent_id}]")
            except Exception as e:
                logger.warning(f"Studio agent creation failed for {self.name}: {e}")
                self._studio_agent = None
        else:
            logger.info(f"Simulation agent: '{self.name}' [{self.agent_id}]")

    @property
    def is_real(self) -> bool:
        return self._studio_agent is not None

    def get_studio_agent(self):
        return self._studio_agent

    def __repr__(self):
        mode = "REAL" if self.is_real else "SIMULATION"
        return f"SREAgent({self.name}, {mode}, env={sre_environment.environment_id})"


# -- Agent Definitions --------------------------------------------------------
# All 4 agents share the SRE Environment but have distinct roles and goals.

TRIAGE_AGENT = SREAgent(
    name="SRE-Triage-Agent",
    role="Senior SRE Triage Specialist",
    goal=(
        "Rapidly cluster and prioritize incoming alerts to focus the team "
        "on the highest-severity incidents first, eliminating noise and duplicates."
    ),
    instructions=TRIAGE_SYSTEM_PROMPT,
)

DIAGNOSTIC_AGENT = SREAgent(
    name="SRE-Diagnostic-Agent",
    role="Root Cause Analysis Expert",
    goal=(
        "Accurately identify the root cause of incidents from logs and metrics, "
        "with cited evidence and calibrated confidence scores. Never speculate."
    ),
    instructions=DIAGNOSTICIAN_SYSTEM_PROMPT,
)

REMEDIATION_AGENT = SREAgent(
    name="SRE-Remediation-Agent",
    role="Safe Infrastructure Remediation Planner",
    goal=(
        "Propose the safest possible runbook to resolve incidents. "
        "Automatically gate all destructive actions behind human approval. "
        "Include rollback steps for every action."
    ),
    instructions=REMEDIATION_SYSTEM_PROMPT,
)

POSTMORTEM_AGENT = SREAgent(
    name="SRE-PostMortem-Agent",
    role="Blameless Post-Mortem Facilitator",
    goal=(
        "Produce clear, blameless, actionable RCAs that prevent recurrence. "
        "Log the complete audit trail to Lyzr AIMS."
    ),
    instructions=POSTMORTEM_SYSTEM_PROMPT,
)

# Registry for lookup and status reporting
AGENT_REGISTRY = {
    "triage": TRIAGE_AGENT,
    "diagnostic": DIAGNOSTIC_AGENT,
    "remediation": REMEDIATION_AGENT,
    "postmortem": POSTMORTEM_AGENT,
}

logger.info(
    f"SRE Agent mesh initialized: {len(AGENT_REGISTRY)} agents | "
    f"Environment: {sre_environment.environment_id} | "
    f"Mode: {env_config['mode']}"
)
