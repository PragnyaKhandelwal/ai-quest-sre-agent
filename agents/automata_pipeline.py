"""
Lyzr Automata Pipeline -- the official multi-agent orchestration layer.

Uses lyzr-automata's LinearSyncPipeline with Agent + Task + Tool pattern.
This is the governed SRE mesh described in the problem statement.

Architecture:
  TriageAgent -> DiagnosticAgent -> RemediationAgent -> PostMortemAgent
  Each agent is a node in the pipeline with explicit input/output contracts.

This module is deliberately independent of agents/pipeline.py's own
orchestration: agents/pipeline.py is what actually drives the incident
through the 4 governed agents (with hallucination guard, retrieval,
metrics, HITL, etc.) and is what backend/main.py calls for every
/simulate and /alerts/ingest request. This module demonstrates the
`lyzr-automata` package explicitly (LinearSyncPipeline / Agent / Task /
OpenAIModel), as required by the problem statement, and is invoked
alongside the main pipeline to record which orchestration mode is active
for the incident (real Automata run vs. simulation) -- see
IncidentState.pipeline_metadata.
"""

import logging
from typing import Any, Optional

from agents import config

logger = logging.getLogger(__name__)

# -- Lyzr Automata imports ---------------------------------------------------
try:
    from lyzr_automata.agents.agent_base import Agent as AutomataAgent
    from lyzr_automata.ai_models.openai import OpenAIModel
    from lyzr_automata.pipelines.linear_sync_pipeline import LinearSyncPipeline
    from lyzr_automata.tasks.task_base import Task as AutomataTask
    from lyzr_automata.tasks.task_literals import InputType, OutputType

    AUTOMATA_AVAILABLE = bool(config.OPENAI_API_KEY or config.GROQ_API_KEY)
    if AUTOMATA_AVAILABLE:
        logger.info(f"Lyzr Automata initialized — pipeline mode active ({config.LLM_PROVIDER})")
    else:
        logger.info("Lyzr Automata available but no OPENAI_API_KEY/GROQ_API_KEY — simulation mode")

    class _OpenAICompatibleModel(OpenAIModel):
        """OpenAIModel subclass that supports pointing the underlying OpenAI
        SDK client at an OpenAI-API-compatible endpoint (e.g. Groq) via
        base_url.

        lyzr_automata.ai_models.openai.OpenAIModel (as of lyzr-automata==0.1.1)
        hardcodes `self.client = OpenAI(api_key=api_key)` with no base_url
        support, and forwards `self.parameters` verbatim into
        `client.chat.completions.create(**self.parameters, ...)`. A raw
        `base_url` key left inside `parameters` would therefore either be
        silently dropped or raise a TypeError at inference time -- this
        subclass pops it out and uses it to configure the client instead,
        so Groq (or any other OpenAI-compatible provider) actually works.
        """

        def __init__(self, api_key: str, parameters: dict, base_url: Optional[str] = None):
            from openai import OpenAI

            params = dict(parameters)
            params.pop("base_url", None)
            self.parameters = params
            self.client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)

except ImportError:
    AUTOMATA_AVAILABLE = False
    logger.warning("lyzr-automata not installed — running simulation pipeline")


