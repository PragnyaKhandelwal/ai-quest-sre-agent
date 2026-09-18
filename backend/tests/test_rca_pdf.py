"""
backend/tests/test_rca_pdf.py

Direct unit tests for RCA PDF rendering (backend/rca_pdf.py). Previously
only exercised indirectly via GET /incidents/{id}/rca/pdf in
backend/tests/test_api.py, after a full simulated pipeline run -- these
test the renderer itself against hand-built RCAReport fixtures, including
the empty contributing_factors/remediation_taken branches those simulated
scenarios don't always hit.
"""
from agents.schemas import RCAReport, Severity, TimelineEvent
from backend.rca_pdf import render_rca_pdf


def _full_rca() -> RCAReport:
    return RCAReport(
        incident_id="inc_test001",
        title="payment-service OOMKilled -- connection pool leak",
        severity=Severity.P1,
        timeline=[
            TimelineEvent(timestamp=1700000000.0, event="Triaged as P1", actor="TriageAndDedupAgent"),
            TimelineEvent(timestamp=1700000010.0, event="Root cause identified", actor="RootCauseDiagnosticianAgent"),
        ],
        root_cause="Connection pool leak causing steady heap growth until OOMKilled.",
        affected_services=["payment-service", "postgres-primary"],
        contributing_factors=["No connection pool timeout configured", "Missing memory alerting"],
        remediation_taken=["Rolled back to previous deployment", "Increased pool timeout"],
        prevention_recommendations=["Add connection pool leak detection", "Set memory alerts at 80%"],
        lessons_learned="Blameless: the leak was introduced by a library upgrade with no regression test coverage.",
    )


def test_render_rca_pdf_returns_valid_pdf_bytes():
    pdf_bytes = render_rca_pdf(_full_rca())
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 0
    assert pdf_bytes.startswith(b"%PDF")


def test_render_rca_pdf_handles_empty_contributing_factors_and_remediation():
    rca = _full_rca()
    rca.contributing_factors = []
    rca.remediation_taken = []
    pdf_bytes = render_rca_pdf(rca)
    assert pdf_bytes.startswith(b"%PDF")


def test_render_rca_pdf_handles_single_service_and_timeline_event():
    rca = _full_rca()
    rca.affected_services = ["payment-service"]
    rca.timeline = [TimelineEvent(timestamp=1700000000.0, event="Single event", actor="system")]
    pdf_bytes = render_rca_pdf(rca)
    assert pdf_bytes.startswith(b"%PDF")


def test_render_rca_pdf_handles_long_text_fields():
    rca = _full_rca()
    rca.root_cause = "A very long root cause description. " * 50
    rca.lessons_learned = "A very long lessons-learned narrative. " * 50
    pdf_bytes = render_rca_pdf(rca)
    assert pdf_bytes.startswith(b"%PDF")
