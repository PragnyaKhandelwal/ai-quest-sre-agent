"""
backend/tests/test_aims_logger.py

Unit tests for the Lyzr AIMS audit-trail wrapper (backend/aims_logger.py),
targeting branches other tests only exercise indirectly through full
pipeline runs: the independent destructive-action safety recheck, the
remote-Lyzr-AIMS success/failure paths, and the local-file last-resort
fallback.
"""
from unittest.mock import MagicMock, patch

import backend.aims_logger as aims_logger
from agents.schemas import AIMSEvent


def test_safety_recheck_forces_blocked_on_governance_violation():
    event = AIMSEvent(
        incident_id="inc_test001",
        agent_name="RemediationPlannerAgent",
        action="auto-executed",
        input_summary="DROP TABLE orders",
        output_summary='{"executed": true}',
        blocked=False,
    )
    checked = aims_logger._independent_safety_recheck(event)
    assert checked.blocked is True
    assert "safety_anomaly" in checked.metadata


def test_safety_recheck_leaves_correctly_blocked_event_alone():
    event = AIMSEvent(
        incident_id="inc_test001",
        agent_name="RemediationPlannerAgent",
        action="auto-executed",
        input_summary="DROP TABLE orders",
        output_summary='{"executed": true}',
        blocked=True,
    )
    checked = aims_logger._independent_safety_recheck(event)
    assert checked.blocked is True
    assert "safety_anomaly" not in checked.metadata


def test_safety_recheck_ignores_safe_non_destructive_event():
    event = AIMSEvent(
        incident_id="inc_test001",
        agent_name="RemediationPlannerAgent",
        action="auto-executed",
        input_summary="rollout restart deployment/payment-service",
        output_summary='{"executed": true}',
        blocked=False,
    )
    checked = aims_logger._independent_safety_recheck(event)
    assert checked.blocked is False


def test_persist_sends_to_remote_lyzr_when_enabled():
    event = AIMSEvent(incident_id="inc_x", agent_name="TestAgent", action="test")
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None

    with patch.object(aims_logger.config, "LYZR_ENABLED", True), patch.object(
        aims_logger._session, "post", return_value=mock_response
    ) as mock_post:
        aims_logger._persist(event)
        mock_post.assert_called_once()


def test_persist_falls_back_to_local_file_when_remote_fails(tmp_path):
    event = AIMSEvent(incident_id="inc_y", agent_name="TestAgent", action="test")
    fallback_path = tmp_path / "aims_fallback.log.jsonl"

    with patch.object(aims_logger.config, "LYZR_ENABLED", True), patch.object(
        aims_logger._session, "post", side_effect=Exception("network error")
    ), patch.object(aims_logger, "_LOCAL_LOG_PATH", fallback_path):
        aims_logger._persist(event)

    assert fallback_path.exists()
    content = fallback_path.read_text(encoding="utf-8")
    assert "inc_y" in content


def test_persist_swallows_local_file_write_errors(tmp_path):
    event = AIMSEvent(incident_id="inc_z", agent_name="TestAgent", action="test")
    unwritable_path = tmp_path / "does" / "not" / "exist" / "aims.log.jsonl"

    with patch.object(aims_logger.config, "LYZR_ENABLED", False), patch.object(
        aims_logger, "_LOCAL_LOG_PATH", unwritable_path
    ):
        aims_logger._persist(event)  # must not raise


def test_recent_events_filters_by_incident_id():
    aims_logger._IN_MEMORY_EVENTS.clear()
    aims_logger.log_event(AIMSEvent(incident_id="inc_a", agent_name="A", action="x"))
    aims_logger.log_event(AIMSEvent(incident_id="inc_b", agent_name="B", action="y"))

    events_a = aims_logger.recent_events(incident_id="inc_a")
    assert len(events_a) == 1
    assert events_a[0]["incident_id"] == "inc_a"


def test_recent_events_respects_limit():
    aims_logger._IN_MEMORY_EVENTS.clear()
    for i in range(5):
        aims_logger.log_event(AIMSEvent(incident_id=f"inc_{i}", agent_name="A", action="x"))
    assert len(aims_logger.recent_events(limit=2)) == 2


def test_total_tokens_used_sums_all_events():
    aims_logger._IN_MEMORY_EVENTS.clear()
    aims_logger.log_event(AIMSEvent(incident_id="inc_a", agent_name="A", action="x", tokens_used=100))
    aims_logger.log_event(AIMSEvent(incident_id="inc_b", agent_name="B", action="y", tokens_used=50))
    assert aims_logger.total_tokens_used() == 150
