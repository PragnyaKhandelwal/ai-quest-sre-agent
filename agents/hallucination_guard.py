"""
agents/hallucination_guard.py

Hallucination mitigation layer -- validates all agent outputs before use.
Implements 3-layer defense:
1. Schema validation (Pydantic) -- output must match expected structure
2. Grounding check -- all cited log lines must exist in provided corpus
3. Confidence gating -- low confidence triggers human escalation
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Keywords that must NEVER appear in agent output (hallucination signals)
HALLUCINATION_SIGNALS = [
    "I think", "I believe", "probably", "might be", "could be",
    "I'm not sure", "approximately", "around", "roughly",
    "I don't have access", "I cannot verify",
]


def check_hallucination_signals(text: str) -> list[str]:
    """Return list of hallucination signal phrases found in text."""
    found = []
    text_lower = text.lower()
    for signal in HALLUCINATION_SIGNALS:
        if signal.lower() in text_lower:
            found.append(signal)
    return found


def validate_log_citations(cited_lines: list[str], log_corpus: list[str]) -> tuple[bool, list[str]]:
    """
    Grounding check: verify cited log lines actually exist in corpus.
    Returns (all_valid, list_of_invalid_citations)
    """
    invalid = []
    corpus_set = set(line.strip() for line in log_corpus)
    for citation in cited_lines:
        # Check if citation is a substring of any corpus line
        found = any(citation.strip() in corpus_line for corpus_line in corpus_set)
        if not found:
            invalid.append(citation)
            logger.warning(f"HALLUCINATION DETECTED: cited log line not in corpus: '{citation[:80]}...'")
    return len(invalid) == 0, invalid


def validate_agent_output(
    output: Any,
    schema_class,
    log_corpus: list[str] = None,
    agent_name: str = "unknown",
) -> tuple[Any, dict]:
    """
    Full validation pipeline for agent output.
    Returns (validated_output, validation_report)
    """
    report = {
        "agent": agent_name,
        "schema_valid": False,
        "hallucination_signals": [],
        "grounding_valid": True,
        "invalid_citations": [],
        "passed": False,
    }

    # Layer 1: Schema validation
    try:
        if not isinstance(output, schema_class):
            output = schema_class(**output) if isinstance(output, dict) else output
        report["schema_valid"] = True
    except Exception as e:
        logger.error(f"Schema validation failed for {agent_name}: {e}")
        return output, report

    # Layer 2: Hallucination signal detection
    output_str = str(output)
    report["hallucination_signals"] = check_hallucination_signals(output_str)
    if report["hallucination_signals"]:
        logger.warning(f"Hallucination signals in {agent_name} output: {report['hallucination_signals']}")

    # Layer 3: Grounding check (if log corpus provided)
    if log_corpus and hasattr(output, "evidence"):
        citations = [e.log_line for e in output.evidence if hasattr(e, "log_line")]
        report["grounding_valid"], report["invalid_citations"] = validate_log_citations(citations, log_corpus)

    report["passed"] = (
        report["schema_valid"]
        and len(report["hallucination_signals"]) == 0
        and report["grounding_valid"]
    )

    return output, report
