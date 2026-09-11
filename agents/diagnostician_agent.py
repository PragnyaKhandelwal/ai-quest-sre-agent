"""
agents/diagnostician_agent.py

Agent 2: Root Cause Diagnostician.

Given a TriageResult and a mock log corpus for the affected service, returns
a structured DiagnosisHypothesis. Confidence is always grounded in how many
independent log lines actually support the hypothesis -- the local
simulation path NEVER invents a log line that isn't in the corpus it was
given, satisfying the "no hallucinated evidence" safety requirement even
when Lyzr is not configured.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Tuple

from agents import config
from agents.lyzr_client import client
from agents.schemas import DiagnosisHypothesis, Evidence, TriageResult

AGENT_NAME = "RootCauseDiagnosticianAgent"


@dataclass
class CauseHypothesis:
    cause: str
    keywords: List[str]
    affected_components: List[str]


# Known cause library used by the local-simulation heuristic. Each entry's
# keywords are matched case-insensitively against the actual log corpus
# supplied for the incident -- nothing here is asserted unless it is found
# verbatim in the logs.
_CAUSE_LIBRARY: List[CauseHypothesis] = [
    CauseHypothesis(
        cause=(
            "Memory leak in the database connection pool: connections are "
            "not being released back to the pool, causing steady heap "
            "growth until the container is OOMKilled."
        ),
        keywords=["oomkilled", "memory", "heap", "connection pool", "outofmemory", "rss"],
        affected_components=["connection-pool", "payment-service pods"],
    ),
    CauseHypothesis(
        cause=(
            "Bad container image deployed (missing required environment "
            "variable) causing the new revision to fail requests at a high "
            "rate immediately after rollout."
        ),
        keywords=["env var", "environment variable", "image", "v2.3.1", "rollout", "5xx", "error rate"],
        affected_components=["api-gateway deployment", "ingress"],
    ),
    CauseHypothesis(
        cause=(
            "Deadlock on the orders table caused by a long-running "
            "transaction holding row locks, cascading into query timeouts "
            "and connection pool exhaustion."
        ),
        keywords=["deadlock", "lock_timeout", "long-running transaction", "query timeout", "connection pool exhausted"],
        affected_components=["postgres-primary", "orders table", "order-service"],
    ),
    CauseHypothesis(
        cause=(
            "Node disk usage reached capacity because audit/log rotation "
            "was never configured, filling the disk with logging-agent "
            "output and triggering pod evictions."
        ),
        keywords=["disk usage", "eviction", "logrotate", "no space left", "audit log", "98%"],
        affected_components=["worker-3 node", "logging-agent"],
    ),
]


def _score_hypothesis(hyp: CauseHypothesis, logs: List[str]) -> Tuple[float, List[Evidence]]:
    evidence: List[Evidence] = []
    matched_keywords = set()
    for i, line in enumerate(logs):
        low = line.lower()
        for kw in hyp.keywords:
            if kw in low and kw not in matched_keywords:
                matched_keywords.add(kw)
                evidence.append(
                    Evidence(log_line=line.strip(), line_number=i + 1, relevance=f"matches '{kw}'")
                )
    confidence = min(0.95, 0.15 + 0.16 * len(matched_keywords))
    return confidence, evidence


def _simulate(triage: TriageResult, logs: List[str]) -> Dict:
    best_hyp = None
    best_conf = -1.0
    best_evidence: List[Evidence] = []
    for hyp in _CAUSE_LIBRARY:
        conf, evidence = _score_hypothesis(hyp, logs)
        if conf > best_conf:
            best_conf, best_hyp, best_evidence = conf, hyp, evidence

    if best_hyp is None or not best_evidence:
        return {
            "incident_id": triage.incident_id,
            "cause": "Insufficient log evidence to determine a root cause.",
            "confidence": 0.2,
            "evidence": [],
            "affected_components": triage.affected_services,
            "requires_human_review": True,
            "reasoning": "No log lines in the supplied corpus matched any known failure signature.",
        }

    requires_review = best_conf < config.CONFIDENCE_THRESHOLD
    return {
        "incident_id": triage.incident_id,
        "cause": best_hyp.cause,
        "confidence": round(best_conf, 2),
        "evidence": [e.model_dump() for e in best_evidence],
        "affected_components": best_hyp.affected_components,
        "requires_human_review": requires_review,
        "reasoning": (
            f"Matched {len(best_evidence)} independent log line(s) against known "
            f"failure signature. Confidence scales with number of corroborating "
            f"log lines actually present in the corpus."
        ),
    }


def run_diagnosis(
    triage: TriageResult, log_corpus: List[str]
) -> Tuple[DiagnosisHypothesis, int, float]:
    try:
        user_message = json.dumps(
            {
                "triage_summary": triage.summary,
                "affected_services": triage.affected_services,
                "log_corpus": log_corpus,
            }
        )

        result = client.run_inference(
            agent_key="diagnostician",
            agent_name=AGENT_NAME,
            system_prompt=config.DIAGNOSTICIAN_SYSTEM_PROMPT,
            user_message=user_message,
            session_id=triage.incident_id,
            simulate_fn=lambda: _simulate(triage, log_corpus),
        )

        try:
            payload = json.loads(result.text)
            diagnosis = DiagnosisHypothesis(**payload)
        except Exception:
            diagnosis = DiagnosisHypothesis(**_simulate(triage, log_corpus))

        # Second, independent enforcement of the confidence gate -- even if
        # the model (or the simulation) forgot to set the flag, we set it
        # here based on the centrally configured threshold.
        if diagnosis.confidence < config.CONFIDENCE_THRESHOLD:
            diagnosis.requires_human_review = True

        return diagnosis, result.tokens_used, result.latency_ms

    except Exception as exc:  # pragma: no cover
        fallback = DiagnosisHypothesis(
            incident_id=triage.incident_id,
            cause=f"Diagnostician agent failed ({exc}).",
            confidence=0.0,
            evidence=[],
            affected_components=triage.affected_services,
            requires_human_review=True,
            reasoning="Fallback path triggered due to internal error.",
        )
        return fallback, 0, 0.0
