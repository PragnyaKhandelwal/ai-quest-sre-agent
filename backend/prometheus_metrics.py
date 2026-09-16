"""
Prometheus Metrics for SRE Agent Backend
==========================================
Exposes production metrics in Prometheus format at GET /metrics/prometheus.
Scrape-able by Grafana, Datadog, CloudWatch agent, Victoria Metrics.

Metrics exposed:
  sre_incidents_total         -- total incidents by severity/scenario
  sre_agent_latency_seconds   -- agent call latency histogram
  sre_agent_tokens_total      -- tokens consumed per agent
  sre_hitl_decisions_total    -- HITL approve/reject counts
  sre_pipeline_errors_total   -- pipeline error count
  sre_active_incidents        -- currently active incidents gauge
  sre_hitl_queue_depth        -- actions awaiting HITL approval gauge
"""
from fastapi import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

# -- Counters -----------------------------------------------------------------
incidents_total = Counter("sre_incidents_total", "Total incidents processed", ["severity", "scenario"])

agent_tokens_total = Counter("sre_agent_tokens_total", "Total tokens consumed", ["agent_name", "token_type"])

hitl_decisions_total = Counter("sre_hitl_decisions_total", "HITL approve/reject decisions", ["decision", "risk_level"])

pipeline_errors_total = Counter("sre_pipeline_errors_total", "Pipeline errors", ["agent_name", "error_type"])

# -- Histograms -----------------------------------------------------------------
agent_latency = Histogram(
    "sre_agent_latency_seconds",
    "Agent inference latency",
    ["agent_name"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0],
)

pipeline_duration = Histogram(
    "sre_pipeline_duration_seconds",
    "Full pipeline duration (triage to RCA)",
    ["severity"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

# -- Gauges -----------------------------------------------------------------
active_incidents = Gauge("sre_active_incidents", "Currently active (unresolved) incidents")

hitl_queue_depth = Gauge("sre_hitl_queue_depth", "Number of actions awaiting HITL approval")


def get_metrics_response() -> Response:
    """Return Prometheus metrics in text format."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
