"""
agents/metrics_tracker.py

Token consumption and latency tracking for all agent calls.
Provides real-time metrics visible in the dashboard (Token Optimization
and Latency Optimization evaluation checkpoints).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class AgentCallMetrics:
    agent_name: str
    incident_id: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    latency_ms: float = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0
    model: str = "gpt-4o-mini"


# GPT-4o-mini pricing (per 1K tokens)
TOKEN_COST = {"input": 0.00015, "output": 0.00060}


def estimate_tokens(text: str) -> int:
    """Word-count based token estimate (~1.3 tokens/word), used uniformly
    for both local-simulation output and real Lyzr responses whose SDK
    response object doesn't expose a structured usage/token count."""
    if not text:
        return 0
    return max(1, int(len(text.split()) * 1.3))


class MetricsTracker:
    def __init__(self):
        self.calls: List[AgentCallMetrics] = []
        self.session_totals = {
            "total_tokens": 0, "total_cost_usd": 0.0,
            "total_calls": 0, "avg_latency_ms": 0.0,
        }

    def start_call(self, agent_name: str, incident_id: str) -> AgentCallMetrics:
        m = AgentCallMetrics(
            agent_name=agent_name,
            incident_id=incident_id,
            started_at=datetime.now(timezone.utc),
        )
        self.calls.append(m)
        return m

    def end_call(self, metrics: AgentCallMetrics,
                 input_tokens: int, output_tokens: int) -> AgentCallMetrics:
        metrics.ended_at = datetime.now(timezone.utc)
        metrics.latency_ms = (metrics.ended_at - metrics.started_at).total_seconds() * 1000
        metrics.input_tokens = input_tokens
        metrics.output_tokens = output_tokens
        metrics.total_tokens = input_tokens + output_tokens
        metrics.cost_usd = (
            (input_tokens / 1000) * TOKEN_COST["input"]
            + (output_tokens / 1000) * TOKEN_COST["output"]
        )
        # Update session totals
        self.session_totals["total_tokens"] += metrics.total_tokens
        self.session_totals["total_cost_usd"] += metrics.cost_usd
        self.session_totals["total_calls"] += 1
        latencies = [c.latency_ms for c in self.calls if c.latency_ms > 0]
        self.session_totals["avg_latency_ms"] = sum(latencies) / len(latencies) if latencies else 0

        logger.info(
            f"[METRICS] {metrics.agent_name} | {metrics.latency_ms:.0f}ms | "
            f"{metrics.total_tokens} tokens | ${metrics.cost_usd:.5f}"
        )
        return metrics

    def get_incident_metrics(self, incident_id: str) -> List[dict]:
        return [
            {
                "agent": m.agent_name,
                "latency_ms": round(m.latency_ms, 1),
                "input_tokens": m.input_tokens,
                "output_tokens": m.output_tokens,
                "total_tokens": m.total_tokens,
                "cost_usd": round(m.cost_usd, 6),
            }
            for m in self.calls if m.incident_id == incident_id
        ]

    def get_session_summary(self) -> dict:
        return {
            **self.session_totals,
            "cost_formatted": f"${self.session_totals['total_cost_usd']:.4f}",
        }


# Global tracker instance
tracker = MetricsTracker()
