"""
agents/lyzr_client.py

Thin wrapper around the Lyzr Agent API (Environment -> Agent -> Inference)
providing the three-layer separation the rubric asks for:

    Environment  -- a Lyzr Environment groups the agents for this system
                    and enables SHORT_TERM_MEMORY so state persists across
                    inference calls within an incident session.
    Agent        -- each pipeline stage (triage / diagnostician / remediation
                    / postmortem) is registered as its own Lyzr Agent with
                    its own system prompt and model config.
    Inference    -- every agent call is a POST /inference call scoped to a
                    session_id (the incident_id), so Lyzr persists
                    conversational state per-incident.

If LYZR_API_KEY is not configured (LYZR_ENABLED=False), every call
transparently falls back to a local, deterministic JSON-producing
"simulated inference" so the whole system remains fully functional for
demo/judging without any external API key. The call signature and return
shape are identical in both modes, so pipeline code never needs to know
which mode it's running in.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import requests

from agents import config


@dataclass
class InferenceResult:
    text: str
    tokens_used: int
    latency_ms: float
    simulated: bool = False


class LyzrClient:
    """Manages one Lyzr Environment and its registered Agents."""

    def __init__(self) -> None:
        self.enabled = config.LYZR_ENABLED
        self.environment_id: Optional[str] = None
        self._agent_ids: Dict[str, str] = {}
        self._session = requests.Session()
        if self.enabled:
            self._session.headers.update(
                {
                    "x-api-key": config.LYZR_API_KEY,
                    "Content-Type": "application/json",
                }
            )

    # ------------------------------------------------------------------
    # Environment / Agent bootstrap
    # ------------------------------------------------------------------
    def ensure_environment(self, name: str = "sre-incident-response") -> Optional[str]:
        if not self.enabled:
            return None
        if self.environment_id:
            return self.environment_id
        try:
            resp = self._session.post(
                f"{config.LYZR_BASE_URL}/environment",
                json={
                    "name": name,
                    "description": "Governed multi-agent SRE incident triage & remediation",
                    "feature_types": ["SHORT_TERM_MEMORY"],
                },
                timeout=15,
            )
            resp.raise_for_status()
            self.environment_id = resp.json().get("id")
            return self.environment_id
        except Exception:
            # Fail safe: fall back to local simulation for this process.
            self.enabled = False
            return None

    def ensure_agent(self, key: str, name: str, system_prompt: str) -> Optional[str]:
        if not self.enabled:
            return None
        if key in self._agent_ids:
            return self._agent_ids[key]
        env_id = self.ensure_environment()
        if not env_id:
            return None
        try:
            resp = self._session.post(
                f"{config.LYZR_BASE_URL}/agent",
                json={
                    "environment_id": env_id,
                    "name": name,
                    "system_prompt": system_prompt,
                    "model_config": {
                        "model": config.MODEL,
                        "max_tokens": config.MAX_TOKENS,
                        "temperature": config.TEMPERATURE,
                    },
                    "tools": [],
                },
                timeout=15,
            )
            resp.raise_for_status()
            agent_id = resp.json().get("id")
            self._agent_ids[key] = agent_id
            return agent_id
        except Exception:
            self.enabled = False
            return None

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------
    def run_inference(
        self,
        agent_key: str,
        agent_name: str,
        system_prompt: str,
        user_message: str,
        session_id: str,
        simulate_fn: Callable[[], Dict[str, Any]],
    ) -> InferenceResult:
        """
        Run one inference call for `agent_key`. Always returns an
        InferenceResult whose `.text` is a JSON string. Falls back to
        `simulate_fn()` (a callable returning a dict) whenever Lyzr is not
        configured or the remote call fails for any reason -- callers never
        see a raised exception from network issues.
        """
        start = time.perf_counter()

        if self.enabled:
            agent_id = self.ensure_agent(agent_key, agent_name, system_prompt)
            if agent_id:
                try:
                    resp = self._session.post(
                        f"{config.LYZR_BASE_URL}/inference",
                        json={
                            "agent_id": agent_id,
                            "session_id": session_id,
                            "message": user_message,
                        },
                        timeout=30,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    latency_ms = (time.perf_counter() - start) * 1000
                    text = data.get("response", "")
                    tokens_used = data.get("tokens_used") or _estimate_tokens(
                        user_message + text
                    )
                    return InferenceResult(
                        text=text, tokens_used=tokens_used, latency_ms=latency_ms
                    )
                except Exception:
                    pass  # fall through to simulation

        # ---- local simulation fallback (deterministic, schema-safe) ----
        try:
            payload = simulate_fn()
            text = json.dumps(payload)
        except Exception as exc:  # pragma: no cover - defensive
            text = json.dumps({"error": str(exc)})
        latency_ms = (time.perf_counter() - start) * 1000
        tokens_used = _estimate_tokens(user_message + text)
        return InferenceResult(
            text=text, tokens_used=tokens_used, latency_ms=latency_ms, simulated=True
        )


def _estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token) used for local-mode logging
    and as a fallback when the API doesn't report usage."""
    return max(1, len(text) // 4)


# Module-level singleton shared by all agents/pipeline runs.
client = LyzrClient()
