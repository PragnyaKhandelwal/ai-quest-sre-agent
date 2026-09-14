"""
agents/tests/test_pipeline.py

End-to-end tests of the governed 4-agent pipeline (agents/pipeline.py) run
directly against a minimal in-test PipelineHooks implementation -- not via
the FastAPI backend -- since agents/pipeline.py is deliberately independent
of backend/ (see its module docstring). This keeps these tests fast and
isolated to the orchestration layer itself.

Note: tests here are plain synchronous functions that drive the pipeline's
async run_pipeline() via asyncio.run(), rather than using
`@pytest.mark.asyncio` / async test functions -- that marker requires the
pytest-asyncio plugin, which isn't part of this project's dependencies, and
asyncio.run() needs nothing extra.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from agents import triage_agent
from agents.pipeline import run_pipeline
from agents.schemas import HITLStatus, IncidentStatus, RCAReport
from backend.mock_generator import get_scenario


@pytest.fixture(autouse=True)
def _reset_dedup_cache():
    """Each test gets a clean dedup cache so fingerprints from one test
    (or from agents/tests/test_triage_agent.py, collected in the same
    pytest session) never leak into another."""
    triage_agent._SEEN_FINGERPRINTS.clear()
    yield
    triage_agent._SEEN_FINGERPRINTS.clear()


class _CollectingHooks:
    """Minimal PipelineHooks implementation that just records everything,
    auto-approving any HITL request immediately (simulating an always-on
    human reviewer) so a full pipeline run always completes."""

    def __init__(self):
        self.statuses = []
        self.trace = []
        self.aims_events = []
        self.results = {}
        self.hallucination_reports = []

    def on_status(self, incident_id, status):
        self.statuses.append(status)

    def on_trace(self, step):
        self.trace.append(step)

    def log_aims(self, event):
        self.aims_events.append(event)

    def set_result(self, incident_id, key, value):
        self.results[key] = value

    def add_hallucination_report(self, incident_id, report):
        self.hallucination_reports.append(report)

    async def await_hitl(self, incident_id, requests):
        resolved = []
        for req in requests:
            req.status = HITLStatus.APPROVED
            req.decided_by = "test-harness"
            req.decided_at = time.time()
            resolved.append(req)
        return resolved


def _run_scenario(scenario_number: int):
    async def _go():
        _, alerts, log_corpus = get_scenario(scenario_number)
        hooks = _CollectingHooks()
        rca = await run_pipeline(f"test_inc_scenario_{scenario_number}", alerts, log_corpus, hooks)
        return hooks, rca

    return asyncio.run(_go())


def test_full_pipeline_runs_for_scenario_1_memory_leak():
    hooks, rca = _run_scenario(1)
    assert hooks.statuses[-1] == IncidentStatus.RESOLVED
    assert rca is not None
    assert hooks.results["triage"].severity.value == "P1"
    # Scenario 1's remediation is entirely SAFE -- no HITL gate expected.
    assert IncidentStatus.AWAITING_HITL not in hooks.statuses


def test_full_pipeline_runs_for_scenario_3_db_deadlock():
    hooks, rca = _run_scenario(3)
    assert hooks.statuses[-1] == IncidentStatus.RESOLVED
    assert rca is not None
    assert hooks.results["triage"].severity.value == "P1"


def test_scenario_3_sets_status_to_awaiting_hitl():
    # Scenario 3 (DB deadlock) always proposes a destructive
    # pg_terminate_backend action, which must be gated behind HITL before
    # the pipeline can proceed to post-mortem.
    hooks, rca = _run_scenario(3)
    assert IncidentStatus.AWAITING_HITL in hooks.statuses
    runbook = hooks.results["runbook"]
    assert any(a.is_destructive for a in runbook.actions)


def test_hitl_approve_changes_status_to_resolved():
    # _CollectingHooks.await_hitl auto-approves every destructive request,
    # so a scenario that hits the HITL gate should still reach RESOLVED.
    hooks, rca = _run_scenario(3)
    assert IncidentStatus.AWAITING_HITL in hooks.statuses
    assert hooks.statuses[-1] == IncidentStatus.RESOLVED
    destructive_actions = [a for a in hooks.results["runbook"].actions if a.is_destructive]
    assert len(destructive_actions) > 0
    assert all(a.executed for a in destructive_actions)


def test_pipeline_produces_valid_rca_json():
    hooks, rca = _run_scenario(2)
    assert isinstance(rca, RCAReport)
    # Must round-trip through real JSON serialization cleanly.
    payload = json.loads(rca.model_dump_json())
    for key in ("incident_id", "title", "severity", "timeline", "root_cause", "affected_services"):
        assert key in payload
    assert payload["severity"] == "P2"
