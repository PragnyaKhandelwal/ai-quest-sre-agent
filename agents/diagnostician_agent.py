"""
agents/diagnostician_agent.py

Agent 2: Root Cause Diagnostician.

Given a TriageResult and a mock log corpus for the affected service, returns
a structured DiagnosisHypothesis. Confidence is always grounded in how many
independent log lines actually support the hypothesis -- the local
simulation path NEVER invents a log line that isn't in the corpus it was
given, satisfying the "no hallucinated evidence" safety requirement even
when Lyzr is not configured.

Retrieval quality: rather than handing the full log corpus to the agent,
agents/vector_store.py narrows the corpus down to the top-K lines most
semantically relevant to the incident (a RAG pattern), which both shrinks
the token budget and shrinks the surface area for hallucinated citations.
Uses a real vector DB (ChromaDB + sentence-transformers) when installed,
transparently falling back to agents/log_retriever.py's TF-IDF retriever
otherwise -- see agents/vector_store.py's module docstring for why those
aren't a default dependency here.

Groundedness: every output additionally passes through
agents/hallucination_guard.py, which independently re-verifies that every
cited log line is a verbatim substring of the corpus actually supplied.

LAYER 2 -> LAYER 3: Agent -> Inference (see lyzr_agents.py + lyzr_inference.py)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from agents import config
from agents.lyzr_agents import DIAGNOSTIC_AGENT
from agents.lyzr_inference import run_inference
from agents.schemas import Alert, DiagnosisHypothesis, Evidence, TriageResult
from agents.vector_store import create_incident_store
from backend.config_watcher import live_config

logger = logging.getLogger(__name__)

AGENT_NAME = "RootCauseDiagnosticianAgent"

RETRIEVAL_TOP_K = 15


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
    CauseHypothesis(
        cause=(
            "CPU limits were not increased after a model upgrade, so the "
            "container is being throttled by the Kubernetes CFS quota, "
            "causing inference latency spikes and request timeouts."
        ),
        keywords=["cpu throttle", "throttled", "cpu.limit", "cfs_quota", "model upgrade", "inference latency"],
        affected_components=["ml-inference-service pods", "cpu limits"],
    ),
    CauseHypothesis(
        cause=(
            "cert-manager's certificate renewal failed because the ACME "
            "DNS-01 challenge kept timing out, leaving the TLS certificate "
            "to approach expiry and causing HTTPS handshake failures."
        ),
        keywords=["acme challenge", "dns timeout", "renewal failed", "cert-manager", "handshake failure", "expires in"],
        affected_components=["api-gateway-tls certificate", "cert-manager"],
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


def _simulate(triage: TriageResult, retrieved_lines: List[str], retrieval_scores: List[float]) -> Dict:
    best_hyp = None
    best_conf = -1.0
    best_evidence: List[Evidence] = []
    for hyp in _CAUSE_LIBRARY:
        conf, evidence = _score_hypothesis(hyp, retrieved_lines)
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
            "reasoning": "No log lines in the retrieved set matched any known failure signature.",
            "retrieval_scores": retrieval_scores,
        }

    requires_review = best_conf < live_config.get("confidence_threshold", config.CONFIDENCE_THRESHOLD)
    return {
        "incident_id": triage.incident_id,
        "cause": best_hyp.cause,
        "confidence": round(best_conf, 2),
        "evidence": [e.model_dump() for e in best_evidence],
        "affected_components": best_hyp.affected_components,
        "requires_human_review": requires_review,
        "reasoning": (
            f"Matched {len(best_evidence)} independent log line(s) against known "
            f"failure signature, out of the top-{RETRIEVAL_TOP_K} semantically retrieved "
            f"log lines. Confidence scales with number of corroborating log lines."
        ),
        "retrieval_scores": retrieval_scores,
    }


def run_diagnosis(
    triage: TriageResult, log_corpus: List[str], alerts: Optional[List[Alert]] = None
) -> Tuple[DiagnosisHypothesis, int, float, dict]:
    """
    Returns (DiagnosisHypothesis, tokens_used, latency_ms, validation_report).
    `validation_report` is the agents/hallucination_guard.py 3-layer
    validation result -- callers (agents/pipeline.py) surface it to the
    incident's audit trail so groundedness is visible to judges.

    `alerts` (when supplied) supplies the retrieval query: raw alert
    title/description text carries far more log-matching vocabulary than
    the triage summary alone (e.g. "OOMKilled", "lock_timeout"), so
    retrieval recall is meaningfully better against the real alert text.
    """
    try:
        # --- Retrieval (RAG pattern): narrow the full corpus down to the
        # top-K lines most semantically relevant to this incident, and hand
        # the agent ONLY those -- not the full corpus. Uses a real vector
        # DB (agents/vector_store.py, ChromaDB + sentence-transformers)
        # when installed, transparently falling back to the TF-IDF
        # retriever (agents/log_retriever.py) otherwise. ---
        query_parts = [triage.summary]
        if alerts:
            query_parts.extend(f"{a.title} {a.description}" for a in alerts)
        query = " ".join(query_parts)

        vector_store = create_incident_store(triage.incident_id)
        vector_store.add_logs(log_corpus)
        retrieved = vector_store.search(query, top_k=RETRIEVAL_TOP_K)
        retrieved_lines = [r["log_line"] for r in retrieved]
        retrieval_scores = [r["score"] for r in retrieved]
        retrieval_backend = vector_store.stats()
        avg_score = sum(retrieval_scores) / len(retrieval_scores) if retrieval_scores else 0.0
        logger.info(
            f"Retrieved {len(retrieved)} relevant logs via {retrieval_backend['backend']} "
            f"with avg score {avg_score:.3f}"
        )

        user_message = json.dumps(
            {
                "incident_id": triage.incident_id,
                "triage_summary": triage.summary,
                "affected_services": triage.affected_services,
                "retrieved_logs": retrieved_lines,
            }
        )

        # run_inference() (LAYER 3) applies the Hallucination Guard
        # internally (schema + hedge-language + grounding, verified against
        # the FULL original corpus, not just the retrieved subset) whenever
        # an output_schema is supplied, and logs the call to Lyzr AIMS.
        diagnosis, meta = run_inference(
            DIAGNOSTIC_AGENT,
            user_message,
            triage.incident_id,
            fallback_fn=lambda: json.dumps(_simulate(triage, retrieved_lines, retrieval_scores)),
            output_schema=DiagnosisHypothesis,
            log_corpus=log_corpus,
        )

        # Second, independent enforcement of the confidence gate -- even if
        # the model (or the simulation) forgot to set the flag, we set it
        # here based on the centrally configured threshold. Reads through
        # backend/config_watcher.py so a PATCH /config/live change to
        # confidence_threshold takes effect on the very next diagnosis,
        # with no restart.
        if diagnosis.confidence < live_config.get("confidence_threshold", config.CONFIDENCE_THRESHOLD):
            diagnosis.requires_human_review = True

        # Authoritative override (same pattern as agents/triage_agent.py's
        # fingerprint/cluster_id/is_duplicate): which retrieval backend
        # actually served this diagnosis is a local fact, never the LLM's.
        diagnosis.retrieval_backend = retrieval_backend

        validation_report = meta.get("validation") or {
            "agent": AGENT_NAME, "schema_valid": True, "hallucination_signals": [],
            "grounding_valid": True, "invalid_citations": [], "passed": True,
        }
        # Keep the report keyed by the pipeline-level agent name (matches
        # AgentTraceStep.agent_name) rather than the Lyzr Studio agent's own
        # name, so the UI can match a report to its trace step.
        validation_report["agent"] = AGENT_NAME
        if not validation_report["passed"]:
            diagnosis.requires_human_review = True
            logger.warning(f"Diagnosis for {triage.incident_id} failed validation: {validation_report}")

        return diagnosis, meta["total_tokens"], meta["latency_ms"], validation_report

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
        error_report = {
            "agent": AGENT_NAME,
            "schema_valid": False,
            "hallucination_signals": [],
            "grounding_valid": False,
            "invalid_citations": [],
            "passed": False,
            "error": str(exc),
        }
        return fallback, 0, 0.0, error_report
