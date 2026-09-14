"""
agents/tests/test_tools.py

Unit tests for the explicit tool-calling contracts (agents/tools.py).
"""
from __future__ import annotations

import pytest

from agents.tools import (
    AlertCorrelationInput,
    KubectlSafeInput,
    LogQueryInput,
    LogQueryOutput,
    MetricsQueryInput,
    MetricsQueryOutput,
    call_tool,
    correlate_alerts,
    kubectl_safe,
    query_logs,
    query_metrics,
)


def test_query_logs_returns_log_query_output():
    result = query_logs(LogQueryInput(service="payment-service", namespace="production"))
    assert isinstance(result, LogQueryOutput)
    assert result.total_found == len(result.log_lines)
    assert result.query_time_ms >= 0


def test_query_logs_filters_by_keyword():
    result = query_logs(
        LogQueryInput(service="payment-service", namespace="production", keywords=["OOMKilled"])
    )
    assert isinstance(result, LogQueryOutput)
    assert all("oomkilled" in line.lower() for line in result.log_lines)


def test_query_metrics_returns_metrics_query_output():
    result = query_metrics(MetricsQueryInput(service="payment-service", metric_name="memory_usage_percent"))
    assert isinstance(result, MetricsQueryOutput)
    assert result.metric_name == "memory_usage_percent"
    assert isinstance(result.is_breaching, bool)


def test_kubectl_safe_blocks_delete_command():
    result = kubectl_safe(KubectlSafeInput(command="kubectl delete pod payment-service-abc123", dry_run=False))
    assert result.is_destructive is True
    assert result.hitl_required is True
    assert result.exit_code == 403
    assert "BLOCKED" in result.output


def test_kubectl_safe_allows_get_command():
    result = kubectl_safe(KubectlSafeInput(command="kubectl get pods -n production"))
    assert result.is_destructive is False
    assert result.hitl_required is False
    assert result.exit_code == 0


def test_correlate_alerts_groups_by_service():
    result = correlate_alerts(
        AlertCorrelationInput(alert_ids=["serviceA-1", "serviceA-2", "serviceB-1"])
    )
    groups_by_service = {g["service"]: g["alerts"] for g in result.correlated_groups}
    assert set(groups_by_service.keys()) == {"serviceA", "serviceB"}
    assert len(groups_by_service["serviceA"]) == 2
    assert len(groups_by_service["serviceB"]) == 1
    # root_alert_id should come from the largest group (serviceA).
    assert result.root_alert_id in groups_by_service["serviceA"]


def test_call_tool_with_unknown_tool_raises_value_error():
    with pytest.raises(ValueError):
        call_tool("nonexistent_tool", {})


def test_call_tool_dispatches_to_query_logs():
    result = call_tool("query_logs", {"service": "payment-service", "namespace": "production"})
    assert isinstance(result, dict)
    assert "log_lines" in result
    assert "total_found" in result