def build_automata_pipeline(incident_context: dict) -> Optional[Any]:
    """
    Build a Lyzr Automata LinearSyncPipeline for SRE incident response.

    Pipeline nodes:
      Task 1: Triage & Deduplication
      Task 2: Root Cause Diagnosis
      Task 3: Safe Remediation Planning
      Task 4: Blameless Post-Mortem RCA
    """
    if not AUTOMATA_AVAILABLE:
        return None

    try:
        # -- Model --------------------------------------------------------------
        # OpenAI first; Groq (OpenAI-API-compatible) as a fallback when no
        # OpenAI key is configured -- see agents/config.py for the priority
        # chain and the Latency Optimization rationale.
        if config.OPENAI_API_KEY:
            openai_model = OpenAIModel(
                api_key=config.OPENAI_API_KEY,
                parameters={
                    "model": config.LLM_MODEL,
                    "temperature": 0.1,  # Low temp for deterministic SRE decisions
                    "max_tokens": 800,  # Token optimized
                },
            )
        else:
            openai_model = _OpenAICompatibleModel(
                api_key=config.GROQ_API_KEY,
                parameters={
                    # NOT config.LLM_MODEL -- this calls Groq's raw REST API
                    # directly, which uses a different model-name convention
                    # than Lyzr Agent Studio's `provider` string. See
                    # agents/config.py for the full explanation.
                    "model": config.GROQ_DIRECT_MODEL,
                    "temperature": 0.1,
                    "max_tokens": 800,
                },
                base_url="https://api.groq.com/openai/v1",
            )

        # -- Agent 1: Triage ----------------------------------------------------
        triage_agent = AutomataAgent(
            prompt_persona=(
                "You are an expert SRE Triage Agent. "
                "You cluster related alerts, deduplicate by fingerprint, "
                "and classify severity P1-P4. Output only valid JSON. "
                "Never invent alert data not provided to you."
            ),
            role="SRE Triage Specialist",
        )

        # -- Agent 2: Diagnostician ----------------------------------------------
        diagnostic_agent = AutomataAgent(
            prompt_persona=(
                "You are an expert Root Cause Diagnostician. "
                "You analyze logs and metrics to identify root causes. "
                "Only cite log lines explicitly provided. Never speculate. "
                "Output confidence scores and evidence citations."
            ),
            role="Root Cause Analyst",
        )

        # -- Agent 3: Remediation Planner ----------------------------------------
        remediation_agent = AutomataAgent(
            prompt_persona=(
                "You are an SRE Remediation Planner. Safety first. "
                "Classify every action as SAFE or DESTRUCTIVE. "
                "DESTRUCTIVE actions (DELETE/DROP/REBOOT/DRAIN/TERMINATE/KILL) "
                "are NEVER auto-executed — they go to HITL approval queue. "
                "Include rollback commands for every action."
            ),
            role="Safe Remediation Planner",
        )

        # -- Agent 4: Post-Mortem Writer ------------------------------------------
        postmortem_agent = AutomataAgent(
            prompt_persona=(
                "You are a Blameless Post-Mortem Analyst. "
                "You write clear, actionable RCAs with timelines. "
                "Never blame individuals. Focus on systems and processes. "
                "Every recommendation must be specific and immediately actionable."
            ),
            role="Blameless Post-Mortem Writer",
        )

        # -- Tasks -----------------------------------------------------------
        alerts_json = str(incident_context.get("alerts", []))
        logs_json = str(incident_context.get("logs", [])[:20])  # Top 20 relevant

        task_triage = AutomataTask(
            name="Triage & Deduplication",
            agent=triage_agent,
            output_type=OutputType.TEXT,
            input_type=InputType.TEXT,
            model=openai_model,
            instructions=(
                f"Triage these alerts. Cluster related ones, deduplicate, "
                f"assign severity P1-P4, return JSON TriageResult.\n"
                f"ALERTS: {alerts_json}"
            ),
            log_output=True,
            enhance_prompt=False,
        )

        task_diagnose = AutomataTask(
            name="Root Cause Diagnosis",
            agent=diagnostic_agent,
            output_type=OutputType.TEXT,
            input_type=InputType.TEXT,
            model=openai_model,
            previous_output=task_triage,
            instructions=(
                f"Based on the triage result above and these logs, "
                f"identify the root cause. Return JSON DiagnosisHypothesis "
                f"with confidence score and cited evidence.\n"
                f"LOGS: {logs_json}"
            ),
            log_output=True,
            enhance_prompt=False,
        )

        task_remediate = AutomataTask(
            name="Safe Remediation Planning",
            agent=remediation_agent,
            output_type=OutputType.TEXT,
            input_type=InputType.TEXT,
            model=openai_model,
            previous_output=task_diagnose,
            instructions=(
                "Based on the diagnosis above, propose a safe runbook. "
                "Mark every action as SAFE or DESTRUCTIVE. "
                "DESTRUCTIVE actions require HITL approval before execution. "
                "Return JSON RunbookProposal."
            ),
            log_output=True,
            enhance_prompt=False,
        )

        task_postmortem = AutomataTask(
            name="Blameless Post-Mortem RCA",
            agent=postmortem_agent,
            output_type=OutputType.TEXT,
            input_type=InputType.TEXT,
            model=openai_model,
            previous_output=task_remediate,
            instructions=(
                "Generate a complete blameless RCA based on the full incident "
                "context above. Include timeline, root cause, contributing factors, "
                "and specific prevention recommendations. Return JSON RCAReport."
            ),
            log_output=True,
            enhance_prompt=False,
        )

        # -- Pipeline ----------------------------------------------------------
        pipeline = LinearSyncPipeline(
            name="SRE Incident Response Pipeline",
            completion_message="Incident pipeline complete — RCA generated.",
            tasks=[task_triage, task_diagnose, task_remediate, task_postmortem],
        )

        logger.info("Lyzr Automata pipeline built: 4 agents, LinearSyncPipeline")
        return pipeline

    except Exception as e:
        logger.error(f"Failed to build Automata pipeline: {e}", exc_info=True)
        return None


def run_automata_pipeline(incident_context: dict) -> dict:
    """
    Run the Lyzr Automata pipeline for an incident.
    Falls back to simulation pipeline if Automata unavailable.
    Returns dict with pipeline_used and task_outputs.
    """
    pipeline = build_automata_pipeline(incident_context)

    if pipeline is not None:
        try:
            result = pipeline.run()
            logger.info("Lyzr Automata pipeline completed successfully")
            return {
                "pipeline_used": "lyzr_automata",
                "pipeline_name": "SRE Incident Response Pipeline",
                "tasks_completed": 4,
                "result": str(result),
            }
        except Exception as e:
            logger.error(f"Automata pipeline run failed: {e}", exc_info=True)

    # Fallback: simulation pipeline
    logger.info("Using simulation pipeline (no API keys)")
    return {
        "pipeline_used": "simulation",
        "pipeline_name": "SRE Incident Response Pipeline (Simulation)",
        "tasks_completed": 4,
        "result": "Simulation mode — set OPENAI_API_KEY or GROQ_API_KEY to activate Lyzr Automata",
    }
