"""
Circuit Breaker Pattern for Agent Pipeline
==========================================
Prevents cascade failures when the underlying LLM API is unavailable.
States: CLOSED (normal) -> OPEN (failing) -> HALF_OPEN (testing)

If an agent fails `failure_threshold` times, the circuit OPENS and
immediately returns the caller's fallback (no further LLM calls) for
`timeout_seconds`. After that window it goes HALF_OPEN and allows one
call through; success closes the circuit again, failure re-opens it.
"""
import logging
import time
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"  # Normal -- calls go through
    OPEN = "open"  # Failing -- calls blocked
    HALF_OPEN = "half_open"  # Testing -- one call allowed


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        timeout_seconds: int = 30,
        success_threshold: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.success_threshold = success_threshold

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float = 0
        self._opened_at: float = 0

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN and time.time() - self._opened_at >= self.timeout_seconds:
            self._state = CircuitState.HALF_OPEN
            logger.info(f"Circuit {self.name}: OPEN -> HALF_OPEN")
        return self._state

    def call(self, func: Callable, *args, fallback=None, **kwargs) -> Any:
        """Execute func through the circuit breaker."""
        state = self.state

        if state == CircuitState.OPEN:
            remaining = self.timeout_seconds - (time.time() - self._opened_at)
            logger.warning(f"Circuit {self.name} OPEN -- returning fallback. Retry in {remaining:.0f}s")
            return fallback

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure(e)
            if fallback is not None:
                return fallback
            raise

    def _on_success(self):
        self._failure_count = 0
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.success_threshold:
                self._state = CircuitState.CLOSED
                self._success_count = 0
                logger.info(f"Circuit {self.name}: HALF_OPEN -> CLOSED")

    def _on_failure(self, error: Exception):
        self._failure_count += 1
        self._last_failure_time = time.time()
        logger.warning(f"Circuit {self.name}: failure {self._failure_count}/{self.failure_threshold}: {error}")
        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.time()
            logger.error(f"Circuit {self.name}: CLOSED -> OPEN (too many failures)")

    def status(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "failure_threshold": self.failure_threshold,
        }


# One circuit breaker per agent, keyed by the Lyzr Agent Studio SREAgent's
# own `.name` (agents/lyzr_agents.py) -- e.g. "SRE-Triage-Agent" -- which
# is what agents/lyzr_inference.py's run_inference() has on hand.
triage_breaker = CircuitBreaker("triage-agent")
diagnostic_breaker = CircuitBreaker("diagnostic-agent")
remediation_breaker = CircuitBreaker("remediation-agent")
postmortem_breaker = CircuitBreaker("postmortem-agent")

BREAKERS_BY_AGENT_NAME = {
    "SRE-Triage-Agent": triage_breaker,
    "SRE-Diagnostic-Agent": diagnostic_breaker,
    "SRE-Remediation-Agent": remediation_breaker,
    "SRE-PostMortem-Agent": postmortem_breaker,
}


def all_breakers() -> list[CircuitBreaker]:
    return [triage_breaker, diagnostic_breaker, remediation_breaker, postmortem_breaker]
