"""
backend/tests/test_circuit_breaker.py

Unit tests for the per-agent circuit breaker (backend/circuit_breaker.py)
that wraps every real LLM call in agents/lyzr_inference.py. Exercises the
full CLOSED -> OPEN -> HALF_OPEN -> CLOSED state machine directly, rather
than only checking the /circuit-breakers endpoint's default (never-failed)
state, which is all backend/tests/test_production_readiness.py covers.
"""
import time

import pytest

from backend.circuit_breaker import BREAKERS_BY_AGENT_NAME, CircuitBreaker, CircuitState, all_breakers


def _always_fails():
    raise RuntimeError("simulated LLM failure")


def _always_succeeds():
    return "ok"


def test_starts_closed():
    cb = CircuitBreaker("test-agent")
    assert cb.state == CircuitState.CLOSED


def test_call_returns_result_on_success():
    cb = CircuitBreaker("test-agent")
    assert cb.call(_always_succeeds) == "ok"
    assert cb.state == CircuitState.CLOSED


def test_opens_after_failure_threshold():
    cb = CircuitBreaker("test-agent", failure_threshold=3)
    for _ in range(3):
        cb.call(_always_fails, fallback="fallback-value")
    assert cb.state == CircuitState.OPEN


def test_open_circuit_returns_fallback_without_calling_func():
    cb = CircuitBreaker("test-agent", failure_threshold=1)
    cb.call(_always_fails, fallback="first-fallback")
    assert cb.state == CircuitState.OPEN

    calls = []

    def _tracked():
        calls.append(1)
        return "should not run"

    result = cb.call(_tracked, fallback="second-fallback")
    assert result == "second-fallback"
    assert calls == []  # the wrapped function was never invoked while OPEN


def test_open_circuit_without_fallback_returns_none():
    cb = CircuitBreaker("test-agent", failure_threshold=1)
    cb.call(_always_fails, fallback="x")
    assert cb.state == CircuitState.OPEN
    # No fallback this time -- the OPEN-circuit short-circuit always
    # returns `fallback` (default None) rather than raising, since the
    # wrapped function is never even attempted while OPEN. Contrast with
    # test_raises_when_func_fails_and_no_fallback, where the circuit is
    # CLOSED and the function actually runs and fails.
    assert cb.call(_always_fails) is None


def test_raises_when_func_fails_and_no_fallback():
    cb = CircuitBreaker("test-agent", failure_threshold=99)
    with pytest.raises(RuntimeError, match="simulated LLM failure"):
        cb.call(_always_fails)


def test_transitions_to_half_open_after_timeout():
    cb = CircuitBreaker("test-agent", failure_threshold=1, timeout_seconds=0)
    cb.call(_always_fails, fallback="x")
    assert cb._state == CircuitState.OPEN
    time.sleep(0.01)
    # Reading .state (not ._state) is what actually advances OPEN -> HALF_OPEN.
    assert cb.state == CircuitState.HALF_OPEN


def test_half_open_success_closes_circuit():
    cb = CircuitBreaker("test-agent", failure_threshold=1, timeout_seconds=0, success_threshold=1)
    cb.call(_always_fails, fallback="x")
    time.sleep(0.01)
    assert cb.state == CircuitState.HALF_OPEN
    result = cb.call(_always_succeeds)
    assert result == "ok"
    assert cb.state == CircuitState.CLOSED


def test_half_open_failure_reopens_circuit():
    cb = CircuitBreaker("test-agent", failure_threshold=1, timeout_seconds=0)
    cb.call(_always_fails, fallback="x")
    time.sleep(0.01)
    assert cb.state == CircuitState.HALF_OPEN
    cb.call(_always_fails, fallback="x")
    # Check the raw internal state, not the .state property: with
    # timeout_seconds=0, reading .state immediately re-evaluates the
    # OPEN -> HALF_OPEN transition condition (time.time() - opened_at >= 0
    # is instantly true), which would mask the fact that _on_failure()
    # really did re-open the circuit a moment ago.
    assert cb._state == CircuitState.OPEN


def test_success_resets_failure_count():
    cb = CircuitBreaker("test-agent", failure_threshold=3)
    cb.call(_always_fails, fallback="x")
    cb.call(_always_fails, fallback="x")
    assert cb._failure_count == 2
    cb.call(_always_succeeds)
    assert cb._failure_count == 0


def test_status_returns_expected_shape():
    cb = CircuitBreaker("status-agent", failure_threshold=5)
    status = cb.status()
    assert status == {
        "name": "status-agent",
        "state": "closed",
        "failure_count": 0,
        "failure_threshold": 5,
    }


def test_all_breakers_returns_four():
    breakers = all_breakers()
    assert len(breakers) == 4
    assert {b.name for b in breakers} == {
        "triage-agent",
        "diagnostic-agent",
        "remediation-agent",
        "postmortem-agent",
    }


def test_breakers_by_agent_name_keys_match_lyzr_agent_names():
    assert set(BREAKERS_BY_AGENT_NAME.keys()) == {
        "SRE-Triage-Agent",
        "SRE-Diagnostic-Agent",
        "SRE-Remediation-Agent",
        "SRE-PostMortem-Agent",
    }
