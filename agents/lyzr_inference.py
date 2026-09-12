"""
LAYER 3: INFERENCE
==================
Lyzr Agent Studio -- Inference Execution
studio.lyzr.ai | Lyzr Agent API: POST /agent/{id}/chat

The Inference layer executes agent calls with:
- Session ID for cross-agent state persistence (SHORT_TERM_MEMORY)
- Token counting and latency tracking
- Hallucination guard validation on every output (when an output_schema
  is supplied)
- AIMS event logging for every inference call
- Automatic fallback to a caller-supplied local simulation whenever no
  API key is configured, the SDK call fails, or the real output doesn't
  validate against the expected schema

This is tier 3 (final tier) of the Lyzr Agent API architecture:
  Environment (lyzr_environment.py) -> Agent (lyzr_agents.py) -> Inference (this file)

The INCIDENT SESSION ID is shared across all 4 agent calls for one incident.
This implements state persistence: each agent can see what prior agents produced.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, List, Optional, Tuple

from agents.hallucination_guard import validate_agent_output
from agents.lyzr_agents import SREAgent, sre_environment
from agents.metrics_tracker import estimate_tokens, tracker
from agents.schemas import AIMSEvent
from backend.aims_logger import log_event as log_aims_event

logger = logging.getLogger(__name__)


def run_inference(
    agent: SREAgent,
    prompt: str,
    incident_id: str,
    fallback_fn: Callable[[], str],
    output_schema: Optional[type] = None,
    log_corpus: Optional[List[str]] = None,
) -> Tuple[Any, dict]:
    """
    Execute an inference call on a Lyzr Agent Studio agent.

    This is the single entry point for ALL agent calls in the SRE pipeline.
    Every call goes through: metrics tracking -> inference -> schema
    validation + hallucination guard -> AIMS log.

    Args:
        agent: SREAgent instance (from lyzr_agents.py)
        prompt: The user message / task prompt
        incident_id: Shared session ID for state persistence across agents
        fallback_fn: Zero-arg callable returning a JSON string -- the
            deterministic local-simulation output used whenever the real
            agent is unavailable, fails, or its output doesn't validate
        output_schema: Pydantic model to parse/validate the output against.
            If None, the raw text is returned as-is (no parsing).
        log_corpus: Log lines for grounding validation (Diagnostician only)

    Returns:
        (result, inference_metadata) -- `result` is a validated instance of
        `output_schema` (or the raw string if no schema given).
    """
    metrics = tracker.start_call(agent.name, incident_id)
    input_tokens = estimate_tokens(prompt)
    output_tokens = 0
    result: Any = None
    validation_report: Optional[dict] = None
    mode = "real" if agent.is_real else "simulation"

    try:
        raw_text: Optional[str] = None

        # -- Real Lyzr Agent Studio inference ---------------------------------
        if agent.is_real and agent.get_studio_agent():
            try:
                response = agent.get_studio_agent().run(prompt)
                raw_text = response.response if hasattr(response, "response") else str(response)
                logger.info(f"Lyzr Studio inference: {agent.name} -> {len(str(raw_text))} chars")
            except Exception as e:
                logger.error(f"Lyzr Studio inference failed for {agent.name}: {e}", exc_info=True)

        # -- Simulation fallback -----------------------------------------------
        if raw_text is None:
            raw_text = fallback_fn()
            mode = "simulation"

        if output_schema is not None:
            try:
                result = output_schema(**json.loads(raw_text))
            except Exception:
                # Real output didn't parse/validate -- fall back to the
                # deterministic local simulation rather than propagate
                # garbage into the pipeline.
                raw_text = fallback_fn()
                result = output_schema(**json.loads(raw_text))
                mode = "simulation"

            result, validation_report = validate_agent_output(
                result, output_schema, log_corpus, agent.name
            )
            if not validation_report["passed"]:
                logger.warning(
                    f"Validation issues for {agent.name}: "
                    f"signals={validation_report['hallucination_signals']}"
                )
        else:
            result = raw_text

        output_tokens = estimate_tokens(raw_text)

    except Exception as e:
        logger.error(f"Inference failed for {agent.name}: {e}", exc_info=True)
        try:
            raw_text = fallback_fn()
            result = output_schema(**json.loads(raw_text)) if output_schema else raw_text
            output_tokens = estimate_tokens(raw_text)
            mode = "simulation"
        except Exception:
            result = None

    completed = tracker.end_call(metrics, input_tokens, output_tokens)

    inference_meta = {
        "agent_name": agent.name,
        "agent_id": agent.agent_id,
        "environment_id": sre_environment.environment_id,
        "incident_id": incident_id,
        "session_id": incident_id,  # Session = incident, for state persistence
        "mode": mode,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "validation": validation_report,
        "latency_ms": round(completed.latency_ms, 1),
        "input_tokens": completed.input_tokens,
        "output_tokens": completed.output_tokens,
        "total_tokens": completed.total_tokens,
        "cost_usd": completed.cost_usd,
    }

    # -- AIMS audit log --------------------------------------------------------
    result_str = str(result)
    log_aims_event(
        AIMSEvent(
            incident_id=incident_id,
            agent_name=agent.name,
            action="INFERENCE",
            input_summary=(prompt[:200] + "...") if len(prompt) > 200 else prompt,
            output_summary=(result_str[:200] + "...") if len(result_str) > 200 else result_str,
            tokens_used=inference_meta["total_tokens"],
            latency_ms=inference_meta["latency_ms"],
            blocked=bool(validation_report and not validation_report.get("passed", True)),
            metadata={
                "mode": mode,
                "environment_id": sre_environment.environment_id,
                "session_id": incident_id,
                "agent_id": agent.agent_id,
            },
        )
    )

    return result, inference_meta
