"""
agents/tests/test_metrics_tracker.py

Unit tests for token/latency/cost tracking (agents/metrics_tracker.py) --
the Token Optimization and Latency Optimization evaluation checkpoints.
Uses a fresh MetricsTracker() per test rather than the shared global
`tracker`, so tests don't leak session totals into each other.
"""
from __future__ import annotations

import time

from agents.metrics_tracker import MetricsTracker, estimate_tokens


def test_estimate_tokens_empty_string_is_zero():
    assert estimate_tokens("") == 0


def test_estimate_tokens_scales_with_word_count():
    short = estimate_tokens("one two three")
    long = estimate_tokens("one two three four five six seven eight nine ten")
    assert long > short
    assert short >= 1


def test_start_call_creates_pending_metrics_entry():
    mt = MetricsTracker()
    m = mt.start_call("TriageAgent", "inc-1")
    assert m.agent_name == "TriageAgent"
    assert m.incident_id == "inc-1"
    assert m.ended_at is None
    assert m in mt.calls


def test_end_call_computes_latency_tokens_and_cost():
    mt = MetricsTracker()
    m = mt.start_call("DiagnosticianAgent", "inc-2")
    time.sleep(0.001)  # ensures a non-zero, deterministic latency measurement
    mt.end_call(m, input_tokens=100, output_tokens=50)

    assert m.ended_at is not None
    assert m.latency_ms > 0
    assert m.input_tokens == 100
    assert m.output_tokens == 50
    assert m.total_tokens == 150
    assert m.cost_usd > 0

    summary = mt.get_session_summary()
    assert summary["total_calls"] == 1
    assert summary["total_tokens"] == 150
    assert summary["avg_latency_ms"] > 0
    assert summary["cost_formatted"].startswith("$")


def test_get_incident_metrics_filters_by_incident_id():
    mt = MetricsTracker()
    m1 = mt.start_call("TriageAgent", "inc-a")
    mt.end_call(m1, input_tokens=10, output_tokens=10)
    m2 = mt.start_call("RemediationAgent", "inc-b")
    mt.end_call(m2, input_tokens=20, output_tokens=20)

    inc_a_metrics = mt.get_incident_metrics("inc-a")
    assert len(inc_a_metrics) == 1
    assert inc_a_metrics[0]["agent"] == "TriageAgent"
    assert inc_a_metrics[0]["total_tokens"] == 20

    assert mt.get_incident_metrics("inc-nonexistent") == []


def test_session_totals_accumulate_across_multiple_calls():
    mt = MetricsTracker()
    for i in range(3):
        m = mt.start_call(f"Agent{i}", "inc-multi")
        mt.end_call(m, input_tokens=100, output_tokens=100)

    summary = mt.get_session_summary()
    assert summary["total_calls"] == 3
    assert summary["total_tokens"] == 600
    assert summary["total_cost_usd"] > 0
