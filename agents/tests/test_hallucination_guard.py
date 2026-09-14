"""
agents/tests/test_hallucination_guard.py

Unit tests for the 3-layer hallucination guard (agents/hallucination_guard.py):
schema validation, hedge-language signal detection, and log-citation grounding.
"""
from __future__ import annotations

from pydantic import BaseModel

from agents.hallucination_guard import (
    check_hallucination_signals,
    validate_agent_output,
    validate_log_citations,
)


# ---------------------------------------------------------------------------
# check_hallucination_signals
# ---------------------------------------------------------------------------
def test_check_hallucination_signals_detects_i_think():
    found = check_hallucination_signals("I think the root cause is a memory leak.")
    assert "I think" in found


def test_check_hallucination_signals_detects_probably():
    found = check_hallucination_signals("This is probably caused by a bad deploy.")
    assert "probably" in found


def test_check_hallucination_signals_detects_might_be():
    found = check_hallucination_signals("The issue might be related to disk pressure.")
    assert "might be" in found


def test_check_hallucination_signals_is_case_insensitive():
    found = check_hallucination_signals("I THINK this is the cause.")
    assert "I think" in found


def test_clean_output_returns_empty_list():
    clean_text = (
        "Memory leak in the connection pool caused the container to be "
        "OOMKilled after exceeding its 512Mi limit."
    )
    assert check_hallucination_signals(clean_text) == []


# ---------------------------------------------------------------------------
# validate_log_citations
# ---------------------------------------------------------------------------
def test_validate_log_citations_with_valid_citation_passes():
    corpus = [
        "2026-01-01T00:00:00 ERROR payment-service OOMKilled after exceeding memory limit",
        "2026-01-01T00:00:05 WARN  payment-service connection pool leak detected",
    ]
    valid, invalid = validate_log_citations(
        ["2026-01-01T00:00:00 ERROR payment-service OOMKilled after exceeding memory limit"],
        corpus,
    )
    assert valid is True
    assert invalid == []


def test_validate_log_citations_with_fabricated_citation_fails():
    corpus = [
        "2026-01-01T00:00:00 ERROR payment-service OOMKilled after exceeding memory limit",
    ]
    valid, invalid = validate_log_citations(
        ["2026-01-01T00:00:00 ERROR payment-service disk fully corrupted, RAID failure imminent"],
        corpus,
    )
    assert valid is False
    assert len(invalid) == 1
    assert "RAID failure" in invalid[0]


# ---------------------------------------------------------------------------
# validate_agent_output
# ---------------------------------------------------------------------------
class _DummyEvidence(BaseModel):
    log_line: str


class _DummySchema(BaseModel):
    cause: str
    evidence: list[_DummyEvidence] = []


def test_validate_agent_output_with_valid_schema_passes():
    output = _DummySchema(cause="Memory leak in connection pool.", evidence=[])
    validated, report = validate_agent_output(output, _DummySchema, agent_name="TestAgent")
    assert report["schema_valid"] is True
    assert report["passed"] is True
    assert report["agent"] == "TestAgent"


def test_validate_agent_output_with_invalid_schema_fails():
    # Missing the required "cause" field -- schema construction must fail.
    bad_output = {"not_a_real_field": 123}
    validated, report = validate_agent_output(bad_output, _DummySchema, agent_name="TestAgent")
    assert report["schema_valid"] is False
    assert report["passed"] is False


def test_validate_agent_output_flags_hallucination_signal():
    output = _DummySchema(cause="I think this might be a memory leak.", evidence=[])
    _, report = validate_agent_output(output, _DummySchema, agent_name="TestAgent")
    assert report["schema_valid"] is True
    assert len(report["hallucination_signals"]) > 0
    assert report["passed"] is False


def test_validate_agent_output_flags_ungrounded_citation():
    output = _DummySchema(
        cause="Memory leak.",
        evidence=[_DummyEvidence(log_line="a line that does not exist in the corpus")],
    )
    log_corpus = ["2026-01-01T00:00:00 ERROR payment-service OOMKilled"]
    _, report = validate_agent_output(output, _DummySchema, log_corpus=log_corpus, agent_name="TestAgent")
    assert report["grounding_valid"] is False
    assert report["passed"] is False
